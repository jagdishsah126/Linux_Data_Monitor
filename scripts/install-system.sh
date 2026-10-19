#!/usr/bin/env bash
#
# Data Monitor — system-level setup.  MUST be run with sudo.
#
# It only does two things:
#   1. installs the two small packages (xfce4-genmon-plugin ~52 KB, nethogs ~34 KB);
#   2. grants the nethogs binary the packet-capture capability it needs.
#
# There is NO root daemon, NO firewall rule and NO network configuration change.
#
# OFFLINE FIRST: it installs from the local package cache and, if something is
# missing, STOPS and asks.  It never starts a download on its own — this project
# is used on metered connections.  Pass --allow-download to approve it up front.
#
# ABOUT THE CAPABILITY — what is really being granted:
#   nethogs captures packets, which normally requires root.  These two *file*
#   capabilities give that power to this one binary only:
#     cap_net_raw    — open raw sockets (i.e. read the packets)
#     cap_net_admin  — put the interface into promiscuous mode
#   cap_net_admin is not a narrow, read-only permission: it is the same
#   capability that allows reconfiguring interfaces and routes.  It is scoped to
#   this executable, but file capabilities apply to *every* user who runs that
#   executable, and they are silently lost whenever the package manager replaces
#   the file (an apt upgrade, for instance).  So: re-run this script after
#   upgrading nethogs.  The collector notices if per-app data stops and logs a
#   hint to the journal; the exact total is unaffected either way.
#
# Remove the capability again with:  sudo setcap -r /usr/sbin/nethogs
# (scripts/uninstall.sh does it for you, and prints the exact path if not root.)
#
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash scripts/install-system.sh" >&2
    exit 1
fi

ALLOW_DOWNLOAD=0
while [[ $# -gt 0 ]]; do
    case "$1" in
        --allow-download) ALLOW_DOWNLOAD=1 ;;
        -h|--help) sed -n '3,10p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

echo "== Data Monitor: system setup =="

# ---------------------------------------------------------------------------
# 1. Packages — offline first, and never download without being told to
# ---------------------------------------------------------------------------
PKGS=(xfce4-genmon-plugin nethogs)

echo "-- trying the local package cache first (no download) --"
if apt-get --no-download install -y --no-install-recommends "${PKGS[@]}"; then
    echo "Installed from the local cache; nothing was downloaded."
elif [[ "$ALLOW_DOWNLOAD" -eq 1 ]]; then
    echo "-- --allow-download given: fetching the two packages (~86 KB) --"
    apt-get install -y --no-install-recommends "${PKGS[@]}"
elif [[ -t 0 ]]; then
    echo "!! Not in the local cache: ${PKGS[*]}"
    read -r -p "Download them now (~86 KB)? [y/N] " reply || reply=""
    if [[ "${reply}" =~ ^[Yy] ]]; then
        apt-get install -y --no-install-recommends "${PKGS[@]}"
    else
        echo "Nothing was downloaded. Continuing without them."
    fi
else
    echo "!! Not in the local cache and no terminal to ask on: ${PKGS[*]}" >&2
    echo "   Nothing was downloaded. Approve it explicitly with:" >&2
    echo "       sudo bash $0 --allow-download" >&2
    exit 1
fi

# ---------------------------------------------------------------------------
# 2. Packet-capture capability for nethogs (reversible, scoped to one binary)
#
#    NOTE: on Ubuntu/Debian nethogs lives in /usr/sbin, not /usr/bin, so we
#    locate it rather than assuming a path.
# ---------------------------------------------------------------------------
NETHOGS_BIN=""
for candidate in "$(command -v nethogs 2>/dev/null || true)" \
                 /usr/sbin/nethogs /usr/bin/nethogs /usr/local/sbin/nethogs; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        NETHOGS_BIN="$candidate"
        break
    fi
done

if [[ -n "$NETHOGS_BIN" ]]; then
    echo "-- granting packet-capture capability to $NETHOGS_BIN --"
    setcap cap_net_raw,cap_net_admin+eip "$NETHOGS_BIN"
    getcap "$NETHOGS_BIN"
else
    echo "!! nethogs binary not found; capability not set" >&2
    echo "   (the exact totals still work; per-app data will be unavailable)" >&2
fi

echo
echo "Done. Next: run the user-level setup (no sudo):"
echo "    bash scripts/install-user.sh"

#!/usr/bin/env bash
#
# Data Monitor — system-level setup.  MUST be run with sudo.
#
# It only does two things:
#   1. installs the two small, approved packages (52 KB + 34 KB);
#   2. grants the nethogs binary the packet-capture capability.
#
# There is NO root daemon, NO firewall rule and NO network configuration
# change.  The capability can be removed again with:  sudo setcap -r /usr/sbin/nethogs
#
set -euo pipefail

if [[ "${EUID}" -ne 0 ]]; then
    echo "Please run with sudo:  sudo bash scripts/install-system.sh" >&2
    exit 1
fi

echo "== Data Monitor: system setup =="

# ---------------------------------------------------------------------------
# 1. Packages (offline first, exactly as our data rules require)
# ---------------------------------------------------------------------------
PKGS=(xfce4-genmon-plugin nethogs)

echo "-- trying local package cache first (no download) --"
if apt-get --no-download install -y --no-install-recommends "${PKGS[@]}"; then
    echo "Installed from local cache."
else
    echo "-- not cached; downloading the approved packages (~86 KB total) --"
    apt-get install -y --no-install-recommends "${PKGS[@]}"
fi

# ---------------------------------------------------------------------------
# 2. Packet-capture capability for nethogs (reversible, scoped to one binary)
#
#    NOTE: on Ubuntu/Debian nethogs is installed in /usr/sbin, not /usr/bin,
#    so we locate it rather than assuming a path.
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
fi

echo
echo "Done. Next: run the user-level setup (no sudo):"
echo "    bash scripts/install-user.sh"

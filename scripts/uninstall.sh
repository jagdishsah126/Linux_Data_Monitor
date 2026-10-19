#!/usr/bin/env bash
#
# Data Monitor — remove everything this project installed.
#
# Safe to run repeatedly.  Add --purge to also delete recorded history.
#
# Run it with sudo to also take the packet-capture capability off nethogs;
# without sudo it prints the exact command instead of pretending to do it.
#
set -euo pipefail

PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "== Data Monitor: uninstall =="

# stop + remove the user service
systemctl --user disable --now data-monitor-collector.service 2>/dev/null || true
rm -f "$UNITDIR/data-monitor-collector.service"
systemctl --user daemon-reload 2>/dev/null || true

# remove launchers
for tool in data-monitor dm-panel dm-collector dm-popup; do
    rm -f "$HOME/.local/bin/$tool"
done

# remove the panel item, if present
# (test -f, not -x: the script is always invoked through bash, and the execute
#  bit may not survive a checkout on some filesystems)
if [[ -f "$(dirname "$0")/setup-panel.sh" ]]; then
    bash "$(dirname "$0")/setup-panel.sh" --remove || true
fi

if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$HOME/.local/share/data-monitor"
    echo "History deleted."
else
    echo "History kept at ~/.local/share/data-monitor (use --purge to delete)."
fi

# ---------------------------------------------------------------------------
# the privileged change: find nethogs the same way the installer does, so the
# path is right even when nethogs is not in /usr/sbin
# ---------------------------------------------------------------------------
NETHOGS_BIN=""
for candidate in "$(command -v nethogs 2>/dev/null || true)" \
                 /usr/sbin/nethogs /usr/bin/nethogs /usr/local/sbin/nethogs; do
    if [[ -n "$candidate" && -x "$candidate" ]]; then
        NETHOGS_BIN="$candidate"
        break
    fi
done

if [[ -z "$NETHOGS_BIN" ]]; then
    echo "Note: nethogs not found; no capability to remove."
elif [[ "${EUID}" -eq 0 ]]; then
    setcap -r "$NETHOGS_BIN" 2>/dev/null || true
    echo "Removed the packet-capture capability from $NETHOGS_BIN."
else
    echo "Note: nethogs still carries the packet-capture capability. Remove it with:"
    echo "    sudo setcap -r $NETHOGS_BIN"
fi

echo "Note: packages can be removed with:  sudo apt remove nethogs xfce4-genmon-plugin"
echo "Done."

#!/usr/bin/env bash
#
# Data Monitor — remove everything this project installed.
#
# Safe to run repeatedly.  Add --purge to also delete recorded history.
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
if [[ -x "$(dirname "$0")/setup-panel.sh" ]]; then
    bash "$(dirname "$0")/setup-panel.sh" --remove || true
fi

if [[ "$PURGE" -eq 1 ]]; then
    rm -rf "$HOME/.local/share/data-monitor"
    echo "History deleted."
else
    echo "History kept at ~/.local/share/data-monitor (use --purge to delete)."
fi

echo "Note: to remove the packet-capture capability:  sudo setcap -r /usr/sbin/nethogs"
echo "Note: packages can be removed with:            sudo apt remove nethogs xfce4-genmon-plugin"
echo "Done."

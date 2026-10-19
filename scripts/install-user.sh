#!/usr/bin/env bash
#
# Data Monitor — user-level setup.  NO sudo needed.
#
# Sets up: the command-line tools in ~/.local/bin, a *user* systemd service
# that records totals (and per-app usage when nethogs is available), and the
# XFCE panel item (via scripts/setup-panel.sh).
#
set -euo pipefail

PROJECT="$(cd "$(dirname "$0")/.." && pwd)"
PYTHON="$(command -v python3)"
BINDIR="$HOME/.local/bin"
UNITDIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "== Data Monitor: user setup =="
echo "Project: $PROJECT"

mkdir -p "$BINDIR" "$UNITDIR"

# --- command-line tools -----------------------------------------------------
for tool in data-monitor dm-panel dm-collector dm-popup; do
    ln -sf "$PROJECT/bin/$tool" "$BINDIR/$tool"
    echo "linked $BINDIR/$tool"
done

# --- user service -----------------------------------------------------------
sed -e "s|@PROJECT@|$PROJECT|g" \
    -e "s|@PYTHON@|$PYTHON|g" \
    "$PROJECT/systemd/data-monitor-collector.service" \
    > "$UNITDIR/data-monitor-collector.service"

systemctl --user daemon-reload
systemctl --user enable --now data-monitor-collector.service

sleep 1
echo
echo "Collector status: $(systemctl --user is-active data-monitor-collector.service)"

# --- panel item (best effort; only when a graphical session is available) ---
if [[ -n "${DISPLAY:-}" || -n "${WAYLAND_DISPLAY:-}" ]]; then
    echo
    bash "$PROJECT/scripts/setup-panel.sh" || echo "!! panel item could not be set up automatically"
fi

echo
echo "Try it:   $BINDIR/data-monitor today"
echo "Per-app:  $BINDIR/data-monitor apps"

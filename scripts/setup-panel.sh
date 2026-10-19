#!/usr/bin/env bash
#
# Data Monitor — add (or remove) the XFCE panel item.  NO sudo needed.
#
# The panel item is an "xfce4-genmon-plugin" widget whose command is
#     ~/.local/bin/dm-panel --markup
#
# IMPORTANT: xfce4-genmon-plugin 4.1.x does NOT read xfconf.  It reads a small
# legacy config file next to the panel config:
#     ~/.config/xfce4/panel/genmon-<plugin-id>.rc
# with plain "Key=Value" lines (no [group] header).  That is what this script
# writes.  (Verified against the 4.1.1 sources and a live panel.)
#
# Usage:
#   bash scripts/setup-panel.sh            # install / repair the panel item
#   bash scripts/setup-panel.sh --remove   # remove the panel item
#
set -euo pipefail

CHANNEL="xfce4-panel"
PANEL="panel-2"
IDS_PATH="/panels/${PANEL}/plugin-ids"
RC_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/xfce4/panel"
LAUNCHER="$HOME/.local/bin/dm-panel"

xq()  { xfconf-query -c "$CHANNEL" "$@"; }
ids() { xq -p "$IDS_PATH" 2>/dev/null | grep -E '^[0-9]+$' || true; }
component() { xq -p "/plugins/plugin-$1" 2>/dev/null || true; }

genmon_id() {
    local id
    for id in $(ids); do
        if [[ "$(component "$id")" == "genmon" ]]; then echo "$id"; return 0; fi
    done
    return 1
}

write_rc() {
    local id="$1"
    mkdir -p "$RC_DIR"
    cat > "$RC_DIR/genmon-${id}.rc" <<EOF
Command=${LAUNCHER} --markup
UseLabel=0
Text=
UpdatePeriod=30000
EOF
}

set_ids() {                      # replace the plugin-ids array with "$@"
    local args=() v
    for v in "$@"; do args+=(-t int -s "$v"); done
    xq -p "$IDS_PATH" "${args[@]}"
}

reload_panel() { xfce4-panel -r >/dev/null 2>&1 || true; }

# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------
if [[ "${1:-}" == "--remove" ]]; then
    if id="$(genmon_id)"; then
        set_ids $(ids | grep -vx "$id")
        xq -p "/plugins/plugin-${id}" -r 2>/dev/null || true
        reload_panel
        # NOTE: the panel rewrites plugin .rc files while shutting down, so the
        # file must be deleted *after* the reload, not before.
        rm -f "$RC_DIR/genmon-${id}.rc"
        echo "Removed the panel item (plugin id ${id})."
    else
        echo "No genmon panel item found; nothing to remove."
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# install / repair
# ---------------------------------------------------------------------------
if id="$(genmon_id)"; then
    echo "Panel item already present (plugin id ${id}); refreshing its settings."
else
    # next free numeric id is max(existing) + 1 (same rule the panel uses)
    id=$(ids | sort -n | tail -1)
    id=$(( ${id:-0} + 1 ))
    echo "Adding a new panel item (plugin id ${id})."
    xq -p "/plugins/plugin-${id}" -t string -s genmon --create
    set_ids $(ids) "$id"
fi

write_rc "$id"
reload_panel

echo
echo "Done.  The panel shows today's total traffic; click it for the per-app list."
echo "Tip: if you do not see it yet, the panel may need a moment to redraw."

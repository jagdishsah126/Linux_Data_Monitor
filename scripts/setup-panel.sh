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
# OWNERSHIP: genmon is a *generic* widget — plenty of people already have one
# (CPU meter, weather, ...).  This script never touches a genmon plugin unless
# that plugin's own config file names our launcher, and it picks the panel to
# add to instead of blindly assuming "panel-2".
#
# Usage:
#   bash scripts/setup-panel.sh              # install / repair the panel item
#   bash scripts/setup-panel.sh --remove     # remove the panel item
#   bash scripts/setup-panel.sh --panel 1    # force a panel (default: detected)
#
set -euo pipefail

CHANNEL="xfce4-panel"
RC_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/xfce4/panel"
STATE_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/data-monitor"
LAUNCHER="$HOME/.local/bin/dm-panel"
PANEL_OVERRIDE="${DATA_MONITOR_PANEL:-}"
REMOVE=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --remove)  REMOVE=1 ;;
        --panel)   shift; PANEL_OVERRIDE="${1:-}" ;;
        --panel=*) PANEL_OVERRIDE="${1#*=}" ;;
        -h|--help)
            sed -n '14,19p' "$0" | sed 's/^# \{0,1\}//'
            exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 2 ;;
    esac
    shift
done

xq()  { xfconf-query -c "$CHANNEL" "$@"; }

# Every panel that exists, e.g. "panel-1 panel-2".
panels() {
    xq -l 2>/dev/null | sed -n 's#^/panels/\(panel-[0-9][0-9]*\)/.*#\1#p' | sort -u
}

plugin_ids_of() {
    xq -p "/panels/$1/plugin-ids" 2>/dev/null | grep -E '^[0-9]+$' || true
}

all_plugin_ids() {
    local panel
    for panel in $(panels); do plugin_ids_of "$panel"; done
}

component() { xq -p "/plugins/plugin-$1" 2>/dev/null || true; }

# A genmon plugin belongs to us ONLY if its config file names our launcher.
# (Every other genmon widget on the machine is somebody else's and is left
# strictly alone.)
is_ours() {
    local id="$1" rc="$RC_DIR/genmon-$1.rc"
    [[ "$(component "$id")" == "genmon" ]] || return 1
    [[ -f "$rc" ]] || return 1
    grep -qF "Command=$LAUNCHER" "$rc"
}

our_id() {
    local id
    for id in $(all_plugin_ids); do
        is_ours "$id" && { echo "$id"; return 0; }
    done
    return 1
}

our_panel() {
    local panel id
    for panel in $(panels); do
        for id in $(plugin_ids_of "$panel"); do
            is_ours "$id" && { echo "$panel"; return 0; }
        done
    done
    return 1
}

# Where should a NEW item go?  Prefer the panel that carries the task list
# (that is the taskbar), then the conventional panel-2, then the first panel.
pick_panel() {
    if [[ -n "$PANEL_OVERRIDE" ]]; then echo "panel-${PANEL_OVERRIDE#panel-}"; return 0; fi
    local panel id
    for panel in $(panels); do
        for id in $(plugin_ids_of "$panel"); do
            [[ "$(component "$id")" == "tasklist" ]] && { echo "$panel"; return 0; }
        done
    done
    for panel in $(panels); do
        [[ "$panel" == "panel-2" ]] && { echo "$panel"; return 0; }
    done
    panels | head -1
}

# Keep a copy of what the id list looked like before we change it.
backup_ids() {
    mkdir -p "$STATE_DIR"
    {
        echo "# plugin-ids of $1 before this change ($(date -Iseconds))"
        echo "# to restore:  xfconf-query -c $CHANNEL -p /panels/$1/plugin-ids \\"
        echo "#                $(plugin_ids_of "$1" | sed 's/^/-t int -s /' | tr '\n' ' ')"
    } > "$STATE_DIR/panel-ids.backup"
}

set_ids() {                      # replace one panel's plugin-ids array with "$@"
    local panel="$1"; shift
    local args=() value
    for value in "$@"; do args+=(-t int -s "$value"); done
    if [[ ${#args[@]} -eq 0 ]]; then
        xq -p "/panels/$panel/plugin-ids" -r 2>/dev/null || true
    else
        xq -p "/panels/$panel/plugin-ids" "${args[@]}"
    fi
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

reload_panel() {
    # Ask the panel to restart so it picks up the new plugin list.
    xfce4-panel -r >/dev/null 2>&1 || true

    # SAFETY: "xfce4-panel -r" occasionally leaves the panel stopped.  If that
    # happens and XFCE then saves the session, the saved session contains NO
    # panel -- and from then on the taskbar never comes back after login/reboot.
    # So: wait for the panel to return, and start it ourselves if it does not.
    local i
    for i in $(seq 1 20); do
        pgrep -x xfce4-panel >/dev/null 2>&1 && return 0
        sleep 0.5
    done
    echo "!! The panel did not come back after restarting; starting it now." >&2
    setsid xfce4-panel >/dev/null 2>&1 < /dev/null &
}

# ---------------------------------------------------------------------------
# validate an explicit panel choice
# ---------------------------------------------------------------------------
if [[ -n "$PANEL_OVERRIDE" ]]; then
    WANT="panel-${PANEL_OVERRIDE#panel-}"
    if ! panels | grep -qx "$WANT"; then
        echo "!! Panel '$WANT' does not exist. Available: $(panels | tr '\n' ' ')" >&2
        exit 2
    fi
    PANEL_OVERRIDE="${WANT#panel-}"
fi

# ---------------------------------------------------------------------------
# remove
# ---------------------------------------------------------------------------
if [[ "$REMOVE" -eq 1 ]]; then
    if id="$(our_id)"; then
        panel="$(our_panel)"
        backup_ids "$panel"
        set_ids "$panel" $(plugin_ids_of "$panel" | grep -vx "$id" || true)
        xq -p "/plugins/plugin-${id}" -r 2>/dev/null || true
        reload_panel
        # NOTE: the panel rewrites plugin .rc files while shutting down, so the
        # file must be deleted *after* the reload, not before.
        rm -f "$RC_DIR/genmon-${id}.rc"
        echo "Removed the Data Monitor panel item (plugin id ${id} on ${panel})."
    else
        echo "No Data Monitor panel item found; nothing to remove."
    fi
    exit 0
fi

# ---------------------------------------------------------------------------
# install / repair
# ---------------------------------------------------------------------------
if id="$(our_id)"; then
    panel="$(our_panel)"
    echo "Data Monitor panel item already present (plugin id ${id} on ${panel}); refreshing its settings."
else
    panel="$(pick_panel)"
    if [[ -z "$panel" ]]; then
        echo "!! No XFCE panel found. Is xfce4-panel configured for this user?" >&2
        exit 1
    fi
    backup_ids "$panel"
    # next free numeric id is max(existing) + 1 (same rule the panel uses)
    id=$(all_plugin_ids | sort -n | tail -1)
    id=$(( ${id:-0} + 1 ))
    echo "Adding a new Data Monitor item (plugin id ${id} on ${panel})."
    xq -p "/plugins/plugin-${id}" -t string -s genmon --create
    set_ids "$panel" $(plugin_ids_of "$panel") "$id"
fi

write_rc "$id"
reload_panel

echo
echo "Done.  The panel shows today's total traffic; click it for the per-app list."
echo "Tip: if you do not see it yet, the panel may need a moment to redraw."

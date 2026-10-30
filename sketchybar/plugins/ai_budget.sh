#!/usr/bin/env zsh

# Show the weekly quota percentage used for one provider. Set QUOTA_AXI_BIN if
# SketchyBar is launched with a PATH that does not include quota-axi.
PROVIDER="$1"
[[ -z "$PROVIDER" ]] && exit 0

QUOTA_AXI_BIN="${QUOTA_AXI_BIN:-$(command -v quota-axi 2>/dev/null)}"
if [[ -z "$QUOTA_AXI_BIN" ]]; then
    for candidate in "$HOME"/nikud/xdg_config_home/nvm/versions/node/*/bin/quota-axi; do
        if [[ -x "$candidate" ]]; then
            QUOTA_AXI_BIN="$candidate"
            break
        fi
    done
fi

# quota-axi is a Node entrypoint. Launch agents often do not inherit the
# interactive shell's nvm PATH, so make the matching node available too.
if [[ -x "$QUOTA_AXI_BIN" ]]; then
    QUOTA_AXI_DIR="${QUOTA_AXI_BIN%/*}"
    export PATH="$QUOTA_AXI_DIR:$PATH"
fi

REMAINING="?"
if [[ -x "$QUOTA_AXI_BIN" ]]; then
    QUOTA_JSON="$($QUOTA_AXI_BIN --provider "$PROVIDER" --json 2>/dev/null)"
    PARSED_REMAINING="$(jq -r '
        .providers[0]
        | select(.state.stale != true)
        | .windows[]?
        | select(.kind == "weekly" or .id == "seven_day")
        | (.percentUsed // (100 - .percentRemaining)) | round
    ' <<< "$QUOTA_JSON" 2>/dev/null | head -1)"
    [[ "$PARSED_REMAINING" == <-> ]] && REMAINING="$PARSED_REMAINING"
fi

sketchybar --set "$NAME" label="${REMAINING}%"

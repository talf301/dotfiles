#!/usr/bin/env zsh
API="http://localhost:26538/api/v1"

[[ $SENDER == "mouse.clicked" ]] && curl -s --max-time 1 -X POST "$API/toggle-play" >/dev/null 2>&1

SONG=$(curl -s --max-time 1 "$API/song" 2>/dev/null)
TITLE=$(jq -r '.title // empty' <<<"$SONG" 2>/dev/null)

if [[ -z $TITLE ]]; then
    sketchybar --set $NAME drawing=off
    exit 0
fi

ARTIST=$(jq -r '.artist // empty' <<<"$SONG" 2>/dev/null)
[[ $(jq -r '.isPaused // false' <<<"$SONG" 2>/dev/null) == "true" ]] \
    && ICON=$'\UF03E4' || ICON=$'\UF040A'   # pause / play

LABEL="$TITLE"
[[ -n $ARTIST ]] && LABEL="$TITLE — $ARTIST"
(( ${#LABEL} > 30 )) && LABEL="${LABEL:0:30}…"

sketchybar --set $NAME drawing=on icon="$ICON" label="$LABEL"

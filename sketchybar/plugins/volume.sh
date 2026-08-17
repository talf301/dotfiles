#!/usr/bin/env zsh

if [[ $SENDER == "mouse.scrolled" ]]; then
    CUR=$(osascript -e 'output volume of (get volume settings)')
    if (( ${SCROLL_DELTA%.*} >= 0 )); then
        VOL=$(( CUR + 5 > 100 ? 100 : CUR + 5 ))
    else
        VOL=$(( CUR - 5 < 0 ? 0 : CUR - 5 ))
    fi
    osascript -e "set volume output volume $VOL"
else
    VOL=${INFO:-$(osascript -e 'output volume of (get volume settings)')}
fi

MUTED=$(osascript -e 'output muted of (get volume settings)' 2>/dev/null)

if [[ $MUTED == "true" ]] || (( VOL == 0 )); then
    ICON=$'\UF0581'                        # volume-off
elif (( VOL < 34 )); then
    ICON=$'\UF057F'                        # volume-low
elif (( VOL < 67 )); then
    ICON=$'\UF0580'                        # volume-medium
else
    ICON=$'\UF057E'                        # volume-high
fi

sketchybar --set $NAME icon="$ICON" label="${VOL}%"

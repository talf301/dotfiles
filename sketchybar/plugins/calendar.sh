#!/usr/bin/env zsh
# The EventKit helper (run by launchd, see helpers/com.tal.sketchybar-nextevent.plist)
# writes "<title>|<Unix start time>|<meeting URL>" here. sketchybar only reads the cache, so it
# never blocks on Calendar and never needs Calendar permission itself.
CACHE="$HOME/.cache/sketchybar/nextevent"

OUT=$(<"$CACHE" 2>/dev/null)
if [[ -z $OUT ]]; then
    sketchybar --set $NAME drawing=off
    exit 0
fi

TITLE="${OUT%%|*}"
DETAILS="${OUT#*|}"
START_TIME="${DETAILS%%|*}"
[[ $DETAILS == *'|'* ]] && URL="${DETAILS#*|}"

if [[ ! $START_TIME == <-> ]]; then
    sketchybar --set $NAME drawing=off
    exit 0
fi

NOW=${NOW:-$(date +%s)}
SECS=$(( START_TIME - NOW ))

# A meeting that ended more than five minutes ago means the helper cache is stale.
if (( SECS < -300 )); then
    sketchybar --set $NAME drawing=off
    exit 0
fi

if [[ $SENDER == "mouse.clicked" ]]; then
    [[ -n $URL ]] && open "$URL"
    exit 0
fi

MINS=$(( SECS / 60 ))
if (( MINS < 1 )); then    WHEN="now"
elif (( MINS < 60 )); then WHEN="${MINS}m"
else                       WHEN="$(( MINS / 60 ))h$(( MINS % 60 ))m"
fi

(( ${#TITLE} > 22 )) && TITLE="${TITLE:0:22}…"

if [[ $SKETCHYBAR_LAPTOP == 1 ]]; then
    LABEL="$WHEN"
else
    LABEL="$TITLE · $WHEN"
fi

sketchybar --set $NAME drawing=on icon=$'\UF00ED' label="$LABEL"

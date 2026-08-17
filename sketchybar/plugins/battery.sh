#!/usr/bin/env zsh
# Icons are written as \U escapes: literal glyphs get stripped by some editors/tools.

PCT=$(pmset -g batt | grep -Eo '[0-9]+%' | head -1 | tr -d '%')
[[ -z $PCT ]] && exit 0
CHARGING=$(pmset -g batt | grep -c 'AC Power')
BG=0xff8aadf4

if (( CHARGING > 0 )); then
    ICON=$'\UF0084'                        # battery-charging
else
    case $PCT in
        100)      ICON=$'\UF0079' ;;       # battery (full)
        9[0-9])   ICON=$'\UF0082' ;;
        8[0-9])   ICON=$'\UF0081' ;;
        7[0-9])   ICON=$'\UF0080' ;;
        6[0-9])   ICON=$'\UF007F' ;;
        5[0-9])   ICON=$'\UF007E' ;;
        4[0-9])   ICON=$'\UF007D' ;;
        3[0-9])   ICON=$'\UF007C' ;;
        2[0-9])   ICON=$'\UF007B' ;;
        1[0-9])   ICON=$'\UF007A' ;;
        *)        ICON=$'\UF0083' ;;       # battery-alert
    esac
fi

sketchybar --set $NAME icon="$ICON" label="${PCT}%" \
    background.color=$BG icon.color=0xff24273a label.color=0xff24273a

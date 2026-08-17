#!/usr/bin/env zsh
# Self-check for the branchy plugin logic. Stubs pmset/osascript/sketchybar (as /bin/sh,
# so no zsh rc files interfere) meaning nothing touches the real bar.  Run: ./selftest.sh
cd ${0:A:h}
STUB=$(mktemp -d); trap 'rm -rf $STUB' EXIT
export SB_LOG="$STUB/out"

printf '#!/bin/sh\nprintf "%%s\\n" "$*" >> "$SB_LOG"\n'                       > $STUB/sketchybar
printf '#!/bin/sh\nprintf "Now drawing from %%s\\n" "$FAKE_SRC"\nprintf " -InternalBattery-0 %%s%%%% present\\n" "$FAKE_PCT"\n' > $STUB/pmset
printf '#!/bin/sh\ncase "$*" in *muted*) echo false;; *) echo "${FAKE_VOL:-50}";; esac\n' > $STUB/osascript
printf '#!/bin/sh\nprintf "%%s\\n" "$FAKE_IPCONFIG"\n'                    > $STUB/ipconfig
printf '#!/bin/sh\nprintf "%%s\\n" "$FAKE_IFCONFIG"\n'                    > $STUB/ifconfig
printf '#!/bin/sh\nprintf "%%s" "$FAKE_SONG"\n'                             > $STUB/curl
printf '#!/bin/sh\nprintf "%%s\n" "$FAKE_DISPLAY_COUNT"\n'                > $STUB/aerospace
printf '#!/bin/sh\nprintf "%%s\n" "$*" >> "$SB_LOG"\n'                  > $STUB/open
chmod +x $STUB/sketchybar $STUB/pmset $STUB/osascript $STUB/ipconfig $STUB/ifconfig $STUB/curl $STUB/aerospace $STUB/open
export PATH="$STUB:$PATH"

typeset -i fail=0
check() {  # check <desc> <expected substring>
    if grep -qF -- "$2" $SB_LOG; then print "  ok   $1"
    else print "  FAIL $1 -- wanted '$2' got '$(<$SB_LOG)'"; fail=1; fi
}
glyph() { printf "\\U$1" }

# --- battery: icon bucket per percentage ---
for spec in 100:F0079 95:F0082 85:F0081 75:F0080 65:F007F 55:F007E 42:F007D 35:F007C 25:F007B 15:F007A 5:F0083; do
    : > $SB_LOG
    FAKE_PCT=${spec%%:*} FAKE_SRC="Battery Power" NAME=battery zsh -f ./battery.sh
    check "battery ${spec%%:*}% icon" "$(glyph ${spec#*:})"
done
: > $SB_LOG; FAKE_PCT=15 FAKE_SRC="Battery Power" NAME=battery zsh -f ./battery.sh
check "battery 15% background is blue" "background.color=0xff8aadf4"
: > $SB_LOG; FAKE_PCT=55 FAKE_SRC="Battery Power" NAME=battery zsh -f ./battery.sh
check "battery 55% background is blue" "background.color=0xff8aadf4"
: > $SB_LOG; FAKE_PCT=55 FAKE_SRC="AC Power" NAME=battery zsh -f ./battery.sh
check "charging shows bolt" "$(glyph F0084)"

# --- volume: icon bucket per level ---
for spec in 0:F0581 20:F057F 50:F0580 90:F057E; do
    : > $SB_LOG; NAME=volume INFO=${spec%%:*} zsh -f ./volume.sh
    check "volume ${spec%%:*}% icon" "$(glyph ${spec#*:})"
done

# --- calendar: countdown formatting, then the guards ---
CACHE="$HOME/.cache/sketchybar/nextevent"
SAVE=$(<$CACHE 2>/dev/null)
cal() { print -n "$1" > $CACHE; : > $SB_LOG; NAME=calendar zsh -f ./calendar.sh }
for spec in "Standup|300::Standup · 5m" "Long|7800::Long · 2h10m" "Soon|30::Soon · now" "Started|-240::Started · now"; do
    cal "${spec%%::*}"; check "calendar ${spec%%::*}" "${spec#*::}"
done
for bad in "Old|-301" "garbage" ""; do
    cal "$bad"; check "calendar hides '$bad'" "drawing=off"
done
cal "$(printf 'A%.0s' {1..40})|300"; check "calendar truncates long title" "…"
: > $SB_LOG; print -n "Standup|300|https://meet.example.com/standup" > $CACHE
NAME=calendar SENDER=mouse.clicked zsh -f ./calendar.sh
check "calendar opens meeting URL" "https://meet.example.com/standup"
print -n "$SAVE" > $CACHE

# --- display changes: reload only when the laptop/desktop mode changed ---
: > $SB_LOG; FAKE_DISPLAY_COUNT=2 zsh -f ./display_change.sh 0
[[ ! -s $SB_LOG ]] || { print "  FAIL desktop focus change reloaded the bar"; fail=1; }
: > $SB_LOG; FAKE_DISPLAY_COUNT=1 zsh -f ./display_change.sh 0
check "desktop to laptop reloads the bar" "--reload"

# --- wifi: name from cache, state from ipconfig ---
WCACHE="$HOME/.cache/sketchybar/wifi"
WSAVE=$(<$WCACHE 2>/dev/null)
wifi() {  # wifi <cache contents> <ipconfig output>
    print -n "$1" > $WCACHE; : > $SB_LOG
    FAKE_IPCONFIG="$2" FAKE_IFCONFIG="" NAME=wifi zsh -f ./wifi.sh
}
wifi "HomeNet"  " SSID : <redacted>"; check "wifi shows cached name"      "label=HomeNet"
wifi ""         " SSID : <redacted>"; check "wifi falls back to Wi-Fi"    "label=Wi-Fi"
wifi "HomeNet"  ""                  ; check "wifi off when disconnected"  "label=off"
wifi "iPhone"   " is_hotspot : TRUE"; check "wifi hotspot icon"           "$(glyph F0CA3)"
wifi "$(printf 'N%.0s' {1..30})" " SSID : x"; check "wifi truncates long name" "…"
print -n "$WSAVE" > $WCACHE

# --- ytmusic: label/icon from the API payload, hidden when nothing is playing ---
ytm() { FAKE_SONG="$1" NAME=ytmusic zsh -f ./ytmusic.sh }
: > $SB_LOG; ytm '{"title":"Scars","artist":"Papa Roach","isPaused":false}'
check "ytmusic playing label" "Scars — Papa Roach"
check "ytmusic playing icon"  "$(glyph F040A)"
: > $SB_LOG; ytm '{"title":"Scars","artist":"Papa Roach","isPaused":true}'
check "ytmusic paused icon"   "$(glyph F03E4)"
: > $SB_LOG; ytm ''
check "ytmusic hidden when idle" "drawing=off"
: > $SB_LOG; ytm "{\"title\":\"$(printf 'T%.0s' {1..40})\",\"artist\":\"A\",\"isPaused\":false}"
check "ytmusic truncates long title" "…"

(( fail )) && { print "FAILED"; exit 1 }
print "all passed"

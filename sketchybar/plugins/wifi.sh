#!/usr/bin/env zsh
# The SSID comes from the cache written by sketchybar-wifi.app (launchd agent
# local.sketchybar.wifi): macOS 15 only reveals it to a Location-Services-authorized
# app bundle. Connection state comes from ipconfig, which is never redacted, so the
# item still reads correctly if that helper is missing or not yet granted.

CACHE="$HOME/.cache/sketchybar/wifi"

# Joining a different network invalidates the cached name, so refresh it now instead of
# waiting for the agent's slow poll (which is slow on purpose: each run briefly lights up
# the menu-bar location indicator).
if [[ $SENDER == "wifi_change" ]]; then
    launchctl kickstart "gui/$(id -u)/local.sketchybar.wifi" >/dev/null 2>&1
    for _ in {1..10}; do
        [[ -s $CACHE ]] && [[ $(date -r "$CACHE" +%s) -ge $(( $(date +%s) - 3 )) ]] && break
        sleep 0.2
    done
fi

SUMMARY=$(ipconfig getsummary en0 2>/dev/null)
SSID=$(<"$CACHE" 2>/dev/null)

if [[ $SUMMARY == *"is_hotspot : TRUE"* ]]; then
    ICON=$'\UF0CA3'                                  # cellphone-link (tethered)
    LABEL=${SSID:-hotspot}
elif [[ $SUMMARY == *" SSID : "* ]] || ifconfig en0 2>/dev/null | grep -q "status: active"; then
    ICON=$'\UF05A9'                                  # wifi
    LABEL=${SSID:-Wi-Fi}
else
    ICON=$'\UF05AA'                                  # wifi-off
    LABEL="off"
fi

(( ${#LABEL} > 18 )) && LABEL="${LABEL:0:18}…"

sketchybar --set $NAME icon="$ICON" label="$LABEL"

#!/bin/bash

CONFIG_DIR="$HOME/.config/sketchybar"

DISPLAY_COUNT=$(aerospace list-monitors --count 2>/dev/null)
if (( ${DISPLAY_COUNT:-1} == 1 )); then
  MAX_WORKSPACE_APPS=2
else
  MAX_WORKSPACE_APPS=999
fi

update_space_icons() {
  local sid=$1
  local apps=$(aerospace list-windows --workspace "$sid" | awk -F'|' '{gsub(/^ *| *$/, "", $2); print $2}')

  sketchybar --set space.$sid drawing=on

  if [ "${apps}" != "" ]; then
    icon_strip=""
    seen_icons=""
    unique_count=0
    while read -r app; do
      app_icon="$($CONFIG_DIR/helpers/icon_map.sh "$app")"
      case " $seen_icons " in
        *" $app_icon "*) continue ;;
      esac
      seen_icons+=" $app_icon"
      if (( unique_count < MAX_WORKSPACE_APPS )); then
        icon_strip+="$app_icon"
      fi
      (( unique_count++ ))
    done <<<"${apps}"
    if (( unique_count > MAX_WORKSPACE_APPS )); then
      icon_strip+="…"
    fi
  else
    icon_strip=""
  fi
  sketchybar --set space.$sid label="$icon_strip"
}

# Update all workspaces to ensure clean state
for monitor_id in $(aerospace list-monitors --format "%{monitor-id}"); do
  for sid in $(aerospace list-workspaces --monitor "$monitor_id"); do
    update_space_icons "$sid"
  done
done

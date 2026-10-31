#!/usr/bin/env zsh
# Dispatched Claude sessions (background or Rex tab) waiting on Tal. An interactive session that is showing a
# question or permission prompt (`claude agents` status "waiting") always counts as a question. A session counts when its name starts with a bead id
# (/dispatch-bead names them "<bead-id>: <description>"), its bead is still in progress, and it is not working.
# Agent view marks every ended turn "blocked", so the session's own first line decides: "QUESTION:" counts as a
# question, "READY:" as ready for review. A session without either line falls back to agent view's state
# (blocked = question, done = ready). Shows "<n>?" and "<n>✓"; hidden when both are zero. Refreshed by
# update_freq and by the dispatched sessions' Stop hook (`sketchybar --trigger agents_changed`).

DIGEST="${DIGEST_BIN:-$HOME/dotfiles/iar/agents-digest.sh}"

counts=$(zsh -f "$DIGEST" 2>/dev/null | /usr/bin/jq -rs '
  [.[] | select(.bead_in_progress and .state != "working" and .status != "busy")
   | (.last_message // "") as $m
   | if .status == "waiting" then "q"
     elif ($m | startswith("QUESTION:")) then "q"
     elif ($m | startswith("READY:")) then "r"
     elif .state == "blocked" then "q"
     elif .state == "done" then "r"
     else empty end]
  | "\(map(select(. == "q")) | length) \(map(select(. == "r")) | length)"' 2>/dev/null)
read -r questions ready <<< "${counts:-0 0}"

label=""
(( questions > 0 )) && label="${questions}?"
(( ready > 0 )) && label="${label:+$label }${ready}✓"

if [[ -n "$label" ]]; then
  color=0xffa6e3a1
  (( questions > 0 )) && color=0xfff38ba8
  sketchybar --set "$NAME" drawing=on label="$label" label.drawing=on icon.color=$color label.color=$color
else
  sketchybar --set "$NAME" drawing=off
fi

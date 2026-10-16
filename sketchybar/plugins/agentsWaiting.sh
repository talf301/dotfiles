#!/usr/bin/env zsh
# Dispatched background Claude sessions waiting on Tal. A session counts when its name starts with a
# bead id (/dispatch-bead names them "<bead-id>: <description>"), its turn has ended (agent view state
# blocked = question, done = ready for review), and its bead is still in progress — so finished work
# drops out without deleting the session. Shows "<n>?" for questions and "<n>✓" for ready-for-review;
# hidden when both are zero. Refreshed by update_freq and by the dispatched sessions' Stop hook
# (`sketchybar --trigger agents_changed`).

export BEADS_DIR="${BEADS_DIR:-$HOME/.local/share/beads/precog/.beads}"
CLAUDE="${CLAUDE_BIN:-$HOME/.local/bin/claude}"
BR="${BR_BIN:-$HOME/.local/bin/br}"
JQ=/usr/bin/jq

counts=$("$JQ" -rn --argjson agents "$("$CLAUDE" agents --json 2>/dev/null || echo '[]')" \
  --argjson beads "$("$BR" list --status in_progress --json 2>/dev/null || echo '{"issues":[]}')" '
  ($beads | (if type == "array" then . else .issues end) | map(.id)) as $open
  | [$agents[] | select(.kind == "background")
     | ((.name // "") | capture("^(?<id>[a-z0-9]+-[a-z0-9.]+): ")? | .id) as $id
     | select(($open | index($id)) != null) | .state]
  | "\(map(select(. == "blocked")) | length) \(map(select(. == "done")) | length)"' 2>/dev/null)
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

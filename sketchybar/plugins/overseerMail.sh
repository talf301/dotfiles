#!/usr/bin/env zsh
# needs-human indicator for the IAR swarm. Counts beads tagged `needs-human`
# (agent escalations + supervisor cap-hits) straight from beads — read-only, no
# Agent Mail. The bead IS the escalation channel now; read the question with
# `br comments <id>` or `swarm-status.sh`. Shows the item only when >0, so the
# bar stays clean.

export BEADS_DIR="${BEADS_DIR:-$HOME/.local/share/beads/precog/.beads}"
BR="$HOME/.local/bin/br"
JQ=/usr/bin/jq

COUNT=0
if [[ -x "$BR" && -x "$JQ" ]]; then
  COUNT=$("$BR" list -l needs-human --status open --status in_progress --json 2>/dev/null \
    | "$JQ" '.issues | length' 2>/dev/null)
  [[ "$COUNT" == <-> ]] || COUNT=0
fi

if [[ "$COUNT" -gt 0 ]]; then
  sketchybar --set "$NAME" drawing=on label="$COUNT" label.drawing=on \
    icon.color=0xfff38ba8 label.color=0xfff38ba8
else
  sketchybar --set "$NAME" drawing=off
fi

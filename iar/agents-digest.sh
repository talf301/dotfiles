#!/usr/bin/env zsh
# One JSON object per Claude session for the /digest skill. Joins `claude agents --json` (state, name, cwd) with
# each session's transcript (AI title, last prompt, last PR link, first line of the last assistant message,
# last message time) and with in-progress beads (a session named "<bead-id>: …" maps to its bead).
# Reads only the transcript tail, so it stays fast on long sessions. Usage: agents-digest.sh [--all]

export BEADS_DIR="${BEADS_DIR:-$HOME/.local/share/beads/precog/.beads}"
JQ=/usr/bin/jq
beads=$("$HOME/.local/bin/br" list --status in_progress --json 2>/dev/null || echo '[]')

"$HOME/.local/bin/claude" agents --json ${1:+--all} 2>/dev/null | "$JQ" -c '.[] | select(.state != "stopped")' |
while IFS= read -r agent; do
  sid=$(print -r -- "$agent" | "$JQ" -r '.sessionId')
  transcript=( $HOME/.claude/projects/*/$sid.jsonl(N) )
  facts='{}'
  if (( $#transcript )); then
    facts=$(tail -c 400000 "$transcript[1]" | "$JQ" -Rs '
      [split("\n")[] | fromjson? // empty] as $rows
      | {
          title: ([$rows[] | select(.type == "ai-title") | .aiTitle] | last),
          last_prompt: ([$rows[] | select(.type == "last-prompt") | .lastPrompt] | last),
          pr: ([$rows[] | select(.type == "pr-link") | .prUrl] | last),
          last_message: ([$rows[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | last // "" | split("\n")[0]),
          last_active: ([$rows[] | select(.type == "assistant" or .type == "user") | .timestamp] | last)
        }')
  fi
  "$JQ" -cn --argjson a "$agent" --argjson f "$facts" --argjson beads "$beads" '
    ($beads | (if type == "array" then . else .issues end)) as $open
    | (first(($a.name // "") | capture("^(?<id>[a-z0-9]+-[a-z0-9.]+): ")? | .id) // null) as $bead
    | $a + $f + {bead: $bead, bead_in_progress: ($bead != null and ([$open[] | .id] | index($bead)) != null)}'
done

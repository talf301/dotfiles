#!/usr/bin/env zsh
# Stop hook for /dispatch-bead sessions (loaded with `claude --bg --settings ~/dotfiles/iar/dispatch-settings.json`).
# When a dispatched session ends its turn: post a macOS notification titled with the session name whose body is
# the first line of its last message (QUESTION: … or READY: …), and refresh the sketchybar agents_waiting item.
# Never blocks the session: always exits 0.

input=$(cat)
session_id=$(print -r -- "$input" | /usr/bin/jq -r '.session_id // empty')
transcript=$(print -r -- "$input" | /usr/bin/jq -r '.transcript_path // empty')
message=$(print -r -- "$input" | /usr/bin/jq -r '.last_assistant_message // empty')
if [[ -z "$message" && -r "$transcript" ]]; then
  message=$(/usr/bin/jq -rs '[.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | last // ""' "$transcript")
fi
first_line=${${(f)message}[1]}
name=$("$HOME/.local/bin/claude" agents --json 2>/dev/null \
  | /usr/bin/jq -r --arg sid "$session_id" '.[] | select(.sessionId == $sid) | .name // empty')

/usr/bin/osascript - "${name:-Claude session}" "${first_line:0:200}" <<'EOF' >/dev/null 2>&1
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
EOF
command -v sketchybar >/dev/null && sketchybar --trigger agents_changed >/dev/null 2>&1
exit 0

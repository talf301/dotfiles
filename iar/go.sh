#!/usr/bin/env zsh
# Put Tal in a Claude session: show its Rex tab when one runs it, else open a Rex tab running
# `claude attach` (background sessions only). ClaudeGo.app calls this for claudego://s/<sessionId> links on board.md.
# Usage: go.sh [--dry] <sessionId>

export XDG_CONFIG_HOME="$HOME/nikud/xdg_config_home"
JQ=/usr/bin/jq REX=$HOME/.local/bin/rex CLAUDE=$HOME/.local/bin/claude
dry=""; [[ $1 == --dry ]] && { dry=echo; shift }
sid=$1
print -r -- "$(date '+%F %T') go $*" >> "$HOME/Library/Logs/session-board-actions.log"
[[ -n $sid ]] || { print -u2 "usage: go.sh [--dry] <sessionId>"; exit 2 }

agent=$("$CLAUDE" agents --json --all 2>/dev/null | "$JQ" -c --arg s "$sid" 'first(.[] | select(.sessionId == $s or .id == $s)) // empty')
[[ -n $agent ]] || { osascript -e "display notification \"No session $sid\" with title \"ClaudeGo\""; exit 1 }
name=$(print -r -- "$agent" | "$JQ" -r '.name // ""')
id=$(print -r -- "$agent" | "$JQ" -r '.id // ""')
kind=$(print -r -- "$agent" | "$JQ" -r '.kind')

# Find the tab by name with `rex window ls --json`: a tab or pane label equal to the session name or its bead id
# (bin/swarm names tabs after the bead), else a Rex session with that label (its active tab).
# Prints "<session_id> <window_id>".
sessions=$("$REX" ls --json 2>/dev/null | "$JQ" -r '.sessions[].session_id')
target=$(for s in ${(f)sessions}; do "$REX" window ls -s "$s" --json 2>/dev/null; done | "$JQ" -rs --arg n "$name" '
  ($n | split(":")[0]) as $b
  | [.[] | . as $s | .windows[]
     | select($n != "" and (.label == $n or .label == $b or any(.layers[]?.blocks[]?; .label == $n)))
     | "\($s.session_id) \(.window_id)"]
  + [.[] | select($n != "" and .label == $n) | "\(.session_id) \(.active_window_id)"]
  | first(.[]) // empty')

if [[ -n $target ]]; then
  $dry "$REX" do session.select session_id="${target% *}" window_id="${target#* }"
elif [[ $kind == background && -n $id && -n $sessions ]]; then
  session=${${(f)sessions}[1]}
  if [[ -n $dry ]]; then
    echo "$REX" window new -s "$session" "$name" --json -- claude attach "$id"
    echo "$REX" do session.select session_id="$session" window_id="<window_id>"
  else
    window=$("$REX" window new -s "$session" "$name" --json -- claude attach "$id" | "$JQ" -r '.window_id // empty')
    "$REX" do session.select session_id="$session" ${window:+window_id="$window"} >/dev/null
  fi
else
  osascript -e "display notification \"$name is not in a Rex tab (interactive, cwd elsewhere)\" with title \"ClaudeGo\""
  exit 1
fi
$dry open -a "Rex Beta"

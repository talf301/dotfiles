#!/usr/bin/env zsh
# Put Tal in a Claude session: focus its herdr pane when one runs it, else open a herdr tab running
# `claude attach` (background sessions only). ClaudeGo.app calls this for claudego://<sessionId> links on board.md.
# Usage: go.sh [--dry] <sessionId>

export XDG_CONFIG_HOME="$HOME/nikud/xdg_config_home"
JQ=/usr/bin/jq HERDR=/opt/homebrew/bin/herdr CLAUDE=$HOME/.local/bin/claude
dry=""; [[ $1 == --dry ]] && { dry=echo; shift }
sid=$1
[[ -n $sid ]] || { print -u2 "usage: go.sh [--dry] <sessionId>"; exit 2 }

agent=$("$CLAUDE" agents --json --all 2>/dev/null | "$JQ" -c --arg s "$sid" 'first(.[] | select(.sessionId == $s or .id == $s)) // empty')
[[ -n $agent ]] || { osascript -e "display notification \"No session $sid\" with title \"ClaudeGo\""; exit 1 }
name=$(print -r -- "$agent" | "$JQ" -r '.name // ""')
id=$(print -r -- "$agent" | "$JQ" -r '.id // ""')
kind=$(print -r -- "$agent" | "$JQ" -r '.kind')
full=$(print -r -- "$agent" | "$JQ" -r '.sessionId')

# herdr knows some panes by Claude session id and others only by terminal title (the session name).
pane=$("$HERDR" agent list 2>/dev/null | "$JQ" -r --arg s "$full" --arg n "$name" '
  [.result.agents[] | select(.agent_session.value == $s)] + [.result.agents[] | select($n != "" and .terminal_title_stripped == $n)]
  | first(.[].pane_id) // empty')

if [[ -n $pane ]]; then
  $dry "$HERDR" agent focus "$pane"
elif [[ $kind == background && -n $id ]]; then
  workspace=$("$HERDR" workspace list | "$JQ" -r 'first(.result.workspaces[] | select(.focused)).workspace_id // first(.result.workspaces[]).workspace_id')
  if [[ -n $dry ]]; then
    echo "$HERDR" tab create --workspace "$workspace" --label "$name" --focus
    echo "$HERDR" pane run "<root_pane>" "claude attach $id"
  else
    root=$("$HERDR" tab create --workspace "$workspace" --label "$name" --focus | "$JQ" -r '.result.root_pane | if type == "object" then .pane_id else . end // empty')
    "$HERDR" pane run "$root" "claude attach $id" >/dev/null
  fi
else
  osascript -e "display notification \"$name is not in herdr (interactive, cwd elsewhere)\" with title \"ClaudeGo\""
  exit 1
fi
$dry open -a WezTerm

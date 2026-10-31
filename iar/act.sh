#!/usr/bin/env zsh
# Cleanup actions behind claudego://act/<verb>/<arg> links on board.md. Every action asks first in a dialog.
# Resolve runs without a dialog: the Inbox can reopen a thread.
# Usage: act.sh rm <session id> | bead <bead id> | slot <slot number> | plannotator <pid> | resolve <thread id>

export BEADS_DIR="$HOME/.local/share/beads/precog/.beads" PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
POOL=$HOME/.treehouse/precog-28077b LEDGER=$HOME/dotfiles/iar/ledger  # the only tracker client
verb=$1 arg=$2
print -r -- "$(date '+%F %T') $*" >> "$HOME/Library/Logs/session-board-actions.log"

ask() {  # ask <message> <button>… — prints the chosen button, exits on Cancel
  local buttons=$(printf '"%s", ' "${@:2}")
  osascript -e "button returned of (display dialog \"$1\" buttons {\"Cancel\", ${buttons%, }} default button \"Cancel\" with title \"Board\")" 2>/dev/null || exit 0
}
tell() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"Board\"" >/dev/null 2>&1 }
done_() { osascript -e "display notification \"$1\" with title \"Board\"" >/dev/null 2>&1 }

case $verb in
  rm)
    name=$(claude agents --json --all | jq -r --arg i "$arg" 'first(.[] | select(.id == $i)).name // $i')
    ask "Remove background session \\\"$name\\\" and its worktree (claude rm)?" Remove >/dev/null
    out=$(claude rm "$arg" 2>&1) || { tell "claude rm refused:\n${out//\"/\'}"; exit 1 }
    done_ "Removed $name" ;;
  bead)
    title=$("$LEDGER" show "$arg" 2>/dev/null | jq -r '.title // ""')
    case $(ask "$arg: ${title//\"/\'}\n\nRelease puts it back to open. Close finishes it." Release Close) in
      Release) "$LEDGER" update "$arg" -s open >/dev/null && done_ "Released $arg" ;;
      Close) "$LEDGER" close "$arg" --reason "closed from board" >/dev/null && done_ "Closed $arg" ;;
    esac ;;
  slot)
    dir=$POOL/$arg/precog
    [[ -d $dir ]] || { tell "No slot $arg"; exit 1 }
    dirty=$(git -C "$dir" status --porcelain | head -5)
    unpushed=$(git -C "$dir" log --oneline HEAD --not --remotes -5)
    [[ -z $dirty && -z $unpushed ]] || { tell "Slot $arg holds work that is not pushed, so it stays leased:\n\n${dirty}${unpushed//\"/\'}"; exit 1 }
    ask "Return slot $arg ($(git -C "$dir" branch --show-current))? It is clean and pushed." Return >/dev/null
    (cd "$dir" && treehouse return --force "$dir") && done_ "Returned slot $arg" ;;
  resolve)
    printf '%s\n' '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-03-26","capabilities":{},"clientInfo":{"name":"board","version":"1"}}}' \
      '{"jsonrpc":"2.0","method":"notifications/initialized"}' \
      "{\"jsonrpc\":\"2.0\",\"id\":2,\"method\":\"tools/call\",\"params\":{\"name\":\"resolve_message\",\"arguments\":{\"message_id\":\"$arg\",\"resolved\":true}}}" |
      timeout 20 plannotator inbox mcp >/dev/null 2>&1 && done_ "Resolved Inbox thread" ;;
  plannotator)
    ps -o command= -p "$arg" | grep -q plannotator || { tell "Plannotator session $arg is gone"; exit 0 }
    ask "Close Plannotator session $arg?" Close >/dev/null
    kill "$arg" && done_ "Closed Plannotator session $arg" ;;
  *) tell "Unknown action: $verb"; exit 2 ;;
esac
"$HOME/dotfiles/iar/board.py"

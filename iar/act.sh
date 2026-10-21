#!/usr/bin/env zsh
# Cleanup actions behind claudego://act/<verb>/<arg> links on board.md. Every action asks first in a dialog.
# Usage: act.sh rm <session id> | bead <bead id> | slot <slot number> | plannotator <pid>

export BEADS_DIR="$HOME/.local/share/beads/precog/.beads" PATH="$HOME/.local/bin:/opt/homebrew/bin:/usr/bin:/bin"
POOL=$HOME/.treehouse/precog-28077b
verb=$1 arg=$2

ask() {  # ask <message> <button>… — prints the chosen button, exits on Cancel
  local buttons=$(printf '"%s", ' "${@:2}")
  osascript -e "button returned of (display dialog \"$1\" buttons {\"Cancel\", ${buttons%, }} default button \"Cancel\" with title \"Board\")" 2>/dev/null || exit 0
}
tell() { osascript -e "display dialog \"$1\" buttons {\"OK\"} default button \"OK\" with title \"Board\"" >/dev/null 2>&1 }

case $verb in
  rm)
    name=$(claude agents --json --all | jq -r --arg i "$arg" 'first(.[] | select(.id == $i)).name // $i')
    ask "Remove background session \\\"$name\\\" and its worktree (claude rm)?" Remove >/dev/null
    out=$(claude rm "$arg" 2>&1) || { tell "claude rm refused:\n${out//\"/\'}"; exit 1 } ;;
  bead)
    title=$(br show "$arg" --json 2>/dev/null | jq -r '(if type == "array" then .[0] else . end).title // ""')
    case $(ask "$arg: ${title//\"/\'}\n\nRelease puts it back to open. Close finishes it." Release Close) in
      Release) br update "$arg" --status open ;;
      Close) br close "$arg" --reason "closed from board" ;;
    esac ;;
  slot)
    dir=$POOL/$arg/precog
    [[ -d $dir ]] || { tell "No slot $arg"; exit 1 }
    dirty=$(git -C "$dir" status --porcelain | head -5)
    unpushed=$(git -C "$dir" log --oneline HEAD --not --remotes -5)
    [[ -z $dirty && -z $unpushed ]] || { tell "Slot $arg holds work that is not pushed, so it stays leased:\n\n${dirty}${unpushed//\"/\'}"; exit 1 }
    ask "Return slot $arg ($(git -C "$dir" branch --show-current))? It is clean and pushed." Return >/dev/null
    (cd "$dir" && treehouse return --force "$dir") ;;
  plannotator)
    ps -o command= -p "$arg" | grep -q plannotator || { tell "Plannotator session $arg is gone"; exit 0 }
    ask "Close Plannotator session $arg?" Close >/dev/null
    kill "$arg" ;;
  *) tell "Unknown action: $verb"; exit 2 ;;
esac
"$HOME/dotfiles/iar/board.py"

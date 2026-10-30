#!/usr/bin/env bash
# Check dcg/config.toml: agent-owned paths pass, home/system paths stay blocked.
# Prints the decision without the config (before) and with it (after).
set -uo pipefail
CFG="$(cd "$(dirname "$0")" && pwd)/config.toml"
EMPTY="$(mktemp)"; trap 'rm -f "$EMPTY"' EXIT
cd /  # no project config

decide() { DCG_CONFIG="$1" dcg test --robot "$2" 2>/dev/null | jq -r .decision; }

fail=0
check() {  # check <want> <command>
  local before after
  before=$(decide "$EMPTY" "$2"); after=$(decide "$CFG" "$2")
  printf '%-5s -> %-5s  %s\n' "$before" "$after" "$(head -1 <<<"$2")"
  [ "$after" = "$1" ] || { echo "  FAIL: want $1"; fail=1; }
}

NL=$'\n'
echo "before -> after  command"
# allowed
check allow "cat > /private/tmp/claude-501/x/scratchpad/a.json <<'EOF'${NL}{}${NL}EOF"
check allow "echo hi > ~/.treehouse/state.json"
check allow "cat <<EOF > ~/.treehouse/pool/slot-1.json${NL}{}${NL}EOF"
check allow "printf x > $HOME/.local/state/iar/run.json"
check allow "cat > ~/.local/state/iar/q/a.md <<'EOF'${NL}x${NL}EOF"
# still blocked
check deny "echo hi > ~/.zshrc"
check deny "cat > ~/.bashrc <<'EOF'${NL}x${NL}EOF"
check deny "echo hi > ~/.treehouse/../.zshrc"
check deny "echo hi > ~/.local/state/other.json"
check deny "echo hi > ~/Library/Application\\ Support/Claude/config.json"
check deny "echo hi > /etc/hosts"
check deny "echo hi > ~/.treehouse/x; rm -rf ~"
check deny "rm -rf ~/.treehouse"

[ $fail = 0 ] && echo PASS || { echo FAIL; exit 1; }

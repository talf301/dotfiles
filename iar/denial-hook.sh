#!/bin/sh
# PermissionDenied hook (W15): append the denied tool call as one JSON line to
# $IAR_STATE_DIR/denials.jsonl (default ~/.local/state/iar). Diagnostic only: iar/ratchet reads it
# to suggest allow rules that Tal picks; no code acts on it alone. Never blocks, prints nothing.
# Registered in ~/.claude/settings.json by iar/settings-patch.jq.
#
#   denial-hook.sh --self-test   (scratch state dir; never touches the real log)

if [ "$1" = --self-test ]; then
  tmp=$(mktemp -d) && trap 'rm -rf "$tmp"' EXIT
  printf '%s' '{"session_id":"s1","cwd":"/w","hook_event_name":"PermissionDenied","tool_name":"Bash","tool_input":{"command":"git push origin x"},"tool_use_id":"t1","reason":"auto mode: push"}' |
    IAR_STATE_DIR=$tmp/state "$0" || { echo "FAIL hook exit"; exit 1; }
  n=$(wc -l <"$tmp/state/denials.jsonl")
  line=$(jq -c '[.session_id, .tool_name, .tool_input.command, .reason, (.ts | test("^[0-9-]+T[0-9:]+Z$"))]' "$tmp/state/denials.jsonl")
  [ "$n" -eq 1 ] && [ "$line" = '["s1","Bash","git push origin x","auto mode: push",true]' ] ||
    { echo "FAIL: $n lines, $line"; exit 1; }
  printf 'not json' | IAR_STATE_DIR=$tmp/state "$0" || { echo "FAIL: bad input exit"; exit 1; }
  [ "$(wc -l <"$tmp/state/denials.jsonl")" -eq 1 ] || { echo "FAIL: bad input wrote a line"; exit 1; }
  echo "ok denial-hook: one JSONL line per denial"
  exit 0
fi

dir=${IAR_STATE_DIR:-$HOME/.local/state/iar}
mkdir -p "$dir" 2>/dev/null
line=$(jq -c --arg ts "$(date -u +%Y-%m-%dT%H:%M:%SZ)" \
  '{ts: $ts, session_id, cwd, tool_name, tool_input, reason}' 2>/dev/null) && [ -n "$line" ] &&
  printf '%s\n' "$line" >>"$dir/denials.jsonl"
exit 0

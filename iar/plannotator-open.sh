#!/usr/bin/env zsh
# One JSON object per open Plannotator session, for /digest and the first mate. Reads Plannotator's session
# records (~/.plannotator/sessions/<pid>.json), keeps the ones whose server process is alive, and adds what it
# opened (annotate target or review), the directory it runs in, and the Claude session that opened it: the first
# `claude` process up its parent chain, matched to `claude agents` by pid, else a session id in the target path.

JQ=/usr/bin/jq
agents=$("$HOME/.local/bin/claude" agents --json --all 2>/dev/null || echo '[]')

for record in $HOME/.plannotator/sessions/*.json(N); do
  pid=$("$JQ" -r '.pid' "$record")
  kill -0 "$pid" 2>/dev/null || continue
  args=$(ps -o command= -p "$pid" | sed 's/^plannotator //')
  cwd=$(lsof -a -p "$pid" -d cwd -Fn 2>/dev/null | sed -n 's/^n//p')
  owner_pid=""
  p=$pid
  for _ in {1..12}; do
    p=$(ps -o ppid= -p "$p" 2>/dev/null | tr -d ' ')
    [[ -z "$p" || "$p" == 1 ]] && break
    if ps -o command= -p "$p" | grep -qE '(^|/)claude( |$)'; then owner_pid=$p; break; fi
  done
  "$JQ" -cn --slurpfile r "$record" --argjson agents "$agents" --arg args "$args" --arg cwd "$cwd" --arg owner "$owner_pid" '
    $r[0] as $s
    | ([$agents[] | select(($owner != "" and ((.pid // "") | tostring) == $owner)
                           or ((.sessionId // "") as $sid | $sid != "" and ($args | contains($sid)))
                           or ((.id // "") as $id | $id != "" and ($args | contains("/" + $id + "/"))))][0]) as $a
    | {pid: $s.pid, mode: $s.mode, url: $s.url, started: $s.startedAt, target: $args, cwd: $cwd,
       session: ($a.name // null), session_id: ($a.sessionId // null), session_kind: ($a.kind // null)}'
done

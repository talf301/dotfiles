#!/usr/bin/env zsh
# Stop hook for /dispatch-bead sessions (loaded with `claude --bg --settings ~/dotfiles/iar/dispatch-settings.json`).
# When a dispatched session ends its turn: post a macOS notification titled with the session name whose body is
# the first line of its last message (QUESTION: … or READY: …), and refresh the sketchybar agents_waiting item.
#
# Question envelope: a first line `QUESTION: <qid> <text>` must name an open question bead (label `question`,
# status open) filed with iar/ask. If it does not, the hook blocks the stop once with the iar/ask feedback.
# It never blocks when stop_hook_active is true, and never when the ledger does not answer in 5 s (it logs to
# $IAR_STATE_DIR/notify-stop.log and lets the stop happen). The notification is sent in every case.
#
#   notify-stop.sh --self-test   (scratch store and fake notifier; never touches the caller's store)

here=${0:A:h} hook=${0:A}

self_test() {
  local tmp=$(mktemp -d) q a c out t0 fail=0
  trap "rm -rf $tmp" EXIT
  export BEADS_DIR=$tmp/.beads IAR_STATE_DIR=$tmp/state IAR_NOTIFY=$tmp/notify
  print -r '#!/bin/sh
cat >/dev/null; echo "$2" >>"$(dirname "$0")/notified"' >$IAR_NOTIFY
  mkdir $tmp/slowbin; print -r '#!/bin/sh
sleep 10' >$tmp/slowbin/br
  chmod +x $IAR_NOTIFY $tmp/slowbin/br
  (cd $tmp && br init --prefix precog >/dev/null 2>&1) || { print "br init failed"; return 1 }
  a=$("$here/ledger" create "asking bead" | /usr/bin/jq -r .id)
  q=$("$here/ledger" create "Q(x): ship it?" -l question,no-swarm | /usr/bin/jq -r .id)
  c=$("$here/ledger" create "Q(x): old" -l question | /usr/bin/jq -r .id)
  "$here/ledger" close $c >/dev/null

  run() {  # run <stop_hook_active> <first line> -> hook stdout
    /usr/bin/jq -nc --argjson act $1 --arg m "$2"$'\nmore text' '{session_id: "t", stop_hook_active: $act, last_assistant_message: $m}' \
      | zsh -f $hook  # -f: no .zshenv, which may reorder PATH
  }
  check() {  # check <name> <block|pass> <stdout>
    local want=$2 got=pass
    [[ $3 == *'"decision":"block"'* ]] && got=block
    [[ $got == block && $3 != *iar/ask* ]] && got=block-without-feedback
    if [[ $got == $want ]]; then print "ok   $1"; else print "FAIL $1: want $want, got $got: $3"; fail=1; fi
  }

  check "malformed: no id blocks"           block "$(run false 'QUESTION: should I ship?')"
  check "malformed: not a question blocks"  block "$(run false "QUESTION: $a ship?")"
  check "malformed: closed question blocks" block "$(run false "QUESTION: $c ship?")"
  check "malformed: unknown id blocks"      block "$(run false 'QUESTION: precog-zzzz ship?')"
  check "valid question passes"             pass  "$(run false "QUESTION: $q ship?")"
  check "READY line passes"                 pass  "$(run false 'READY: done')"
  check "stop_hook_active passes"           pass  "$(run true 'QUESTION: should I ship?')"
  t0=$SECONDS
  out=$(PATH=$tmp/slowbin:$PATH run false "QUESTION: $q ship?")
  check "br unreachable passes"             pass  "$out"
  (( SECONDS - t0 <= 8 )) || { print "FAIL br unreachable took $(( SECONDS - t0 )) s"; fail=1 }
  grep -q "qid=$q ledger exit" $IAR_STATE_DIR/notify-stop.log || { print "FAIL br unreachable: no log line"; fail=1 }
  (( $(wc -l <$tmp/notified) == 8 )) || { print "FAIL notifications: $(wc -l <$tmp/notified) of 8 sent"; fail=1 }
  (( fail )) && { print "notify-stop self-test: FAILED"; return 1 }
  print "notify-stop self-test: ok"
}
[[ $1 == --self-test ]] && { self_test; exit }

input=$(cat)
session_id=$(print -r -- "$input" | /usr/bin/jq -r '.session_id // empty')
transcript=$(print -r -- "$input" | /usr/bin/jq -r '.transcript_path // empty')
message=$(print -r -- "$input" | /usr/bin/jq -r '.last_assistant_message // empty')
active=$(print -r -- "$input" | /usr/bin/jq -r '.stop_hook_active // false')
if [[ -z "$message" && -r "$transcript" ]]; then
  message=$(/usr/bin/jq -rs '[.[] | select(.type == "assistant") | .message.content[]? | select(.type == "text") | .text] | last // ""' "$transcript")
fi
first_line=${${(f)message}[1]}

problem=
if [[ $first_line == QUESTION:* && $active != true ]]; then
  if [[ ! $first_line =~ '^QUESTION:[[:space:]]*(precog-[A-Za-z0-9.]+)' ]]; then
    problem="has no question id"
  else
    qid=${match[1]%.}
    out=$(timeout 5 "$here/ledger" show "$qid" 2>&1); rc=$?
    if (( rc == 0 )); then
      print -r -- "$out" | /usr/bin/jq -e '.status == "open" and ((.labels // []) | index("question") != null)' >/dev/null \
        || problem="names $qid, which is not an open question bead"
    elif [[ $out == *"Issue not found"* ]]; then
      problem="names $qid, which is not an open question bead"
    else
      log=${IAR_STATE_DIR:-$HOME/.local/state/iar}/notify-stop.log
      mkdir -p ${log:h}
      print -r -- "$(date -u +%FT%TZ) session=$session_id qid=$qid ledger exit $rc, stop allowed: ${out//$'\n'/ }" >>$log
    fi
  fi
fi

name=$("$HOME/.local/bin/claude" agents --json 2>/dev/null \
  | /usr/bin/jq -r --arg sid "$session_id" '.[] | select(.sessionId == $sid) | .name // empty')

${IAR_NOTIFY:-/usr/bin/osascript} - "${name:-Claude session}" "${first_line:0:200}" <<'EOF' >/dev/null 2>&1
on run argv
  display notification (item 2 of argv) with title (item 1 of argv)
end run
EOF
command -v sketchybar >/dev/null && sketchybar --trigger agents_changed >/dev/null 2>&1

if [[ -n $problem ]]; then
  /usr/bin/jq -nc --arg r "Your QUESTION line $problem. File it with iar/ask and put the id in your QUESTION line: QUESTION: <qid> <text>." \
    '{decision: "block", reason: $r}'
fi
exit 0

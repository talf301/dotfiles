#!/usr/bin/env bash
# W23 spike: N agents write to one scratch tracker store at the same time.
# Each agent does OPS rounds of: create own issue, comment on a shared issue, add a unique label
# to the shared issue, claim its own issue (status + assignee). No retries, like a real agent call.
# Then it counts failed calls (lock errors) and lost writes (call exited 0, effect missing).
# Usage: tracker-concurrency-test.sh br|bd|bd-server [AGENTS=5] [OPS=12]  (br caps an issue at 64 labels)
# Exit 0 = no lost writes and no failed calls; 1 = otherwise.
set -uo pipefail
TOOL=${1:?br|bd|bd-server}; AGENTS=${2:-5}; OPS=${3:-12}
T=$(mktemp -d)
cleanup() { [ "$TOOL" = bd-server ] && (cd "$T" && bd dolt stop >/dev/null 2>&1); rm -rf "$T"; }
trap cleanup EXIT
cd "$T" || exit 1
case $TOOL in
  br) export BEADS_DIR="$T/.beads"; br init --prefix t -q >/dev/null 2>&1; BIN=br
      q() { br q "$@"; }; comment() { br comments add "$1" "$2"; }
      label() { br label add "$1" -l "$2"; }; claim() { br update "$1" -s in_progress --assignee "$2"; } ;;
  bd|bd-server) git init -q; flags=(--non-interactive --stealth -p t); [ "$TOOL" = bd-server ] && flags+=(--server)
      bd init "${flags[@]}" >init.log 2>&1 || { cat init.log; exit 1; }; BIN=bd
      q() { bd q "$@"; }; comment() { bd comments add "$1" "$2"; }
      label() { bd label add "$1" "$2"; }; claim() { bd update "$1" -s in_progress --assignee "$2"; } ;;
  *) echo "unknown tool $TOOL"; exit 2 ;;
esac
echo "$TOOL $($BIN --version 2>/dev/null | head -1)  agents=$AGENTS ops=$OPS"
SHARED=$(q shared 2>/dev/null | tail -1)
[ -n "$SHARED" ] || { echo "cannot create shared issue"; exit 1; }

agent() {  # $1 = agent number. Logs "op key rc" per call; errors go to err.<n>.
  local a=$1 j id rc
  for ((j = 1; j <= OPS; j++)); do
    id=$(q "a$a-$j" 2>>"err.$a" | tail -1); rc=$?; [ -n "$id" ] || rc=1
    echo "create a$a-$j $rc $id"
    comment "$SHARED" "c-a$a-$j" >/dev/null 2>>"err.$a"; echo "comment c-a$a-$j $?"
    label "$SHARED" "l-a$a-$j" >/dev/null 2>>"err.$a"; echo "label l-a$a-$j $?"
    if [ -n "$id" ]; then claim "$id" "agent$a" >/dev/null 2>>"err.$a"; echo "claim $id $? agent$a"; fi
  done >"log.$a"
}
start=$(date +%s)
for ((a = 1; a <= AGENTS; a++)); do agent "$a" & done
wait
echo "wall time: $(($(date +%s) - start)) s for $((AGENTS * OPS * 4)) calls"

$BIN show "$SHARED" --json >shared.json; $BIN list --all --json --limit 0 >all.json
$BIN comments "$SHARED" --json >comments.json  # bd show --json omits comments
cat err.* 2>/dev/null | grep -v 'permissions 0755' | sort | uniq -c | sort -rn | head -5 | sed 's/^/stderr: /'
python3 - "$T" <<'PY'
import glob, json, sys
t = sys.argv[1]
def first(p):
    d = json.load(open(p))
    if isinstance(d, dict) and "issues" in d: d = d["issues"]
    return d[0] if isinstance(d, list) else d
def rows(p):
    d = json.load(open(p))
    return d["issues"] if isinstance(d, dict) and "issues" in d else d
shared = first(f"{t}/shared.json"); allrows = {r["id"]: r for r in rows(f"{t}/all.json")}
comments = {c.get("text", c.get("body")) for c in rows(f"{t}/comments.json")}
labels = set(shared.get("labels") or []); titles = {r["title"] for r in allrows.values()}
calls = failed = lost = 0
for f in glob.glob(f"{t}/log.*"):
    for line in open(f):
        p = line.split(); op, key, rc = p[0], p[1], int(p[2]); calls += 1
        if rc: failed += 1; continue
        ok = {"create": key in titles, "comment": key in comments, "label": key in labels,
              "claim": key in allrows and allrows[key].get("status") == "in_progress"
                       and allrows[key].get("assignee") == (p[3] if len(p) > 3 else None)}[op]
        if not ok: lost += 1; print("LOST", op, key)
print(f"calls={calls} failed={failed} lost={lost}")
print("RESULT:", "PASS" if not failed and not lost else "FAIL")
sys.exit(1 if failed or lost else 0)
PY

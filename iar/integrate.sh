#!/usr/bin/env bash
# integrate.sh: land an approved bead branch in its PR group branch (precog-e6vh, WORKFLOW.md step 7).
#
#   integrate.sh <bead> <worktree> <slug> <done-when-cmd>   slug: <slug> or u/tfriedman/pr/<slug>
#   integrate.sh --self-test                                scratch repo + scratch bare remote
#
# Under the group lock $IAR_STATE_DIR/locks/pr-<slug>.lock (default ~/.local/state/iar; flock, wait
# IAR_LOCK_WAIT_S, default 30 min):
#   1. fetch the group branch (or the remote default branch if the group does not exist yet)
#   2. merge it into the bead branch (detached; the branch ref moves only after the push)
#   3. the merge changed the tree: run the done-when command again
#   4. merge --no-ff the result into the group, push group and bead branch atomically (never forced)
# The worktree must be clean and on the bead branch. Every exit (also SIGTERM) aborts a merge, checks
# the bead branch out again and frees the lock (it is held by this process's fd 9).
# Exit: 0 landed (prints `integrated <sha>`); 3 conflict with the group (nothing changed: merge the
# group into your branch yourself, resolve, test, review, run again); 4 done-when failed after the
# merge; 5 lock wait timed out; 2 usage; 1 any other error (a rejected push included).
set -euo pipefail

self_test() {
  d=$(mktemp -d); trap 'rm -rf "$d"' EXIT
  export IAR_STATE_DIR="$d/state" IAR_LOCK_WAIT_S=60
  local me; me="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"
  local g="git -c user.name=t -c user.email=t@t -c init.defaultBranch=main"
  $g init -q --bare "$d/remote.git"
  $g clone -q "$d/remote.git" "$d/clone" 2>/dev/null
  printf '1\n2\n3\n4\n5\n6\n7\n8\n9\n10\n' > "$d/clone/f"
  $g -C "$d/clone" add f && $g -C "$d/clone" commit -qm base && $g -C "$d/clone" push -q origin main
  $g -C "$d/clone" push -q origin main:refs/heads/u/tfriedman/pr/grp
  bead() {  # bead <name> <sed-expr on f>: a worktree on branch b-<name> off the group, one commit
    $g -C "$d/clone" fetch -q origin
    $g -C "$d/clone" worktree add -q -b "b-$1" "$d/wt-$1" origin/u/tfriedman/pr/grp
    sed -i '' "$2" "$d/wt-$1/f" && $g -C "$d/wt-$1" commit -qam "$1"
    $g -C "$d/wt-$1" push -q origin "b-$1"
  }
  grp() { $g -C "$d/clone" ls-remote origin refs/heads/u/tfriedman/pr/grp | cut -f1; }
  free() { python3 -c 'import fcntl,sys; fcntl.flock(open(sys.argv[1],"a"),fcntl.LOCK_EX|fcntl.LOCK_NB)' \
    "$IAR_STATE_DIR/locks/pr-grp.lock"; }
  lands() { $g -C "$d/clone" fetch -q origin; $g -C "$d/clone" merge-base --is-ancestor "$1" origin/u/tfriedman/pr/grp; }
  export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
  t() { "$@" || { echo "integrate self-test: FAIL (line ${BASH_LINENO[0]}): $*" >&2; exit 1; }; }
  local log="$d/ran"
  # Two integrations into one group at the same moment, edits in one file (the second auto-merges and
  # reruns done-when): both land in sequence, no rejected push, no lost commit.
  bead a 's/^1$/1a/'; bead b 's/^9$/9b/'
  "$me" pa "$d/wt-a" grp "echo a >> $log" > "$d/out-a" & local pa=$!
  "$me" pb "$d/wt-b" u/tfriedman/pr/grp "echo b >> $log" > "$d/out-b" & local pb=$!
  t wait $pa; t wait $pb
  t lands b-a; t lands b-b; t [ "$(wc -l < "$log")" -eq 1 ]; t free
  t grep -q '^integrated ' "$d/out-a"; t grep -q '^integrated ' "$d/out-b"
  t [ "$($g -C "$d/clone" show origin/u/tfriedman/pr/grp:f | sed -n '1p;9p' | tr '\n' ' ')" = "1a 9b " ]
  for b in a b; do  # back on the bead branch, clean, and the branch (with the group merge) pushed
    t [ "$($g -C "$d/wt-$b" status --porcelain)" = "" ]; t [ "$($g -C "$d/wt-$b" branch --show-current)" = "b-$b" ]
    t [ "$($g -C "$d/clone" rev-parse "b-$b")" = "$($g -C "$d/clone" rev-parse "origin/b-$b")" ]
  done
  # A failing done-when after the merge: group unchanged, bead branch unchanged, lock free.
  bead c 's/^5$/5c/'; bead x 's/^7$/7x/'; bead e 's/^7$/7e/'; t "$me" pe "$d/wt-e" grp true > /dev/null
  local before c0 rc=0; c0=$($g -C "$d/wt-c" rev-parse HEAD); before=$(grp)
  "$me" pc "$d/wt-c" grp false > /dev/null 2>&1 || rc=$?
  t [ $rc -eq 4 ]; t [ "$(grp)" = "$before" ]; t [ "$($g -C "$d/wt-c" rev-parse b-c)" = "$c0" ]; t free
  # A real conflict (x and e both edit line 7): exit 3, nothing changed.
  rc=0; "$me" px "$d/wt-x" grp true > /dev/null 2>&1 || rc=$?
  t [ $rc -eq 3 ]; t [ "$(grp)" = "$before" ]; t [ "$($g -C "$d/wt-x" status --porcelain)" = "" ]; t free
  # SIGTERM while done-when runs (after the merge): lock free, group unchanged, worktree back on its branch.
  "$me" pc "$d/wt-c" grp "sleep 31" > /dev/null 2>&1 & local pk=$!
  for _ in $(seq 100); do pgrep -f "sleep 31" > /dev/null && break; sleep 0.1; done
  t pgrep -f "sleep 31" > /dev/null
  kill -TERM $pk; rc=0; wait $pk 2> /dev/null || rc=$?
  t [ $rc -eq 143 ]; t [ "$(grp)" = "$before" ]; t free
  t [ "$($g -C "$d/wt-c" branch --show-current)" = b-c ]; t [ "$($g -C "$d/wt-c" status --porcelain)" = "" ]
  sleep 0.2; t [ -z "$(pgrep -f 'sleep 31' || true)" ]
  # SIGTERM mid-merge (a merge driver that sleeps): the same.
  $g -C "$d/clone" config merge.slow.driver 'sleep 33; git merge-file %A %O %B'
  mkdir -p "$d/clone/.git/info"; echo 'f merge=slow' > "$d/clone/.git/info/attributes"
  "$me" pc "$d/wt-c" grp true > /dev/null 2>&1 & pk=$!
  for _ in $(seq 100); do pgrep -f "sleep 33" > /dev/null && break; sleep 0.1; done
  t pgrep -f "sleep 33" > /dev/null
  kill -TERM $pk; rc=0; wait $pk 2> /dev/null || rc=$?
  t [ $rc -eq 143 ]; t [ "$(grp)" = "$before" ]; t free; t [ "$($g -C "$d/wt-c" rev-parse b-c)" = "$c0" ]
  t [ "$($g -C "$d/wt-c" branch --show-current)" = b-c ]; t [ "$($g -C "$d/wt-c" status --porcelain)" = "" ]
  sleep 0.2; t [ -z "$(pgrep -f 'sleep 33' || true)" ]
  rm "$d/clone/.git/info/attributes"
  # A held lock: exit 5 after the wait; SIGTERM during the wait ends it at once.
  python3 -c 'import fcntl,sys,time; f=open(sys.argv[1],"a"); fcntl.flock(f,fcntl.LOCK_EX); time.sleep(20)' \
    "$IAR_STATE_DIR/locks/pr-grp.lock" & local holder=$!
  sleep 0.5; rc=0; IAR_LOCK_WAIT_S=1 "$me" pc "$d/wt-c" grp true > /dev/null 2>&1 || rc=$?
  t [ $rc -eq 5 ]
  "$me" pc "$d/wt-c" grp true > /dev/null 2>&1 & pk=$!
  sleep 1; kill -TERM $pk; sleep 1; rc=0; kill -0 $pk 2> /dev/null || rc=1
  t [ $rc -eq 1 ]; rc=0; wait $pk 2> /dev/null || rc=$?; t [ $rc -eq 143 ]; t [ "$(grp)" = "$before" ]
  kill $holder; wait $holder 2> /dev/null || true; t free
  # The conflicting bead x merges the group into its own branch and resolves; meanwhile bead y lands. Then x
  # integrates: the group merge changes its tree again, so done-when runs (it sees x's and y's lines), and x lands.
  $g -C "$d/wt-x" fetch -q origin; $g -C "$d/wt-x" merge -q origin/u/tfriedman/pr/grp > /dev/null 2>&1 || true
  sed -i '' '/^[<=>]\{7\}/d; /^7e$/d' "$d/wt-x/f"; $g -C "$d/wt-x" commit -qam "resolve"
  bead y 's/^3$/3y/'; t "$me" py "$d/wt-y" grp true > /dev/null
  t "$me" px "$d/wt-x" grp "grep -q 3y f && grep -q 7x f && echo x >> $log" > /dev/null; t lands b-x; t free
  t [ "$(tail -1 "$log")" = x ]
  t [ "$($g -C "$d/clone" show origin/u/tfriedman/pr/grp:f | sed -n 7p)" = 7x ]
  echo "integrate self-test: ok"
}

if [ "${1:-}" = --self-test ]; then self_test; exit; fi
[ $# -eq 4 ] || { sed -n '4,5p' "$0" >&2; exit 2; }
bead=$1 wt=$2 slug=${3#u/tfriedman/pr/} dw=$4
group="u/tfriedman/pr/$slug"
git() { command git -C "$wt" "$@" 9>&-; }  # children never inherit the lock fd

branch=$(git symbolic-ref -q --short HEAD) || { echo "integrate: $wt is not on a branch" >&2; exit 1; }
[ -z "$(git status --porcelain)" ] || { echo "integrate: $wt has uncommitted changes" >&2; exit 1; }
orig=$(git rev-parse HEAD)

set -m  # each background job gets its own process group, so cleanup can stop it with its children
kid=
waited() {  # run in the background and wait: a signal is handled at once, not when the child ends
  "$@" & kid=$!
  local rc=0; wait $kid || rc=$?; kid=; return $rc
}
cleanup() {
  set +e  # every step runs, whatever the one before did
  if [ -n "$kid" ]; then kill -TERM -- "-$kid" 2> /dev/null; wait "$kid" 2> /dev/null; fi
  git rev-parse -q --verify MERGE_HEAD > /dev/null && git merge --abort
  # The worktree was clean at the start: put the bead branch back as it was.
  git checkout -q -f "$branch" && git reset -q --hard && git clean -qfd || true
}
trap cleanup EXIT
trap 'exit 143' TERM INT HUP

locks="${IAR_STATE_DIR:-$HOME/.local/state/iar}/locks"
mkdir -p "$locks"
exec 9>> "$locks/pr-$slug.lock"
waited python3 -c '
import fcntl, sys, time
end = time.monotonic() + float(sys.argv[1])
while True:
    try:
        fcntl.flock(9, fcntl.LOCK_EX | fcntl.LOCK_NB)  # the lock stays with the shell: same open file
        break
    except BlockingIOError:
        if time.monotonic() > end:
            sys.exit(5)
        time.sleep(1)' "${IAR_LOCK_WAIT_S:-1800}" || { echo "integrate: $group is locked by another integrate; ask" >&2; exit 5; }

# 1. The group head, or the default branch for the first member.
if git ls-remote --exit-code -q origin "refs/heads/$group" > /dev/null; then
  base=$group
else
  base=$(git ls-remote --symref origin HEAD | sed -n 's|^ref: refs/heads/\(.*\)\tHEAD$|\1|p')
fi
git fetch -q origin "+refs/heads/$base:refs/remotes/origin/$base"
# 2. Merge it into the bead branch, detached.
git checkout -q --detach
if ! waited git merge -q --no-edit -m "Merge $base into $branch" "origin/$base" > /dev/null; then
  git merge --abort 2> /dev/null || true
  echo "integrate: $branch conflicts with $base; merge it into your branch, resolve, test, review, run again" >&2
  exit 3
fi
merged=$(git rev-parse HEAD)
# 3. A changed tree is tested again.
if [ "$(git rev-parse "$orig^{tree}")" != "$(git rev-parse HEAD^{tree})" ]; then
  done_when() { cd "$wt" && exec bash -c "$dw"; } 9>&-
  waited done_when || { echo "integrate: done-when failed after merging $base" >&2; exit 4; }
fi
# 4. One merge commit per bead in the group; both branches move together or not at all.
git checkout -q --detach "origin/$base"
waited git merge -q --no-ff --no-edit -m "Merge $branch ($bead) into $group" "$merged" > /dev/null
sha=$(git rev-parse HEAD)
git push -q --atomic origin "$merged:refs/heads/$branch" "$sha:refs/heads/$group" \
  || { echo "integrate: push to $group rejected; report it, never force-push" >&2; exit 1; }
git update-ref "refs/heads/$branch" "$merged" "$orig"
git fetch -q origin "+refs/heads/$group:refs/remotes/origin/$group"
echo "integrated $sha"

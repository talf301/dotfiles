#!/usr/bin/env bash
# iar-review.sh — run ONE independent codex review of a bead's worktree, foreground.
#
# Extracted from spawn_reviewer() in swarm-supervisor.sh for the solo interactive flow
# (an interactive Claude session implements, then calls this and reads the verdict).
# Same invariants as the swarm wrapper, all learned the hard way:
#   - the read-only codex sandbox can't take br/CASS file locks, so WE capture the
#     acceptance criteria into a file the reviewer cats (never runs br itself)
#   - criteria are passed as a FILE PATH, never injected text (quoting breaks)
#   - stdin is closed (< /dev/null) or codex hangs reading it
#   - codex echoes the prompt, which names every verdict — parse the LAST match
#   - label + comment writes happen here, outside the sandbox
#
# Usage: iar-review.sh <bead-id> <worktree> <base-sha>
# Exit:  0 = APPROVED   1 = CHANGES REQUESTED   2 = NEEDS HUMAN   3 = no verdict
set -euo pipefail

id="${1:?bead id}"; wt="${2:?worktree}"; base="${3:?base sha}"
export BEADS_DIR="${BEADS_DIR:-/Users/tal/.local/share/beads/precog/.beads}"
LOG_DIR="${LOG_DIR:-/tmp/swarm-logs}"
ACTOR="${ACTOR:-tal-solo}"
mkdir -p "$LOG_DIR"

critf="$LOG_DIR/crit-$id.txt"
rlog="$LOG_DIR/review-$id.log"
parent=$(br show "$id" --json 2>/dev/null | jq -r '.[0].parent // empty' || true)
{ br show "$id"; if [ -n "$parent" ]; then echo; echo "=== PARENT SPECIFICATION ($parent) ==="; br show "$parent"; fi; } 2>/dev/null | tr -d '\r' > "$critf"

cd "$wt"
codex exec -m gpt-5.6-sol -s read-only "You are the independent REVIEWER for bead $id (iar-worker:$id). Do NOT run br and do NOT search CASS (the read-only sandbox blocks their locks). Do NOT modify anything. Read the acceptance criteria and the parent specification with: cat $critf . Review ONLY the fixed range $base..HEAD: run git diff $base HEAD and read the changed files. You cannot run pytest under read-only; use non-writing checks (python3 -c import, ast.parse) and the implementer stated results. Judge against the criteria and repo standards (correctness, security, tests, maintainability, scope creep). List findings (file:line, impact, concrete fix), then print exactly one final line, choosing ONE of three verdicts: VERDICT: APPROVED  or  VERDICT: CHANGES REQUESTED  or  VERDICT: NEEDS HUMAN . Use NEEDS HUMAN when the remaining gap CANNOT be closed by an automated headless implementer for a valid structural reason — it requires credentials, VPN, real hardware, a cluster/live run, or a human decision the sandbox cannot provide — so more fix cycles would only re-hit the same dead end; in that case make your findings state exactly what human action is required. Prefer NEEDS HUMAN over CHANGES REQUESTED whenever the fix is a human-only action rather than a code change the implementer could make.

$(cat "$HOME/dotfiles/iar/reviewer-scope.md")" < /dev/null > "$rlog" 2>&1 || true

v=$(grep -oE "VERDICT: (APPROVED|CHANGES REQUESTED|NEEDS HUMAN)" "$rlog" | tail -1 || true)
case "$v" in
  "VERDICT: APPROVED")
    br update "$id" --add-label iar:approved --remove-label iar:awaiting-review --actor "$ACTOR" >/dev/null 2>&1 || true; rc=0 ;;
  "VERDICT: CHANGES REQUESTED")
    br update "$id" --add-label iar:changes-requested --remove-label iar:awaiting-review --actor "$ACTOR" >/dev/null 2>&1 || true; rc=1 ;;
  "VERDICT: NEEDS HUMAN")
    br update "$id" --add-label needs-human --remove-label iar:awaiting-review --actor "$ACTOR" >/dev/null 2>&1 || true; rc=2 ;;
  *)
    echo "iar-review: no VERDICT line found — see $rlog" >&2; exit 3 ;;
esac
[ -n "$v" ] && br comments add "$id" --actor "reviewer:sol" -m "$v (reviewer:sol) — full findings in $rlog" >/dev/null 2>&1 || true
echo "$v — full findings in $rlog"
exit "$rc"

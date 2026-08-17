#!/usr/bin/env bash
# swarm-supervisor.sh — self-assigning IAR swarm as a mechanical phase machine.
#
# Non-LLM poller. Idle cost = a couple `br` calls per tick (no idle agent, no tokens at rest).
# Each IAR phase is its OWN short-lived process; the supervisor never runs the whole loop in one
# session (that can't work headless — a `claude -p` session dies when the model yields, so it can't
# wait on an async reviewer). Instead the supervisor drives phases off Beads LABELS it can read:
#
#   (claimed, no iar: label)   -> spawn IMPLEMENTER  -> agent sets  iar:awaiting-review
#   iar:awaiting-review        -> spawn REVIEWER     -> wrapper sets iar:approved | iar:changes-requested
#   iar:changes-requested      -> spawn FIXER        -> agent sets  iar:awaiting-review
#   iar:approved               -> DONE (human merges; no auto-merge in v1)
#   needs-human                -> HOLD (the escalation gate)
#
# Why labels are the control signal: `br` reads are proven and atomic. Human-readable context
# (review request / verdict / escalation) is recorded as `br comments` ON THE BEAD — no Agent Mail,
# no second store to keep in sync (a wrong mail read returns empty == "no review yet" == respawn
# forever). The reviewer runs `codex exec -s read-only`, which cannot write the beads DB, so its
# verdict LABEL and record COMMENT are written by its launch wrapper (outside the sandbox) from the
# reviewer's OWN stdout VERDICT line.
#
# Safety rails: per-phase attempt cap + fix-cycle cap -> needs-human (stops exit-0-but-incomplete
# respawn loops). Supervisor creates the worktree/branch itself and pins base_sha, so agents work in
# isolation and can't edit live tooling in place. Nothing irreversible (merge) happens unattended.
#
# Usage:
#   ./swarm-supervisor.sh            # loop forever
#   ./swarm-supervisor.sh once       # single tick (good for cron)
#   DRY_RUN=1 ./swarm-supervisor.sh once   # show intended actions, mutate no real state (self-check)
#
# Requires: br, jq, git, claude, codex.  bash 3.2-safe (no mapfile/assoc-arrays) for stock macOS.
set -euo pipefail

export BEADS_DIR="${BEADS_DIR:-/Users/tal/.local/share/beads/precog/.beads}"
REPO="${REPO:-/Users/tal/Documents/precog}"
MAX_INFLIGHT="${MAX_INFLIGHT:-3}"          # cap concurrently-owned beads
POLL_SECS="${POLL_SECS:-60}"
LOG_DIR="${LOG_DIR:-/tmp/swarm-logs}"
ACTOR="${ACTOR:-swarm-$(hostname -s)}"     # claim identity — STABLE per host (no $$), so a restarted poller reclaims its own in-flight beads instead of orphaning them. Single poller per host (the `swarm` toggle enforces one); set ACTOR explicitly to run more.
PHASE_CAP="${PHASE_CAP:-2}"                # max spawn attempts in one phase before needs-human
FIX_CAP="${FIX_CAP:-2}"                    # max review->changes cycles before needs-human
DRY_RUN="${DRY_RUN:-0}"
mkdir -p "$LOG_DIR"

# ---- tiny state helpers (throwaway counters under $LOG_DIR, keyed by bead) -------------------
val()      { cat "$1" 2>/dev/null || echo 0; }
inc()      { echo $(( $(val "$1") + 1 )) > "$1"; }
over_cap() { [ "$(val "$1")" -gt "$2" ]; }   # fix-cycles: allow N cycles, escalate on N+1
maxed()    { [ "$(val "$1")" -ge "$2" ]; }   # attempts: escalate once N failed tries reached

inflight()   { br list --assignee "$ACTOR" --status in_progress --json 2>/dev/null | jq '.issues | length'; }
proc_alive() { pgrep -f "iar-worker:$1" >/dev/null 2>&1; }   # any phase process for this bead

# Current phase = derived purely from the bead's labels (single source of truth).
phase_of() {
  local L; L=$(br list --id "$1" --json 2>/dev/null | jq -r '.issues[0].labels // [] | join(" ")')
  case " $L " in
    *" needs-human "*)            echo human;;
    *" iar:approved "*)           echo approved;;
    *" iar:changes-requested "*)  echo changes;;
    *" iar:awaiting-review "*)    echo review;;
    *)                            echo impl;;
  esac
}

# Sets globals WT (worktree path) and BASE (pinned base sha); creates the worktree/branch first time.
load_worktree() {
  local id="$1" branch="u/tfriedman/iar-$1" basef="$LOG_DIR/base-$1"
  WT="$REPO/.claude/worktrees/iar-$1"
  if [ -d "$WT" ]; then
    BASE=$(val "$basef")
    [ "$BASE" = 0 ] && BASE=$(git -C "$REPO" rev-parse HEAD)
    return 0   # explicit: a bare `return` here would propagate the `&&` test's exit 1 and set -e would kill the caller
  fi
  BASE=$(git -C "$REPO" rev-parse HEAD)
  [ "$DRY_RUN" = 1 ] && return 0
  git -C "$REPO" worktree add -b "$branch" "$WT" "$BASE" >/dev/null 2>&1 \
    || git -C "$REPO" worktree add "$WT" "$branch" >/dev/null 2>&1   # branch may already exist
  echo "$BASE" > "$basef"
  return 0
}

escalate_stuck() {  # $1 bead  $2 why
  echo "[$(date +%T)] $1 STUCK at '$2' (cap hit) -> needs-human"
  [ "$DRY_RUN" = 1 ] && return
  # Post the REAL reason so swarm-status.sh's "latest comment" doesn't misreport a prior
  # review/verdict comment as the escalation. Supervisor-authored: id/phase are quote-safe.
  br comments add "$1" --actor "$ACTOR" \
    -m "ESCALATION (supervisor) — hit the cap at phase '$2' (phase_cap=$PHASE_CAP, fix_cap=$FIX_CAP); no more auto-attempts. Inspect $LOG_DIR/*-$1.log and the worktree, then to resume: br update $1 --remove-label needs-human (and reset the phase counter in $LOG_DIR if a cap put it here); to resolve: br close $1." >/dev/null 2>&1 || true
  br update "$1" --add-label needs-human --actor "$ACTOR" >/dev/null 2>&1 || true
}

# IMPLEMENTER / FIXER: full-access claude in the worktree; sets its own iar:awaiting-review on exit.
spawn_impl() {  # $1 bead  $2 role(implement|fix)
  local id="$1" role="${2:-implement}"; load_worktree "$id"
  if [ "$DRY_RUN" = 1 ]; then echo "  DRY: would spawn ${role} for $id in $WT"; return; fi
  local extra=""
  [ "$role" = fix ] && extra="You are FIXING a prior review. First read the reviewer's findings in $LOG_DIR/review-$id.log (and 'br comments $id' for the recorded verdict), then address only actionable findings. "
  ( cd "$WT" && claude -p "You are the IMPLEMENTER for bead $id (iar-worker:$id). ${extra}Work ONLY inside this worktree ($WT); never edit anything outside it. Read the bead ('br show $id'), its acceptance criteria, repo instructions, and relevant CASS. Implement the smallest complete solution. Run tests only with: iar-run-tests.sh $WT <pytest-args> (never bazel, never bare pytest). Commit all task changes to the current branch. Then record a review-request note on the bead: WRITE a file /tmp/iar-review-$id.txt containing base SHA $BASE, the head SHA, the acceptance criteria, a concise change summary, the exact checks you ran and their results, and any known risks; then run: br comments add $id --actor $ACTOR -f /tmp/iar-review-$id.txt . (Write the file with your editor — do NOT pack this prose into a shell -m argument, quoting will bite you.) Finally run EXACTLY: br update $id --add-label iar:awaiting-review --remove-label iar:changes-requested --actor $ACTOR . Do not set other labels; do not close the bead; then stop. If you are blocked or need a human decision, record it on the bead: WRITE a file /tmp/iar-esc-$id.txt whose first line is 'ESCALATION — <your exact question and what input you need>', then a line 'To decide+resume: br update $id --notes \"<decision>\" then br update $id --remove-label needs-human', then a line 'To resolve directly: br close $id'; then run: br comments add $id --actor $ACTOR -f /tmp/iar-esc-$id.txt . (Write the file with your editor — do NOT pack prose into -m.) Then run: br update $id --add-label needs-human --actor $ACTOR (leave it in_progress — do NOT release the claim). If the block is STRUCTURAL or needs a rethink rather than a quick factual decision, ALSO run: br update $id --add-label needs-fable --actor $ACTOR (this flags it so the human can open a fresh Fable session on it). Then stop." \
      --permission-mode bypassPermissions --add-dir "$WT" >"$LOG_DIR/impl-$id.log" 2>&1 ) &
}

# REVIEWER: read-only codex/Sol. The read-only sandbox blocks the file locks br + CASS need, so the
# reviewer must NOT touch them — the supervisor (full access) captures the acceptance criteria and
# injects them; the reviewer judges the git diff + files only. codex echoes the prompt (names both
# verdicts) so we stamp the LAST verdict line; stdin is closed or codex hangs reading it; nohup
# detaches the wrapper so a tick exiting can't orphan it; the marker keeps it matchable until the
# label is stamped; the label write runs in the wrapper (outside the sandbox), so it can mutate beads.
spawn_reviewer() {  # $1 bead
  local id="$1"; load_worktree "$id"
  if [ "$DRY_RUN" = 1 ]; then echo "  DRY: would spawn reviewer for $id ($BASE..HEAD in $WT)"; return; fi
  local critf="$LOG_DIR/crit-$id.txt"; br show "$id" 2>/dev/null | tr -d '\r' > "$critf"
  nohup bash -c '
    id="$1"; wt="$2"; base="$3"; actor="$4"; rlog="$5"; critf="$6"
    cd "$wt" || exit 1
    codex exec -m gpt-5.6-sol -s read-only "You are the independent REVIEWER for bead $id (iar-worker:$id). Do NOT run br and do NOT search CASS (the read-only sandbox blocks their locks). Do NOT modify anything. Read the acceptance criteria with: cat $critf . Review ONLY the fixed range $base..HEAD: run git diff $base HEAD and read the changed files. You cannot run pytest under read-only; use non-writing checks (python3 -c import, ast.parse) and the implementer stated results. Judge against the criteria and repo standards (correctness, security, tests, maintainability, scope creep). List findings (file:line, impact, concrete fix), then print exactly one final line: VERDICT: APPROVED  or  VERDICT: CHANGES REQUESTED." < /dev/null > "$rlog" 2>&1 || true
    # Take the LAST verdict line: codex echoes the prompt (which names both verdicts), so the
    # real answer is always the final match. stdin closed above or codex hangs reading it.
    v=$(grep -oE "VERDICT: (APPROVED|CHANGES REQUESTED)" "$rlog" | tail -1)
    if [ "$v" = "VERDICT: APPROVED" ]; then
      br update "$id" --add-label iar:approved --remove-label iar:awaiting-review --actor "$actor" >/dev/null 2>&1 || true
    elif [ "$v" = "VERDICT: CHANGES REQUESTED" ]; then
      br update "$id" --add-label iar:changes-requested --remove-label iar:awaiting-review --actor "$actor" >/dev/null 2>&1 || true
    fi
    # Record the verdict on the bead (durable human/fixer-readable provenance; replaces the old mail).
    [ -n "$v" ] && br comments add "$id" --actor "reviewer:sol" -m "$v (reviewer:sol) — full findings in $rlog" >/dev/null 2>&1 || true
    : # iar-worker:'"$id"' — marker: keep wrapper matchable until label stamped
  ' _ "$id" "$WT" "$BASE" "$ACTOR" "$LOG_DIR/review-$id.log" "$critf" >"$LOG_DIR/revwrap-$id.log" 2>&1 &
}

# Advance ONE owned bead by at most one phase step. Never acts while a worker for it is alive.
advance() {  # $1 bead
  local id="$1" cur; cur=$(phase_of "$id")
  proc_alive "$id" && { [ "$DRY_RUN" = 1 ] && echo "  $id: phase=$cur (worker running)"; return 0; }

  if [ "$DRY_RUN" = 1 ]; then echo "  $id: phase=$cur -> would spawn/advance"; return 0; fi

  local pf="$LOG_DIR/phase-$id" af="$LOG_DIR/attempts-$id" cf="$LOG_DIR/fixcycles-$id"
  if [ "$cur" != "$(val "$pf")" ]; then           # phase transition -> reset attempt counter
    echo "$cur" > "$pf"; echo 0 > "$af"
    [ "$cur" = changes ] && inc "$cf"             # count each new review->changes cycle
  fi

  case "$cur" in
    human|approved) return 0 ;;                    # hold / done
    review)
      maxed "$af" "$PHASE_CAP" && { escalate_stuck "$id" review; return 0; }
      inc "$af"; echo "[$(date +%T)] $id -> reviewer"; spawn_reviewer "$id" ;;
    changes)
      over_cap "$cf" "$FIX_CAP" && { escalate_stuck "$id" fix-cycles; return 0; }
      maxed "$af" "$PHASE_CAP" && { escalate_stuck "$id" fix; return 0; }
      inc "$af"; echo "[$(date +%T)] $id -> fixer (cycle $(val "$cf"))"; spawn_impl "$id" fix ;;
    impl)
      maxed "$af" "$PHASE_CAP" && { escalate_stuck "$id" implement; return 0; }
      inc "$af"; echo "[$(date +%T)] $id -> implementer"; spawn_impl "$id" implement ;;
  esac
}

tick() {
  # 1. Advance every bead this supervisor already owns.
  br list --assignee "$ACTOR" --status in_progress --json 2>/dev/null | jq -r '.issues[].id' \
  | while IFS= read -r id; do [ -n "$id" ] && advance "$id"; done

  # 2. Claim new ready work up to the cap, then let advance() kick off its implement phase.
  br scheduler --format json 2>/dev/null \
    | jq -r '.recommendations[] | select(.evidence.stale_claim.classification=="unassigned") | .issue.id' \
  | while IFS= read -r id; do
      [ -z "$id" ] && continue
      if [ "$(inflight)" -ge "$MAX_INFLIGHT" ]; then echo "[$(date +%T)] at capacity ($MAX_INFLIGHT), holding"; break; fi
      if [ "$DRY_RUN" = 1 ]; then echo "  DRY: would claim $id -> implement"; continue; fi
      if br update "$id" --claim --actor "$ACTOR" >/dev/null 2>&1 \
         && [ "$(br list --id "$id" --json 2>/dev/null | jq -r '.issues[0].assignee // empty')" = "$ACTOR" ]; then
        echo "[$(date +%T)] claimed $id"; advance "$id"
      else
        echo "[$(date +%T)] lost race for $id, skipping"
      fi
    done

  # 3. Crash-recovery visibility (opt-in reclaim; a "stale" claim may be a slow-but-alive agent).
  br scheduler --format json 2>/dev/null \
    | jq -r '.recommendations[].evidence.stale_claim | select(.recommended_action=="reclaim")
             | "  STALE: consider reclaim (assignee=\(.assignee), age=\(.updated_age_minutes)m)"' 2>/dev/null || true
}

if [ "${1:-loop}" = once ]; then
  tick
else
  echo "[supervisor] actor=$ACTOR cap=$MAX_INFLIGHT poll=${POLL_SECS}s phase_cap=$PHASE_CAP fix_cap=$FIX_CAP"
  # A transient null br/jq response must not kill the loop: `|| ...` keeps set -e from exiting and
  # skips one tick. State is re-derived from labels each tick, so a skipped tick is harmless.
  while true; do tick || echo "[$(date +%T)] tick error (transient br/jq?), skipping to next poll" >&2; sleep "$POLL_SECS"; done
fi

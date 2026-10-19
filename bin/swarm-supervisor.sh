#!/usr/bin/env bash
# swarm-supervisor.sh — self-assigning IAR swarm as a mechanical phase machine.
#
# Non-LLM poller. Idle cost = a couple `br` calls per tick (no idle agent, no tokens at rest).
# Each IAR phase is its OWN short-lived process; the supervisor never runs the whole loop in one
# session (that can't work headless — a `claude -p` session dies when the model yields, so it can't
# wait on an async reviewer). Instead the supervisor drives phases off Beads LABELS it can read:
#
#   (claimed, no iar: label)   -> spawn IMPLEMENTER  -> agent sets  iar:awaiting-review
#   iar:awaiting-review        -> spawn REVIEWER     -> wrapper sets iar:approved | iar:changes-requested | needs-human
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
MAIN="${MAIN:-main}"                        # trunk that PR branches (and standalone beads) branch off of
MAX_INFLIGHT="${MAX_INFLIGHT:-5}"          # cap concurrently-owned beads
POLL_SECS="${POLL_SECS:-15}"               # each phase change waits up to one poll; a tick is only a few br calls
LOG_DIR="${LOG_DIR:-/tmp/swarm-logs}"
ACTOR="${ACTOR:-swarm-precog}"             # claim identity — pinned, NOT hostname-derived: EC2 private IPs (hence `hostname -s`) roll on stop/start, which would mint a new actor and orphan in-flight claims. A fixed name means a restarted poller reclaims its own beads. One poller per DB (the `swarm` toggle enforces one); set ACTOR explicitly to run more.
PR_BRANCH_PREFIX="${PR_BRANCH_PREFIX:-u/tfriedman/pr}"  # PR-group integration branches; u/<user>/ prefix per repo convention so they never collide with anyone else's branches
PHASE_CAP="${PHASE_CAP:-2}"                # max spawn attempts in one phase before needs-human
FIX_CAP="${FIX_CAP:-2}"                    # max review->changes cycles before needs-human
PHASE_TIMEOUT="${PHASE_TIMEOUT:-5400}"     # seconds a phase process may run before it is killed; the retry then counts against PHASE_CAP
DRY_RUN="${DRY_RUN:-0}"

# Single-instance guard: two pollers race the same beads and double-spawn workers (fixer A and
# fixer B both writing one worktree). `swarm on` and the TUI toggle check this too, but a bare
# `nohup swarm-supervisor.sh &` bypasses both — enforce it here. DRY_RUN passes through: a dry
# tick mutates nothing and is the self-check path.
if [ "$DRY_RUN" != 1 ]; then
  _others="$(pgrep -f 'swarm-supervisor\.sh' | grep -vw "$$" || true)"
  if [ -n "$_others" ]; then
    echo "swarm-supervisor: another poller is already running (pid $(printf '%s\n' $_others | head -1)); refusing to start a second one" >&2
    exit 1
  fi
fi
mkdir -p "$LOG_DIR"

# ---- tiny state helpers (throwaway counters under $LOG_DIR, keyed by bead) -------------------
val()      { cat "$1" 2>/dev/null || echo 0; }
inc()      { echo $(( $(val "$1") + 1 )) > "$1"; }
over_cap() { [ "$(val "$1")" -gt "$2" ]; }   # fix-cycles: allow N cycles, escalate on N+1
maxed()    { [ "$(val "$1")" -ge "$2" ]; }   # attempts: escalate once N failed tries reached

inflight()   { br list --assignee "$ACTOR" --status in_progress --json 2>/dev/null | jq '.issues | length'; }
proc_alive() { pgrep -f "iar-worker:$1|iar-review\.sh $1 " >/dev/null 2>&1; }   # any phase process for this bead
# no_swarm: a bead the swarm must never implement — either explicitly labelled `no-swarm` (by
# spec-to-beads) or any wayfinder:<phase> map bead (design/grilling/research/prototype decisions
# owned by the human; an unassigned wayfinder bead was claimed the moment its blocker closed —
# assignment must not be the only guard on a map).
# Read LIVE from `br list --id` (not `br scheduler`'s snapshot, whose labels can lag a just-applied
# label) so it's authoritative at both claim time and advance time — labelling an in-flight bead
# `no-swarm` cleanly ejects it from the pipeline.
no_swarm()   { br list --id "$1" --json 2>/dev/null | jq -e '.issues[0].labels // [] | any(.[]; . == "no-swarm" or startswith("wayfinder:"))' >/dev/null 2>&1; }

# ---- PR-group helpers: a bead labelled pr:<slug> bases off the PR branch, not main ---------------
# A PR-group is the integration unit — one or more beads that ship as one PR — carried as a `pr:<slug>`
# label (orthogonal to epics, which are parent-child organization and do NOT drive integration).
# The integration branch is $PR_BRANCH_PREFIX/<slug> (i.e. u/tfriedman/pr/<slug>).
# prgroup_of: the pr:<slug> a bead belongs to, or '' (a bead with no pr: label is its own 1-bead PR,
# branched off main, terminal for a human PR — i.e. exactly the old behavior).
prgroup_of() { br list --id "$1" --json 2>/dev/null | jq -r '.issues[0].labels // [] | map(select(startswith("pr:"))) | .[0] // empty' | sed 's/^pr://'; }
# prgroup_base: the sha a bead should be cut from — PR branch HEAD if it has a group (creating the
# branch off $MAIN on the first member), else main HEAD (standalone: unchanged).
prgroup_base() {
  local slug="$1" pb
  [ -z "$slug" ] && { git -C "$REPO" rev-parse HEAD; return 0; }
  pb="$PR_BRANCH_PREFIX/$slug"
  if git -C "$REPO" show-ref --verify -q "refs/heads/$pb"; then
    git -C "$REPO" rev-parse "$pb"
  else
    [ "$DRY_RUN" = 1 ] || git -C "$REPO" branch "$pb" "$MAIN" >/dev/null 2>&1
    git -C "$REPO" rev-parse "$pb" 2>/dev/null || git -C "$REPO" rev-parse "$MAIN"
  fi
}

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
  PRG=$(prgroup_of "$id")                       # '' for a standalone bead; global for callers
  if [ -d "$WT" ]; then
    BASE=$(val "$basef")
    [ "$BASE" = 0 ] && BASE=$(prgroup_base "$PRG")
    return 0   # explicit: a bare `return` here would propagate the `&&` test's exit 1 and set -e would kill the caller
  fi
  BASE=$(prgroup_base "$PRG")                    # PR-group members cut from the PR branch HEAD (sees merged siblings); standalone from main
  [ "$DRY_RUN" = 1 ] && { echo "  DRY: $id base=${PRG:+$PR_BRANCH_PREFIX/$PRG @ }$BASE"; return 0; }
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
  date +%s > "$LOG_DIR/started-$id"
  local extra=""
  [ "$role" = fix ] && extra="You are FIXING a prior review. First read the reviewer's findings in $LOG_DIR/review-$id.log (and 'br comments $id' for the recorded verdict), then address only actionable findings. "
  ( cd "$WT" && claude -p "You are the IMPLEMENTER for bead $id (iar-worker:$id). ${extra}Work ONLY inside this worktree ($WT); never edit anything outside it. Read the bead ('br show $id'), its acceptance criteria, repo instructions, and relevant CASS. Implement the smallest complete solution. Run tests only with: iar-run-tests.sh $WT <pytest-args> (never bazel, never bare pytest). Commit all task changes to the current branch. Then record a review-request note on the bead: WRITE a file /tmp/iar-review-$id.txt containing base SHA $BASE, the head SHA, the acceptance criteria, a concise change summary, the exact checks you ran and their results, and any known risks; then run: br comments add $id --actor $ACTOR -f /tmp/iar-review-$id.txt . (Write the file with your editor — do NOT pack this prose into a shell -m argument, quoting will bite you.) Finally run EXACTLY: br update $id --add-label iar:awaiting-review --remove-label iar:changes-requested --actor $ACTOR . Do not set other labels; do not close the bead; then stop. If you are blocked or need a human decision, record it on the bead: WRITE a file /tmp/iar-esc-$id.txt whose first line is 'ESCALATION — <your exact question and what input you need>', then a line 'To decide+resume: br update $id --notes \"<decision>\" then br update $id --remove-label needs-human', then a line 'To resolve directly: br close $id'; then run: br comments add $id --actor $ACTOR -f /tmp/iar-esc-$id.txt . (Write the file with your editor — do NOT pack prose into -m.) Then run: br update $id --add-label needs-human --actor $ACTOR (leave it in_progress — do NOT release the claim). If the block is STRUCTURAL or needs a rethink rather than a quick factual decision, ALSO run: br update $id --add-label needs-fable --actor $ACTOR (this flags it so the human can open a fresh Fable session on it). Then stop.

$(cat "$HOME/dotfiles/iar/scope-self-review.md")" \
      --permission-mode bypassPermissions --add-dir "$WT" >"$LOG_DIR/impl-$id.log" 2>&1 ) &
}

# REVIEWER: iar-review.sh (shared with the solo /next-bead flow) runs read-only codex and stamps the
# verdict label + comment outside the sandbox. nohup detaches it so a tick exiting can't orphan it;
# proc_alive matches its command line until the label is stamped.
spawn_reviewer() {  # $1 bead
  local id="$1"; load_worktree "$id"
  if [ "$DRY_RUN" = 1 ]; then echo "  DRY: would spawn reviewer for $id ($BASE..HEAD in $WT)"; return; fi
  date +%s > "$LOG_DIR/started-$id"
  ACTOR="$ACTOR" LOG_DIR="$LOG_DIR" nohup iar-review.sh "$id" "$WT" "$BASE" >"$LOG_DIR/revwrap-$id.log" 2>&1 &
}

# INTEGRATE: an approved PR-group bead's done-condition is its code merged into the PR branch (the
# cheap, local equivalent of "the PR merged" — which is already what closing a bead requires).
# Merge happens in a dedicated PR worktree so the user's main checkout/HEAD is never touched.
# Order is load-bearing: merge succeeds -> THEN close (close is what unblocks dependents, and a
# dependent must never be cut from a PR branch missing this bead). Conflict -> do NOT close.
# ponytail: one mkdir-lock per PR-group serializes concurrent integrates; a real queue only if a
# group ever gets wide enough to actually contend.
integrate_bead() {  # $1 bead  $2 pr-slug
  local id="$1" slug="$2" pb="$PR_BRANCH_PREFIX/$2" bb="u/tfriedman/iar-$1"
  local pwt="$REPO/.claude/worktrees/pr-$2" lock="$LOG_DIR/pr-$2.lock"
  local tries=0
  while ! mkdir "$lock" 2>/dev/null; do tries=$((tries+1)); [ "$tries" -gt 120 ] && { echo "[$(date +%T)] $id integrate: lock busy, retry next tick"; return 0; }; sleep 1; done
  [ -d "$pwt" ] || git -C "$REPO" worktree add "$pwt" "$pb" >/dev/null 2>&1
  git -C "$pwt" checkout "$pb" >/dev/null 2>&1
  if git -C "$pwt" merge --no-ff -m "integrate $id into $pb" "$bb" >/dev/null 2>&1; then
    br close "$id" --reason "integrated into $pb" --actor "$ACTOR" >/dev/null 2>&1
    echo "[$(date +%T)] $id integrated into $pb and closed"
  else
    git -C "$pwt" merge --abort >/dev/null 2>&1
    br comments add "$id" --actor "$ACTOR" -m "MERGE CONFLICT integrating $bb into $pb — a sibling bead in this PR-group likely edited the same files without a blocks edge. Resolve in $pwt, then close; or add the missing blocks edge and re-run. Bead left approved (not closed) so dependents stay blocked." >/dev/null 2>&1 || true
    br update "$id" --add-label needs-human --actor "$ACTOR" >/dev/null 2>&1 || true
    echo "[$(date +%T)] $id MERGE CONFLICT into $pb -> needs-human"
  fi
  rmdir "$lock" 2>/dev/null || true
}

# Advance ONE owned bead by at most one phase step. Never acts while a worker for it is alive.
advance() {  # $1 bead
  local id="$1" cur; cur=$(phase_of "$id")
  if proc_alive "$id"; then
    [ "$DRY_RUN" = 1 ] && echo "  $id: phase=$cur (worker running)"
    # A finished `claude -p` can fail to exit and hold the bead forever. Kill it once over the
    # limit; the next tick respawns the same phase, so PHASE_CAP still bounds retries.
    local sf="$LOG_DIR/started-$id"
    if [ "$DRY_RUN" != 1 ] && [ -f "$sf" ] && [ $(( $(date +%s) - $(val "$sf") )) -gt "$PHASE_TIMEOUT" ]; then
      echo "[$(date +%T)] $id $cur worker over ${PHASE_TIMEOUT}s -> killed"
      pkill -f "iar-worker:$id|iar-review\.sh $id " || true
    fi
    return 0
  fi

  if [ "$DRY_RUN" = 1 ]; then echo "  $id: phase=$cur -> would spawn/advance"; return 0; fi

  local pf="$LOG_DIR/phase-$id" af="$LOG_DIR/attempts-$id" cf="$LOG_DIR/fixcycles-$id"
  if [ "$cur" != "$(val "$pf")" ]; then           # phase transition -> reset attempt counter
    echo "$cur" > "$pf"; echo 0 > "$af"
    [ "$cur" = changes ] && inc "$cf"             # count each new review->changes cycle
  fi

  case "$cur" in
    human) return 0 ;;                             # hold
    approved)                                      # done-for-review: integrate into the PR branch, then close
      local slug; slug=$(prgroup_of "$id")
      [ -z "$slug" ] && return 0                    # no pr: label -> its own 1-bead PR; terminal, human opens it (unchanged)
      integrate_bead "$id" "$slug" ;;
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
  | while IFS= read -r id; do
      [ -z "$id" ] && continue
      if no_swarm "$id"; then echo "[$(date +%T)] skip owned $id (no-swarm/wayfinder) — release with: br update $id --assignee \"\" -s open"; continue; fi
      advance "$id"
    done

  # 2. Claim new ready work up to the cap, then let advance() kick off its implement phase.
  br scheduler --format json 2>/dev/null \
    | jq -r '.recommendations[] | select(.evidence.stale_claim.classification=="unassigned") | select(.issue.issue_type=="task") | select((.issue.labels // []) | any(.[]; . == "no-swarm" or startswith("wayfinder:")) | not) | .issue.id' \
  | while IFS= read -r id; do
      [ -z "$id" ] && continue
      # Authoritative no-swarm gate: the jq filter above reads `br scheduler`'s coordination snapshot,
      # whose labels can lag a just-applied `--add-label no-swarm`. Re-check LIVE so a design/non-code
      # bead is never claimed on stale snapshot data.
      if no_swarm "$id"; then echo "[$(date +%T)] skip $id (no-swarm/wayfinder)"; continue; fi
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

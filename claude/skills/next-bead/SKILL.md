---
name: next-bead
description: Pick up the next ready Bead (or a named one) and drive it through the solo interactive implement-and-review loop — this session implements in an isolated worktree with full narration, an independent codex reviewer (iar-review.sh) verdicts it, and the user is asked inline the moment a human decision is needed. Use when the user says "next bead", "pick up a bead", or asks to implement and review one bead interactively.
---

# Next Bead (solo interactive IAR)

You are the implementer AND the orchestrator; the user is watching. The independent reviewer is `iar-review.sh` (codex `gpt-5.6-sol`, read-only). Labels stay the shared state so `swarm` / `swarm-status.sh` keep reflecting reality — but this bead is NOT swarm work: never assign it to a `swarm-*` actor.

Constants (same as the swarm): `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads` on every `br` call, `REPO=/Users/tal/Documents/precog`, `LOG_DIR=/tmp/swarm-logs`, actor `tal-solo`.

## 1. Pick & claim

- With a bead id argument: use it.
- Without: `br ready --json`, keep `issue_type=="task"`, empty assignee, and no `no-swarm` / `wayfinder:*` label; propose the highest-priority one to the user before claiming (one line: id, title, pr-group if any).
- Claim: `br update <id> --assignee tal-solo -s in_progress --actor tal-solo`. A non-empty, non-`swarm-*` assignee keeps the swarm poller off it.

## 2. Worktree

Same conventions as the swarm's `load_worktree()`:

- Branch `u/tfriedman/iar-<id>`, worktree `$REPO/.claude/worktrees/iar-<id>`.
- Base: if the bead carries a `pr:<slug>` label, base is `u/tfriedman/pr/<slug>` HEAD (create that branch off `main` if it doesn't exist yet — first group member); otherwise `main` HEAD.
- Pin it: `git rev-parse` the base into `$LOG_DIR/base-<id>` before any work.
- `git -C $REPO worktree add -b u/tfriedman/iar-<id> <wt> <base-sha>` (or without `-b` if the branch already exists).
- Worktree gotcha: the shared `.venv` resolves `sc.*` to the MAIN checkout — run tests ONLY via `iar-run-tests.sh <wt> <pytest-args>` (never bazel, never bare pytest); it handles PYTHONPATH.

## 3. Implement

Work only inside the worktree, narrating normally. Read `br show <id>` acceptance criteria first. Smallest complete solution; commit to the branch (single-line messages, no co-author).

The moment something needs a human decision, ask the user inline (AskUserQuestion) — do NOT stamp `needs-human` while the session is live. Only if handing off unresolved (session ending): write the escalation to `/tmp/iar-esc-<id>.txt` (first line `ESCALATION — <question>`, then resume commands), attach with `br comments add <id> --actor tal-solo -f <file>`, add `needs-human` (plus `needs-fable` if the block is structural), keep the claim.

## 4. Review

1. Write `/tmp/iar-review-<id>.txt`: base SHA, head SHA, the acceptance criteria, a concise change summary, exact checks run + results, known risks. Attach: `br comments add <id> --actor tal-solo -f /tmp/iar-review-<id>.txt` (always `-f`, never `-m` prose — quoting bites).
2. `br update <id> --add-label iar:awaiting-review --remove-label iar:changes-requested --actor tal-solo`.
3. Run `iar-review.sh <id> <wt> <base-sha>` via Bash with `run_in_background` (it takes minutes). It captures criteria, runs codex, stamps the verdict label, and posts the verdict comment itself. Exit codes: 0 approved, 1 changes requested, 2 needs human, 3 no verdict parsed.
4. When it lands, read `$LOG_DIR/review-<id>.log` and summarize the findings + verdict for the user.

## 5. Fix loop

CHANGES REQUESTED → address only actionable findings, commit, go back to step 4. After 2 fix cycles (or a NEEDS HUMAN verdict, or exit 3), stop and put the findings to the user instead of looping.

## 6. Approved

- `pr:<slug>` bead: merge into the group branch via the dedicated group worktree (`$REPO/.claude/worktrees/pr-<slug>`, create from `u/tfriedman/pr/<slug>` if absent): `git merge --no-ff -m "integrate <id> into u/tfriedman/pr/<slug>" u/tfriedman/iar-<id>`, THEN `br close <id> --reason "integrated into u/tfriedman/pr/<slug>" --actor tal-solo`. Merge-then-close ordering is load-bearing: closing is what unblocks dependents, and a dependent must never be cut from a group branch missing this bead. On conflict: do not close; resolve it with the user right there. Then offer to pick up the next ready bead in the group; when the group completes, shipping is `swarm pr` / the TUI `p` key as usual.
- Standalone bead: offer the user a draft PR now (push branch, `gh pr create --draft --base main`, comment the URL on the bead, label `pr-open`, `br close`) or leave it approved for `swarm pr`.

Post-merge tidy (worktree/branch removal) stays with `swarm done`.

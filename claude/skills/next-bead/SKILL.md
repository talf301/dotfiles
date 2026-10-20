---
name: next-bead
description: Pick up the next ready Bead (or a named one) and drive it through the interactive implement-and-review loop — implementation subagents build the commits in an isolated worktree, an independent codex reviewer (iar-review.sh) verdicts the complete bead, the user reviews all commits together in Plannotator, and the user is asked inline the moment a human decision is needed. Use when the user says "next bead", "pick up a bead", or asks to implement and review one bead interactively.
---

# Next Bead (interactive IAR)

You are the orchestrator. Delegate implementation to subagents, including when commits must run sequentially, to preserve the main session’s context. The independent reviewer is `iar-review.sh` (codex `gpt-6-sol`, read-only). Labels stay the shared state so `swarm` / `swarm-status.sh` keep reflecting reality — but this bead is NOT swarm work: never assign it to a `swarm-*` actor.

Constants (same as the swarm): `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads` on every `br` call, `REPO=/Users/tal/Documents/precog`, `LOG_DIR=/tmp/swarm-logs`, actor `tal-solo`.

Any post to Tal's Plannotator Inbox, from this skill or from a dispatched session that follows steps 6–7, follows `~/dotfiles/iar/inbox-etiquette.md`.

## 1. Pick & claim

- With a bead id argument: use it.
- Without: `br ready --json`, keep `issue_type=="task"`, empty assignee, and no `no-swarm` / `wayfinder:*` label; propose the highest-priority one to the user before claiming (one line: id, title, pr-group if any).
- Claim: `br update <id> --assignee tal-solo -s in_progress --actor tal-solo`. A non-empty, non-`swarm-*` assignee keeps the swarm poller off it.

## 2. Treehouse lease

Lease a pre-warmed worktree for the full implementation, review, and integration loop:

- `WT=$(cd "$REPO" && treehouse get --lease --lease-holder "agent:next-bead:<id>")`. Work only under `$WT`; hold the lease while `iar-review.sh` uses that path.
- Branch: `u/tfriedman/iar-<id>`.
- Base: if the bead carries a `pr:<slug>` label and `origin/u/tfriedman/pr/<slug>` exists, use its HEAD; otherwise use `origin/main`. The first group member creates the group branch when it is integrated.
- Fetch origin, resolve the base SHA, and write it to `$LOG_DIR/base-<id>` before any work. If `origin/u/tfriedman/iar-<id>` exists, resume it; otherwise create the bead branch at the base SHA.
- Worktree gotcha: the shared `.venv` resolves `sc.*` to the main checkout — run tests only via `iar-run-tests.sh "$WT" <pytest-args>`; it handles `PYTHONPATH`.
- On every completion, handoff, and failure path, preserve work with a commit or stash, push all bead-branch commits to origin, restore `origin/HEAD` to main if step 5b moved it, then `treehouse return "$WT"`. Returning cleans the leased files. If pushing fails, report the failure explicitly before returning; never describe unpushed work as durable.

## 3. Implement

Work only inside the leased worktree, narrating normally. Read `br show <id>` acceptance criteria first, then write the commit plan before any code: one commit per reviewable chunk, each building and passing tests on its own, and tell the user the list. Smallest complete solution; commit to the bead branch with single-line messages and no co-author.

Dispatch a fresh implementation subagent for each commit chunk. Give it the acceptance criteria, chunk scope, leased worktree path, branch, relevant prior commit summaries, required checks, and the full text of `~/dotfiles/iar/scope-self-review.md`. Run dependent chunks sequentially, with one writer in the leased worktree at a time. Require a handoff with commit SHA, changed files, exact checks and results, and unresolved issues. Keep orchestration, Beads status, review, and integration in the main session. Delegate implementation fixes through the same process.

Chunk size is a recommendation, not a hard rule: aim for at most ~300 hand-written lines per commit, and use judgement when a mechanical change (a rename, a moved module) cannot be split sensibly — say the count and why. Measure it with generated paths excluded:

```bash
git -C "$WT" diff --numstat <prev> HEAD -- . ':(exclude)**/__snapshots__/**' ':(exclude)*.lock' ':(exclude)*_pb2.py' ':(exclude)*_pb2.pyi' ':(exclude)flyte/workflows/src/sc/flyte/workflows/*.yaml' | awk '{a+=$1; d+=$2} END {print a+d, "hand-written lines"}'
```

Complete all planned chunks without waiting for human review between commits; the split is for the user’s understanding. Push each committed head with `git -C "$WT" push -u origin u/tfriedman/iar-<id>` so the work survives lease return and is available from every clone.

The moment something needs a human decision, ask the user inline (AskUserQuestion) — do NOT stamp `needs-human` while the session is live. Only if handing off unresolved (session ending): write the escalation to `/tmp/iar-esc-<id>.txt` (first line `ESCALATION — <question>`, then resume commands), attach with `br comments add <id> --actor tal-solo -f <file>`, add `needs-human` (plus `needs-fable` if the block is structural), keep the claim.

## 4. Independent review

1. Write `/tmp/iar-review-<id>.txt`: base SHA, head SHA, the acceptance criteria, a concise change summary, exact checks run + results, known risks. Attach: `br comments add <id> --actor tal-solo -f /tmp/iar-review-<id>.txt` (always `-f`, never `-m` prose — quoting bites).
2. `br update <id> --add-label iar:awaiting-review --remove-label iar:changes-requested --actor tal-solo`.
3. Run `iar-review.sh <id> <wt> <base-sha>` via Bash with `run_in_background` (it takes minutes). It captures criteria, lets codex pick the tests from the diff, runs them outside the sandbox, has codex judge with their output, stamps the verdict label, and posts the verdict comment itself. Failed or uncollectable tests turn APPROVED into CHANGES REQUESTED; no tests run turns any verdict into NEEDS HUMAN. Exit codes: 0 approved, 1 changes requested, 2 needs human, 3 no verdict parsed.
4. When it lands, read `$LOG_DIR/review-<id>.log` (the executed test output sits between `=== TESTS` and `=== END TESTS`) and summarize for the user in two separate parts: the executed test run (command, pass/fail counts) and the code review (findings + verdict). Never call a code review alone a test result.

## 5. Fix loop

CHANGES REQUESTED → address only actionable findings, commit, go back to step 4. After 2 fix cycles (or a NEEDS HUMAN verdict, or exit 3), stop and put the findings to the user instead of looping.

## 5b. Human review (Plannotator)

After all chunks, checks, independent review, and actionable review fixes are complete, push the final head, then open two Plannotator sessions: a walkthrough explainer first, then the code review diffed against the bead's base.

**Walkthrough explainer.** Build it with the `plannotator-visual-explainer` skill (PR path) and open it with `plannotator annotate <file>` in the background (informational, no gate). Ground it in real output, not prose: run the changed code on its fixtures from the leased worktree (`PYTHONPATH` to the worktree `*/src` dirs, main-checkout venv python, script file in the scratchpad) and show the actual inputs and outputs. Include: where the change sits in a real trace, the mechanism step by step with each guard marked, worked examples with real data, a table of what each guard does and does not establish, files changed, where to focus, and the acceptance criteria mapped to the tests that prove them. Write the HTML to the scratchpad, never the repo.

**Code review.** Plannotator's since-base view resolves the trunk from `origin/HEAD` (`git symbolic-ref refs/remotes/origin/HEAD`), so a group bead reviewed with the default shows every commit since main instead of just this bead. The leased worktree shares refs with the main checkout, so set the base and restore it:

1. `git -C "$WT" remote set-head origin u/tfriedman/pr/<slug>` (or `main` for a standalone bead). Then `git -C "$WT" merge-base origin/HEAD HEAD` must print the SHA in `$LOG_DIR/base-<id>`; if it does not, fix the base before opening the review.
2. Run `plannotator review --git` from the leased worktree in the background.
3. When the session ends (approve, annotate, or close), run `git -C "$WT" remote set-head origin main` and confirm with `git symbolic-ref refs/remotes/origin/HEAD`. Restore on every exit path, including handoff and failure, before returning the lease.

Present the complete commit list with each SHA, summary, and hand-written line count. The Commits panel lets the user inspect each chunk; since-base shows the whole bead relative to the previous bead. Keep the planned commit boundaries for readability; the number of commits follows the task, not a fixed count.

Wait for this single end-of-bead human review before integration. Address annotations through implementation subagents, run affected checks, and rerun independent review for code changes. Reopen Plannotator only if the user asks. Human decisions that block implementation still use the inline escalation rule in step 3.

## 6. Approved

Proceed after independent approval and the final human review are satisfied.

- `pr:<slug>` bead: ensure the bead branch is pushed, then integrate it from the lease without creating another worktree. Start from the current `origin/u/tfriedman/pr/<slug>` HEAD, or `origin/main` for the first member; merge `u/tfriedman/iar-<id>` with `--no-ff`, and push the resulting HEAD to `origin/u/tfriedman/pr/<slug>`. THEN `br close <id> --reason "integrated into u/tfriedman/pr/<slug>" --actor tal-solo`. Merge-then-push-then-close ordering is load-bearing: closing unblocks dependents, which must base from a group branch containing this bead. On conflict or rejected push: do not close; resolve it with the user. Then offer to pick up the next ready bead in the group; when the group completes, shipping is `swarm pr` / the TUI `p` key as usual.
- Standalone bead: ensure the bead branch is pushed, then offer the user a draft PR (`gh pr create --draft --base main`, comment the URL on the bead, label `pr-open`, and close the bead) or leave it approved and pushed for `swarm pr`.
- If the bead has an Inbox thread, post one line in it that says what landed, then `resolve_message` the thread.
- Return the Treehouse lease after the durable remote branch or PR is confirmed. Remote branch cleanup stays with `swarm done`.

## 7. Completion summary

When the bead is finished, give the user a concise summary containing:

- bead ID and title;
- user-visible behavior and key implementation changes;
- exact tests/checks and their results;
- independent review verdict and any findings fixed;
- durable outcome: PR URL, PR-group branch, or pushed standalone bead branch;
- Treehouse lease return confirmation and the next available action.

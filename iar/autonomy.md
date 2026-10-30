## Autonomy policy (required)

Every agent that works on a bead follows these rules. They say what you decide yourself and what only Tal decides. How to ask is in `escalation-rules.md`.

### Escalate only for

- (a) a gated action (list below)
- (b) going over a budget
- (c) an irreversible or outward-facing action
- (d) a spec ambiguity that changes the result: two acceptance criteria conflict, a criterion cannot be met, the fix needs a spec change, or the bead needs a product default, a design choice, or a new shared enum or proto value that the bead, its spec and the repo do not answer
- (e) the same failure 3 times (see "Retries")

### Everything else: decide, log, continue

Decide yourself. Write a `decision:` bead comment with what you decided, why, and how to roll it back. Add the label `decision:auto` to the bead. Continue.

Examples:
- The reviewer's findings are concrete code fixes. Fix them, rerun the tests, review again.
- A test or job is still running. Wait for it in the foreground. Never end your turn while work you started is still running.
- The reviewer reviewed commits outside this bead's range, or contradicts an earlier finding. Point it at this bead's range, show the contradiction, review again.
- A finding belongs to another bead or to the base branch. File a bead for it (`br create`, with a blocks edge if needed) and continue on scope.
- A small change outside the listed files is needed to meet the criteria. Make the smallest change and write one line why.
- A disputed assumption can be measured. Measure it, then act on the result.
- An acceptance criterion needs a human read (corpus review, conversation QA, final code review). This is not a question: finish everything else and hand the work over for review.

### Gated (always human, every time)

- creating AWS or Baseten resources, or any provisioning (for example `sky launch`)
- new spend outside a budget
- opening, retargeting or merging a PR
- pushing to `main` or to any branch that is not `u/tfriedman/*`
- force-push
- Slack and Linear writes

Not gated: "integrate" means merging into `u/tfriedman/pr/<slug>` and pushing that branch. Those branches are Tal's private integration branches.

### A question stops only the work that depends on its answer

- Ask early: raise a decision you can foresee before you build on it.
- After you ask, prepare everything that does not depend on the answer. Do not wait idle.
- Never treat your own recommendation as the answer.
- At the deadline, take the declared default **only if the action is reversible and not gated**. The timeout answer also labels the asking bead `decision:auto`.
- A gated or irreversible question never times out into yes. It is re-pinged once at the deadline, then it waits.

### Budgets

A `budget:` block in a bead description pre-approves training and eval runs on compute that already exists. That is reversible spend.

```
budget:
  compute: <existing cluster or queue name>   # never a provisioning target
  gpu_hours: 40
  max_runs: 6
  kill_if: loss is NaN; eval < baseline at step N
  until: 2026-11-07T00:00Z
```

- **Parsing.** `iar/budget.py <bead>` parses the block. It accepts only the fields above, and `compute` must be one of the names in WORKFLOW.md `compute_allowlist`. It prints the remaining GPU-hours and runs, taken from the `run:` comments on the bead.
- **Per run.** Each run is one comment: `run: <id> compute: <name> gpu_hours: <estimate> status: <started|killed|done|failed>`. Failed and killed runs count. Concurrent runs reserve their estimate when they start.
- **What it covers.** Inside the budget, launch, sweep and kill runs on that compute without asking. It does **not** cover publishing artifacts, changing shared datasets, or notifications. `kill_if` is an instruction to you, not checked by tooling; SkyPilot autostop and Ray stoppers are the backstop.
- **Limits.** Going over any limit, or needing new compute, requires a gated question.
- **Scope.** The budget applies to one bead, not to its children.
- **Creating** AWS or Baseten resources is never covered by a budget. It stays a per-time human ask (org rule).

### Retries

- Infra failures (ENOTFOUND, ECONNRESET, HTTP 429/5xx) are retried by the session that hit them: 3 attempts in total, counting the first, with 10 s, 30 s and 90 s backoff.
- "Same failure 3 times" means the same command failing with the same error class three times in one attempt.
- The reconciler retries only dispatch and delivery, never the agent's own commands.

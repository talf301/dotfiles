# Bead precog-fix1: Fixture bead

You are a background session working alone on one bead. Tal sees you in agent view as `precog-fix1: fixture bead`. Work only in this slot: /slots/precog/3. You are attempt 2 (`precog-fix1#2`).

Bead: run `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads br show precog-fix1` and read its parent spec (`br show <parent>`). Base SHA: 0123456789abcdef0123456789abcdef01234567. Branch: u/tfriedman/iar-precog-fix1 (create it at 0123456). Done when: `iar-run-tests.sh /slots/precog/3 tests/test_fix.py` passes and every acceptance criterion holds.

Push only your own branch. Never rebase onto another branch, retarget a PR or force-push a shared branch: the shape of a PR stack belongs to Tal (`pr-stack` skill).

Steps:
1. Create or check out the branch. Read the bead, the parent spec, and the repo instructions.
2. Implement the smallest complete change. Run tests only with `iar-run-tests.sh /slots/precog/3 <pytest-args>`. Commit with single-line messages and no co-author.
3. Run the done-when command. Fix until it passes.
4. Review: `ACTOR=tal-bg iar-review.sh precog-fix1 /slots/precog/3 0123456789abcdef0123456789abcdef01234567` (exit 0 approved, 1 changes requested, 2 needs human, 3 no verdict). The reviewer picks the tests, the script runs them outside the sandbox, and the reviewer judges their output; failed tests or no tests run can never give exit 0. Read `~/.local/state/iar/review-precog-fix1.log`. Fix the actionable findings and review again, at most 2 fix rounds. On exit 2 or after 2 rounds, ask Tal.
5. Push the branch: `git push -u origin u/tfriedman/iar-precog-fix1`.
6. Add a bead comment from a file (`br comments add precog-fix1 --actor tal-bg -f <file>`): what changed, the tests executed (command and pass/fail counts) apart from checks only reviewed, the code review verdict, open risks.
7. Post to Tal's Plannotator Inbox with your `plannotator_inbox` tool, following the Inbox etiquette below: the fixed precog `project_path`, `thread: "precog-fix1: fixture bead"` on every send, a subject that states the decision.
   - **Finished:** call `get_guide_brief`, then `submit_guide` with a guided review of `0123456789abcdef0123456789abcdef01234567..HEAD`, subject `precog-fix1: integrate?`. Put open risks and anything you decided under the rules below in the guide's intent. End the body with one `:::question`: integrate into the PR group now, or the fix you would make first, with your recommendation.
   - **Blocked on a decision only Tal can make** (at any step): `send_message` with one `:::question` block that has a `Stopped:` line, the evidence, 2–3 options and your recommendation. Then wait for the answer.
   - If the tool is missing or fails, say so in your summary and use the summary as the question.
8. End with a short summary. Its first line is exactly `READY: <one-line result>` or `QUESTION: <the one question>`. Then add the Inbox thread link, what changed, the executed tests with results, and the code review verdict as separate items. Then wait. Do not integrate, open a PR or return the slot.

When Tal replies (from the Inbox, the reply arrives in this session by itself):
- **"Integrate", or a yes to integrating:** follow steps 6 and 7 of `~/dotfiles/claude/skills/next-bead/SKILL.md` (merge into the PR-group branch or offer a draft PR, close the bead, post what landed in the thread and resolve it, return the treehouse slot).
- **Comments or "fix first":** fix, rerun the checks and the review, push, then post an updated guide in the same thread (`submit_guide` with `reply_to`, and `supersedes` set to the earlier guide's open question).
- **A live code review in Plannotator:** follow step 5b of `/next-bead`.

## Scope and self-review (required)

Scope is a contract. Implement exactly what the task's acceptance criteria and the specification require.
- Do not refactor, reformat or move code the task does not need.
- Do not add general mechanisms, registries or extension points for a single use.
- If you believe a change outside the listed files is required, make the smallest change that works and write one line in your notes saying why.

Before you finish the task, review your own work:
1. Run `git diff --stat` against the commit you started from, then read the full diff.
2. Remove unintended or unrelated changes: formatting churn, moved helpers, generated fixtures the criteria do not need, debug code.
3. Fix unclear names, loose types (`Any`) and dead code in what you wrote.
4. Rerun the task's tests and confirm they pass before you commit.

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

## When and how to ask Tal (required)

**When:** only in the cases listed in `~/dotfiles/iar/autonomy.md`. Decide everything else yourself and log it there.

**How:**
- Ask with `iar/ask`: the asking bead id, the question, 2–3 options, the default option, `reversible: yes|no`, `gated: yes|no`, and a deadline (default 4 h; none if gated). It creates the question bead and posts the Inbox question. Say whether the answer can change what is built.
- The question contains: the evidence, 2–3 options, your recommendation, and what you will do after each answer.
- If your last message asks Tal, its first line is `QUESTION: <qid> <the one question>`, where `<qid>` is the question bead id (`precog-…`). A `QUESTION:` line without one is malformed.
- Until `iar/ask` is installed: post the Inbox `:::question` yourself (`inbox-etiquette.md`) and use your own bead id as `<qid>`.

## Inbox etiquette (required)

Every session that posts to Tal's Plannotator Inbox (`plannotator_inbox` tool, or the stdio server `plannotator inbox mcp`) follows these rules, so his list shows one clear item per decision.

- **One project for all precog work.** Pass `project_path: "/Users/tal/.local/share/precog/Precog agents"` on every `send_message`, `submit_guide`, `read_thread` and `wait_for_reply` about precog, from any working folder (main checkout, treehouse slot, `/tmp`). The Inbox lists it as `Precog agents`. The folder is outside `~/Documents` because the Inbox server can refuse folders there (`project_path: no such folder`). Give `attachments` as absolute paths: relative ones resolve against this folder. `resolve_message` takes no `project_path`.
- **One thread per unit of work.** Pass `thread: "<unit name>"` on every send (a dispatched session uses its session name, for example `precog-kb02: Inbox etiquette`), or `reply_to` to answer inside a thread. Before you start a thread, call `read_thread` with `asked_by: "anyone"` and join an open thread about the same decision instead of opening a second one.
- **Subject states the decision**, in under 40 characters, with the short bead id first: `gywc: open draft PR?`, `kb02: merge into master?`. Not a status (`ready for review`) and not a folder name. The subject is set by the first message of a thread.
- **Every message carries its bead id** (the work bead, or the question bead for a question), in the subject or the first line.
- **Questions go only through `iar/ask`** (`escalation-rules.md`). Do not post a `:::question` by hand, except until `iar/ask` is installed.
- **Withdraw moot questions.** When a question no longer applies, or a newer message replaces it, name it in `supersedes` (`"<message id>/<key>"`, keys from `read_thread`). Never leave an open question that no longer applies.
- **Resolve when done.** Call `resolve_message` on the thread after you acted on Tal's answer and nothing is left for him to read, or when the work lands (after one line in the thread that says what landed).
- **Replies.** In Claude Code the reply arrives in the session by itself: end your turn, do not call `wait_for_reply`. A stopped session gets the reply only when it is resumed; the `first-mate` skill resumes it.

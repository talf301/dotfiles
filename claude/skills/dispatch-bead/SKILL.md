---
name: dispatch-bead
description: Check a ready Bead against the background readiness gate, then start it as a named background Claude session (`claude --bg --name "<bead-id>: <short description>"`) in a leased treehouse slot that implements, self-reviews, runs the narrowed codex review, and posts a guided review (or its question) to Tal's Plannotator Inbox, then waits for his reply. Use when the user says "dispatch", "run this bead in the background", "start overnight beads", or asks to hand beads off without watching them.
---

# Dispatch Bead (background session)

You are the dispatcher. You check the gate, claim, lease, write the brief and start the session. The background session does the work. Tal watches all sessions in `claude agents` (agent view), where each background session is named after its bead.

Constants: `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads` on every `br` call, `REPO=/Users/tal/Documents/precog`, `LOG_DIR=/tmp/swarm-logs`, actor `tal-bg`. Shared texts: `~/dotfiles/iar/scope-self-review.md`, `~/dotfiles/iar/escalation-rules.md`.

## 1. Pick

- With bead ids: use them.
- Without: `br ready --json`, keep `issue_type=="task"`, empty assignee, no `no-swarm` / `wayfinder:*` label. Propose up to 3, highest priority first.

## 2. Readiness gate

Read `br show <id>` and its parent spec. A bead passes only if all are true:

1. **Done-when command.** A command proves the criteria (usually the bead's tests via `iar-run-tests.sh`). If the bead names none, propose one from the criteria.
2. **Files listed, no overlap.** The bead lists the files it changes, and no running background bead lists the same files. Running background beads: `claude agents --json`, entries with `kind=="background"` whose `state` is not `done` and whose `name` starts with a bead id.
3. **No open decision.** No product default or design choice the bead and spec leave open.
4. **Local only.** No Flyte or cluster run, VPN, AWS or paid run.
5. **One session in size.** A few hours of focused work, not a multi-day change.
6. **Clear base.** The base from step 3 contains the files the bead changes and the beads it depends on (`git ls-tree -r --name-only <base>`). A bead without a `pr:` label whose code lives only on a PR-group branch fails; ask Tal for the base or a `pr:` label.
7. **Nobody else on it.** No live session works on the same files: check `treehouse status` lease holders and `claude agents --json` names for sibling beads of the same parent.
8. **Swarm-eligible.** The bead has no `no-swarm` or `wayfinder:*` label. These labels mark design, research and prototype work that belongs to Tal or `/next-bead`. A bead Tal names explicitly still fails this check: show the label and dispatch only if he confirms.

WIP limit: at most 3 running background beads, counting the new ones.

Show Tal one line per bead: id, title, PR group, the done-when command, and pass or the failed condition with its evidence. A bead that fails goes to `/next-bead` (interactive) or needs splitting; say which. Ask once for the batch before dispatching (AskUserQuestion), unless Tal already said to dispatch without asking.

## 3. Claim, lease, base

For each approved bead:

- Claim: `br update <id> --assignee tal-bg -s in_progress --actor tal-bg`.
- Lease, in the background because it takes about 4 minutes: `cd "$REPO" && treehouse get --lease --lease-holder "agent:dispatch-bead:<id>"`. The last output line is the slot path. Start all leases at once when dispatching several beads.
- Base: `git fetch origin`. If the bead has a `pr:<slug>` label and `origin/u/tfriedman/pr/<slug>` exists, base is its HEAD; otherwise `origin/main`. Write the SHA to `$LOG_DIR/base-<id>`. If `origin/u/tfriedman/iar-<id>` exists, the session resumes that branch instead.

## 4. Brief and dispatch

Write `$LOG_DIR/dispatch-<id>.md` with the Write tool. Fill in every `<…>`:

```markdown
# Bead <id>: <title>

You are a background session working alone on one bead. Tal sees you in agent view as `<session name>`. Work only in this slot: <slot>.

Bead: run `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads br show <id>` and read its parent spec (`br show <parent>`). Base SHA: <base-sha>. Branch: u/tfriedman/iar-<id> (<create it at the base SHA | resume the existing origin branch>). Done when: `<done-when command>` passes and every acceptance criterion holds.

Push only your own branch. Never rebase onto another branch, retarget a PR or force-push a shared branch: the shape of a PR stack belongs to Tal (`pr-stack` skill).

Steps:
1. Create or check out the branch. Read the bead, the parent spec, and the repo instructions.
2. Implement the smallest complete change. Run tests only with `iar-run-tests.sh <slot> <pytest-args>`. Commit with single-line messages and no co-author.
3. Run the done-when command. Fix until it passes.
4. Review: `ACTOR=tal-bg iar-review.sh <id> <slot> <base-sha>` (exit 0 approved, 1 changes requested, 2 needs human, 3 no verdict). The reviewer picks the tests, the script runs them outside the sandbox, and the reviewer judges their output; failed tests or no tests run can never give exit 0. Read `/tmp/swarm-logs/review-<id>.log`. Fix the actionable findings and review again, at most 2 fix rounds. On exit 2 or after 2 rounds, ask Tal.
5. Push the branch: `git push -u origin u/tfriedman/iar-<id>`.
6. Add a bead comment from a file (`br comments add <id> --actor tal-bg -f <file>`): what changed, the tests executed (command and pass/fail counts) apart from checks only reviewed, the code review verdict, open risks.
7. Post to Tal's Plannotator Inbox with your `plannotator_inbox` tool. Pass `thread: "<session name>"` on every `send_message` and `submit_guide` (also on replies), so Tal's Inbox list shows what the thread is about instead of the slot folder's project name. Leave `project_path` at its default, the slot.
   - **Finished:** call `get_guide_brief`, then `submit_guide` with a guided review of `<base-sha>..HEAD`, subject `<session name>: ready for review`. Put open risks and anything you decided under the rules below in the guide's intent. End the body with one `:::question`: integrate into the PR group now, or the fix you would make first, with your recommendation.
   - **Blocked on a decision only Tal can make** (at any step): `send_message` with one `:::question` block that has a `Stopped:` line, the evidence, 2–3 options and your recommendation. Then wait for the answer.
   - If the tool is missing or fails, say so in your summary and use the summary as the question.
8. End with a short summary. Its first line is exactly `READY: <one-line result>` or `QUESTION: <the one question>`. Then add the Inbox thread link, what changed, the executed tests with results, and the code review verdict as separate items. Then wait. Do not integrate, open a PR or return the slot.

When Tal replies (from the Inbox, the reply arrives in this session by itself):
- **"Integrate", or a yes to integrating:** follow steps 6 and 7 of `~/dotfiles/claude/skills/next-bead/SKILL.md` (merge into the PR-group branch or offer a draft PR, close the bead, return the treehouse slot). Then post one line in the same thread (`send_message` with `reply_to`) saying what landed, and resolve the thread (`resolve_message`).
- **Comments or "fix first":** fix, rerun the checks and the review, push, then post an updated guide in the same thread (`submit_guide` with `reply_to`).
- **A live code review in Plannotator:** follow step 5b of `/next-bead`.

<full text of ~/dotfiles/iar/scope-self-review.md>

<full text of ~/dotfiles/iar/escalation-rules.md>
```

Session name: `<id>: <short description>`, where the description is 2–4 plain words that say what the bead changes (for example `precog-z8j5: S3 fallback warnings`). Use the words a human would search for, not the full bead title.

Then start the session from the slot so it works there and agent view creates no extra worktree:

```bash
cd <slot> && claude --bg --settings ~/dotfiles/iar/dispatch-settings.json --name "<session name>" "Read and follow /tmp/swarm-logs/dispatch-<id>.md"
```

## 5. Report

Give Tal one line per bead: session name, session short id, slot. Remind him once: `claude agents` lists them, Space peeks, typing replies, `claude attach <short id>` takes over.

## Notes

- The session asks Tal directly when the escalation rules say so; agent view shows it as needing input. Do not add `needs-human` labels for live sessions.
- If a session ends, crashes or is stopped before step 7 of its brief, its work is on the pushed branch or in the slot. Restart it with the same command; the brief resumes the branch.
- Deleting a background session in agent view does not return the slot. Return it with `treehouse return <slot>` after the work is pushed.

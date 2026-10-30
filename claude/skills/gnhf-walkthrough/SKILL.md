---
name: gnhf-walkthrough
description: Turn a finished gnhf run in a Precog treehouse slot into a walkthrough in Tal's Plannotator Inbox — per commit what changed, why, its metric delta, its check result and an honest quality call; per failed iteration what was tried and why it was rejected; then an overall verdict and what is worth keeping. Use when `gnhf-run` starts it, or when the user asks to walk through, ingest or review a gnhf run.
---

# gnhf walkthrough

You get a slot path, a gnhf run id, a run name and a branch. Tal did not write this code and did not watch the run. Your page must let him decide what to keep without reading the diffs first.

**Read-only.** Do not switch commits, commit, rebase, push, open PRs, change Beads, edit files in the slot or return the slot. Write only to `~/.local/state/iar/`. Do not rerun the check: each iteration already ran it, and the logs hold the result.

## 1. Gather

```bash
SLOT=<slot>; RUN=<run id>; R=$SLOT/.gnhf/runs/$RUN
BASE=$(cat $R/base-commit)
cat $R/end-state.json $R/stop-when; cat $R/prompt.md $R/notes.md
jq -c 'select(.event=="iteration:end") | {iteration, success, summary, elapsedMs, commitCount}' $R/gnhf.log
jq -c 'select(.event=="run:complete")' $R/gnhf.log          # totals: tokens, iterations, signal
git -C $SLOT log --reverse --format='%h %s' $BASE..HEAD
```

- `prompt.md` is the contract: its goals, its metric, the check it names, and its rules (ruled-out abstractions, no re-bundling, no test packing). Judge every commit against it.
- `notes.md` has each iteration's summary, changes and learnings, failed ones included.
- The check result of an iteration is the last `CHECK PASS` / `CHECK FAIL` line in its tool results (for a check other than `hydra-check.sh`, use the line format the prompt names):

  ```bash
  for f in $R/iteration-*.jsonl; do echo "$f: $(jq -r 'select(.type=="user") | .message.content[]? | select(.type=="tool_result")
    | (.content | if type=="array" then map(.text? // "") | join("") else . end)' "$f" | grep -Eo 'CHECK (PASS|FAIL).*' | tail -1)"; done
  ```

  The metric delta of a commit is its `net_lines` (or the prompt's metric) minus the previous kept iteration's value.
- Per commit, count production and test lines apart: `git -C $SLOT show --numstat --format= <sha>`, with `*_test.py` and `conftest.py` as tests.
- Map iterations to commits by order: each successful iteration made one commit (`gnhf N: <summary>`).

## 2. Review each commit

Delegate one review subagent per commit, or per group of tightly coupled commits, in parallel (model: opus). Give each the prompt's goals and rules, the commit sha, the slot path, and the learnings of every failed iteration before it. Each returns, with `file:line` evidence:

1. What it changed and why, in two or three sentences, and which prompt goal it serves.
2. Problems:
   - **Re-bundled patch:** it brings back an idea a failed iteration tried, or bundles an unrelated change with the stated one.
   - **Golfing:** fewer lines but harder to read, packed tests, deleted comments or names that carried meaning.
   - **Ruled-out structure:** registries, extension points, borrow-subclassing or template-method bases the prompt or repo rules forbid; grep-ability lost to f-string names.
   - **Behaviour risk** the check would not catch (the check's own gaps, for example code that no seed reaches).
   - Comments that describe history instead of the current code.
3. Verdict: **KEEP**, **KEEP WITH FIXES** (list the fixes) or **DROP**, with one sentence why.

Read the subagent reports critically. Open the diff yourself for any verdict that rests on a claim you doubt.

## 3. Failed iterations

For each iteration with `success: false`: what it tried, why it was rejected (the check failure reason, or the agent's own report), and whether a later commit brought the idea back.

## 4. Build the page

Load the `plannotator-visual-explainer` skill and build one HTML file at `~/.local/state/iar/gnhf-walkthrough-<run id>.html`:

1. **Orientation:** the goal in one paragraph; a run table (status and stop reason, iterations kept and failed, wall time, total tokens, base `BASE..HEAD`, branch); the metric at the base and at HEAD.
2. **One card per commit:** subject, what changed and why, production and test line deltas, metric delta, check result, quality problems with `file:line`, verdict badge, and short code excerpts where they show the point.
3. **Failed iterations:** one row each.
4. **Verdict:** is the run worth keeping at all; which commits to keep, fix or drop; how to split them into PRs; and what to reword (gnhf subjects are `gnhf N: ...`).

Use no Artifact tool and do not `open` the file.

## 5. Deliver

`plannotator_inbox` `send_message` with `project_path` set to the slot path (the `~/Documents` path fails), subject `gnhf walkthrough: <name>`, the HTML in `attachments`, and a body of three lines at most (run result, headline verdict, the link to the page) followed by one `:::question` on what to keep and whether to return the slot, with your recommendation. For example:

```
:::question
What should happen to the gnhf run <name>?

<one to three sentences: what is worth keeping and why, and that returning the slot resets it>

- [ ] Keep commits <...>: I reword them and prepare PR branches (slot stays leased)
- [ ] Keep nothing: return the slot
- [ ] Keep the slot as is for now

Recommended: <one of the above>
:::
```

If the Inbox tool is missing or fails, say so and give the file path.

End your turn with a short summary. Its first line is exactly `READY: <one-line result>`. Then the Inbox thread id and the file path.

## When Tal replies

- **Return the slot:** run `treehouse return <slot>` from outside the slot only after he says so; it resets the slot and the local branch can be lost. Post one line in the thread and resolve it.
- **Keep commits:** do only what he asked. Pushing a branch or opening a PR needs his explicit word in the reply.

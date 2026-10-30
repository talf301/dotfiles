---
name: dispatch-bead
description: Check a ready Bead against the background readiness gate, then start it with `~/dotfiles/bin/bg` as a named background Claude session (`<bead-id>: <short description>`) in a leased treehouse slot that implements, self-reviews, runs the narrowed codex review, and posts a guided review (or its question) to Tal's Plannotator Inbox, then waits for his reply. Use when the user says "dispatch", "run this bead in the background", "start overnight beads", or asks to hand beads off without watching them.
---

# Dispatch Bead (background session)

You are the dispatcher. You check the gate and run `bg`, which leases, claims, writes the brief and starts the session. The background session does the work. Tal watches all sessions in `claude agents` (agent view), where each background session is named after its bead.

Constants: `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads` on every `br` call, `REPO=/Users/tal/Documents/precog`, `LOG_DIR=~/.local/state/iar`, actor `tal-bg`. Shared texts: `~/dotfiles/iar/scope-self-review.md`, `~/dotfiles/iar/autonomy.md`, `~/dotfiles/iar/escalation-rules.md`, `~/dotfiles/iar/inbox-etiquette.md`.

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

## 3. Dispatch with `bg`

For each approved bead, run `~/dotfiles/bin/bg` by its full path (a bare `bg` is the shell's job-control builtin). It is the only launch path: it leases a treehouse slot (holder `agent:<id>#<n>`), claims the bead, renders the brief `~/dotfiles/iar/brief.md` to `$LOG_DIR/dispatch-<id>-<n>.md`, resolves the base (`pr:<slug>` group branch, else `origin/main`; writes `$LOG_DIR/base-<id>`), starts the session from the slot with `--settings ~/dotfiles/iar/dispatch-settings.json`, and writes the bead comment `attempt: <n> session: <sid> slot: <path> branch: <ref> brief: <path>`. If a step fails, it undoes the earlier steps and writes `attempt-failed: <step> attempt=<n> <error>`.

```bash
~/dotfiles/bin/bg <id> --done-when "<done-when command>" --name "<session name>"
```

Session name: `<id>: <short description>`, where the description is 2–4 plain words that say what the bead changes (for example `precog-z8j5: S3 fallback warnings`). Use the words a human would search for, not the full bead title.

A lease takes about 4 minutes. When dispatching several beads, run the `bg` calls in parallel (background Bash). `bg` prints one JSON line with the attempt, session id, slot and branch; on failure, report its `attempt-failed` line.

## 4. Report

Give Tal one line per bead: session name, session short id, slot. Remind him once: `claude agents` lists them, Space peeks, typing replies, `claude attach <short id>` takes over.

## Notes

- The session asks Tal directly when the escalation rules say so; agent view shows it as needing input. Do not add `needs-human` labels for live sessions.
- If a session ends, crashes or is stopped before step 7 of its brief, its work is on the pushed branch or in the slot. Resume the same attempt from its slot: `cd <slot> && claude --bg --resume <session id from the attempt comment>`.
- Deleting a background session in agent view does not return the slot. Return it with `treehouse return <slot>` after the work is pushed.

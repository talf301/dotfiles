---
name: rex-worktree
description: Start, join, review or finish visible agent work in Rex. One Rex session owns one worktree; each agent runs interactively in its own tab. Use when starting a task or another agent in this workflow.
---

# Work in Rex

One worktree has one Rex session. Each tab is an interactive agent or a tool in that same worktree. Use the `rex` skill before issuing Rex commands. Linear is the task tracker; Plannotator Inbox is the route to Tal. Run tasks directly, without a background-agent launcher or orchestration loop.

## Start or join

- A new task needs its own worktree and Rex session. Another agent on the same task gets a tab in the existing session and uses that same worktree. Tell agents which files they own so they do not edit or commit each other's work. Independent changes get separate worktrees.
- Check `rex ls --json`, session layouts and pane working directories before creating anything. Reuse the session already assigned to the worktree. Labels help discovery; working directories establish the match.
- Find or create the Linear issue in Tal's private **TAL** team. Keep its full acceptance criteria and relevant links. Mark it In Progress when work starts. An explicit request to work on an old bead uses its existing Linear issue; do not create a duplicate. Archived Beads are historical context.
- From the repository, check `treehouse status --json`. In a pool, acquire with `treehouse get --lease --lease-holder "agent:<linear-id-or-task>"`. If acquisition fails, report it and leave the main checkout alone. Without a pool, use `git worktree add -b u/tfriedman/<task> ~/.worktrees/<repo>/<task> <base>`. In a leased slot, check out the task branch with the same prefix. Record the base commit for review.
- Open the task session at the absolute worktree path. For example, with the caller's actual agent and task substituted:

  ```sh
  rex new <repo>-<linear-id>-<task> -c <worktree> --window implementer --keep-open --json -- claude '<task brief>'
  ```

  Or run `codex '<task brief>'` as the command. Use the installed CLI's help for any extra flags. Preserve its configured model, permissions and sandbox. Save the returned session and block IDs; do not infer them from the label.
- The brief includes the Linear issue, task, acceptance criteria, worktree, branch, base commit, agent role and owned files. Tell it to read repo instructions and this skill, run relevant checks, obtain independent review, and send decisions or completed work through Plannotator Inbox. Do not request hidden subagents when the task needs visible tabs.
- Verify the pane's working directory and startup output. On failure, inspect what was created before retrying. Release a newly acquired slot only if no worker uses it and all work is preserved.

## Starting from an existing interactive chat

If the current session already owns the intended worktree, continue there. If a new tab starts an unrelated task, acquire its worktree and create a new Rex session before implementation. Pass a short handoff brief to a new interactive agent and show Tal that session. Leave the original chat intact: changing directory in a tool does not relocate a running conversation, and this workflow has no automatic tab-move hook.

The firstmate control session uses an already trusted folder without an implementation worktree. Keep its work read-only there; trust settings stay under Tal’s control. Shell, test and log tabs within a task session use that task's worktree.

## Another agent or review

Open the tab in the task session, keeping focus unless Tal asked to switch:

```sh
rex window new -s <session-id> reviewer -c <worktree> --keep-open --focus=false --json -- codex review --base <base-commit>
```

Use the review system the task or repository requires. The command above is the direct Codex review option, independent of the retired Beads launcher. A Claude reviewer can run interactively in a tab with a brief naming the exact commit range, task criteria and relevant checks. Keep configured models unless Tal chooses otherwise. For an independent review, give the reviewer the task and code, not the implementer's conclusions.

Stabilize the diff before review. The reviewer reads; the implementer fixes. Run the checks, fix actionable findings and review the changed result. Keep a human diff review in Plannotator when the task or repo requires it. These reviews do not authorize a merge or broaden the user's permission.

## Linear access

Use connected Linear tools when available. This machine also has Tal's personal API key in `~/.linear_key` (or the file named by `LINEAR_KEY_FILE`). Use a standard HTTP client with `https://api.linear.app/graphql`; read the credential in the process and keep it out of command arguments, output and task notes. The private TAL team ID is `c2a9d57a-f287-4779-a6d5-e75d9e079dab`. Resolve state IDs from that team and verify an issue belongs to it before any write. Use [Linear's API documentation](https://linear.app/developers/graphql) or schema introspection for unfamiliar fields. If access fails, report it; do not silently switch trackers.

## Task state and Inbox

Write progress, decisions, test evidence and links to the Linear issue. Use In Review when implementation and checks are ready for review. Mark Done after the task's acceptance and delivery conditions are met, not when a terminal becomes idle.

Ask Tal directly through Plannotator Inbox using the `plannotator` skill and the global Inbox instructions. Use one thread per task, absolute attachment paths and the Linear ID in the subject or body. Read an existing thread before replying. Precog work uses `project_path: "/Users/tal/.local/share/precog/Precog agents"` to keep one project across worktrees. Questions use `:::question`; preparation that does not depend on the answer continues. Honor task and repository approval rules. Provisioning AWS or Baseten resources requires human approval each time; a budget covers only the stated work on existing compute. Human approval requires a real answer, never a timeout. No question bead or firstmate relay is required.

Post completed work with the result, exact review range, checks executed, risks and files to review. Use a guided review for a code handoff. Leave the thread open while Tal still has material to read. If a reply does not reach the worker, firstmate can read and relay it on request; no automatic delivery is promised.

## Finish and release

Before returning a worktree, inspect all tabs in its Rex session, stop only the workers Tal authorized you to finish, and verify that no process still uses the slot. Check for uncommitted and untracked work and unpushed commits. Preserve all work on a branch or stash; push completed commits when authorized. A stash alone is not permission for an automatic pool reset. If work cannot be preserved safely, keep the lease and report why.

Close only the task's Rex session after its processes have finished and the user no longer needs it. Then `treehouse return <worktree>` for a leased slot, or remove a normal worktree after merge. Report the release. A closed tab never releases a worktree by itself.

## Legacy machinery

The old factory, Beads data and runtime records are backed up at `/Users/tal/Backups/software-factory/20261102T201300Z`. Existing detached agents keep their current lifecycle until explicitly migrated. The old `bin/bg`, swarm, question-bead and reconciler paths are for explicit legacy use only. New work uses the visible workflow above. The session-board launch agent and mirror stay disabled so Beads cannot overwrite Linear edits.

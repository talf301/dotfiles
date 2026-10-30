---
name: digest
description: One-screen status of every Claude session (dispatched background beads and interactive tabs) — what needs Tal, what is ready for review, what is working, which tabs are stale, and loose ends — then walk Tal through open questions one at a time and relay his answers to the waiting sessions. Use when the user says "digest", "status", "what's going on", "what needs me", "bearings", or asks about his open sessions or tabs.
---

# Digest

Read-only except for relaying Tal's own answers. Never stop, delete or redirect a session unless Tal says so.

## 1. Collect

- Sessions: `~/dotfiles/iar/agents-digest.sh` prints one JSON object per session: `kind` (background or interactive), `name`, `state` (background: working, blocked, done) or `status` (interactive: busy, idle), `cwd`, `title`, `last_prompt`, `last_message` (first line of the last assistant message), `last_active`, `pr`, `bead`, `bead_in_progress`.
- Slots: `cd /Users/tal/Documents/precog && treehouse status` (leased slots and their holders).
- Dispatched beads: `BEADS_DIR=/Users/tal/.local/share/beads/precog/.beads br list --status in_progress --assignee tal-bg --json`.
- Inbox: precog sessions post under one fixed project (`~/dotfiles/iar/inbox-etiquette.md`). Call `plannotator_inbox` with `action: "read_thread"`, `asked_by: "anyone"` and `project_path: "/Users/tal/.local/share/precog/Precog agents"`. Threads opened before that rule sit under older projects: also call it once with no `project_path` and once for each slot folder in `treehouse status` (`project_path: "/Users/tal/.treehouse/precog-28077b/<n>/precog"`), and merge the results by `thread_id`. A folder with no Inbox project of its own returns the default project's threads again, so expect duplicates. Each call lists the unresolved threads: `subject`, `author.session` (the session that started it), `questions` (`open`, `stopped`, `prompt`), `guide`, `last_author`, `last_at` and `waiting_on_person`. Thread link: `<Inbox url>#thread=<thread_id>`, with the url from `plannotator inbox --background`. Match `author.session` to `sessionId` in `~/dotfiles/iar/agents-digest.sh --all`. The session is stopped when it has no entry or its `pid` is null; its resume command is `cd <cwd> && claude --bg --resume <sessionId>`. If the tool is missing or fails, say so on the count line: never report an empty Inbox you could not read.
- Plannotator: `~/dotfiles/iar/plannotator-open.sh` prints one JSON object per open Plannotator session: `mode` (annotate or review), `url`, `started`, `target` (what it opened), `cwd`, and `session` (the Claude session that opened it, or null).

## 2. Show

Leave out the session running this digest. One line per item, newest first inside each section. Leave out empty sections. Each line: session name, then the title when it differs from the name, then what it last said (at most 100 characters), then its age (from `last_active`).

Agent view marks every ended turn `blocked`, even when the session only answered something, so do not take `blocked` as a question. The session's own first line decides: `QUESTION:` means it needs Tal, `READY:` means ready for review. Without either line, read the last message: it needs Tal only if it ends by asking him to decide something.

An Inbox thread and its session are one item: show the session's line once, with the thread link added.

1. **Needs you.** Interactive sessions with status `waiting` (a question or permission prompt is on screen), sessions whose `last_message` starts with `QUESTION:`, or whose last message (read in full from the transcript) asks Tal to decide something, and Inbox threads with `questions.open` above 0. Show the question.
2. **Ready for review.** Sessions whose `last_message` starts with `READY:`, dispatched sessions (named `<bead-id>: …`, background or herdr tab) that ended their turn without a question while their bead is still in progress, and Inbox guided reviews (`guide`) with `waiting_on_person`.
   **Inbox to read.** Other Inbox threads with `waiting_on_person` and no open question, such as a session's report of what landed. Read the last message with `read_thread` and its `thread_id`, show its first line, and suggest Tal reads and resolves the thread.
3. **Open Plannotator sessions.** Each is a review or document waiting on Tal. Show the target (file name or "review" plus its worktree), the owning session, age and URL, newest first. Sessions older than 2 days whose owner session is gone go under loose ends, with `plannotator sessions --clean` or closing the tab as the suggestion.
4. **Working.** Background sessions in state `working`, and interactive tabs with status `busy`.
5. **Open tabs.** Idle interactive tabs active in the last 3 days.
6. **Loose ends.** Show each with a suggested action, but do not take it:
   - Inbox threads where Tal spoke last (`last_author` is the person) and the session is stopped: his reply is held until that session runs again. Put these first and give the resume command;
   - idle tabs inactive for more than 3 days: suggest closing them;
   - background sessions blocked or done for more than 2 days with no in-progress bead: suggest `claude rm <id>`;
   - `dispatch-bead` slot leases whose bead has no session: suggest resuming or returning the slot;
   - `tal-bg` beads in progress with no session;
   - **Archive?** Inbox threads with no open question whose work has landed (the bead is closed, or its branch is merged or its PR is closed) or whose session is gone (no entry in `agents-digest.sh --all`). Show the thread link and why; resolve one (`resolve_message` with its `thread_id`) only when Tal names it.

End with a count line: needs you, ready, Inbox to read, open Plannotator sessions, working, open tabs, loose ends.

## 3. Walk through

If anything needs Tal, offer to go through the items one at a time. A question that came through the Inbox is answered in the Inbox: give its thread link and do not relay it with SendMessage, so the thread records the answer. For each other item:

1. Read the full last assistant message from the session's transcript (`~/.claude/projects/*/<sessionId>.jsonl`).
2. Show Tal the question, the options and the session's recommendation.
3. Ask with AskUserQuestion. Offer the session's options, plus "Skip".
4. Send Tal's answer to the session with SendMessage, addressed to the session's name as ListAgents shows it. Say it is Tal's answer, quote his words, and add nothing of your own. Confirm in one line that it was sent.

For "Ready for review" items, offer to relay "review" (the session opens the Plannotator review) or "integrate" (the session merges or offers a draft PR, closes the bead and returns its slot).

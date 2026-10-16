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
- Plannotator: `~/dotfiles/iar/plannotator-open.sh` prints one JSON object per open Plannotator session: `mode` (annotate or review), `url`, `started`, `target` (what it opened), `cwd`, and `session` (the Claude session that opened it, or null).

## 2. Show

Leave out the session running this digest. One line per item, newest first inside each section. Leave out empty sections. Each line: session name, then the title when it differs from the name, then what it last said (at most 100 characters), then its age (from `last_active`).

Agent view marks every ended turn `blocked`, even when the session only answered something, so do not take `blocked` as a question. The session's own first line decides: `QUESTION:` means it needs Tal, `READY:` means ready for review. Without either line, read the last message: it needs Tal only if it ends by asking him to decide something.

1. **Needs you.** Sessions whose `last_message` starts with `QUESTION:`, or whose last message (read in full from the transcript) asks Tal to decide something. Show the question.
2. **Ready for review.** Sessions whose `last_message` starts with `READY:`, and dispatched sessions that ended their turn without a question while their bead is still in progress.
3. **Open Plannotator sessions.** Each is a review or document waiting on Tal. Show the target (file name or "review" plus its worktree), the owning session, age and URL, newest first. Sessions older than 2 days whose owner session is gone go under loose ends, with `plannotator sessions --clean` or closing the tab as the suggestion.
4. **Working.** Background sessions in state `working`, and interactive tabs with status `busy`.
5. **Open tabs.** Idle interactive tabs active in the last 3 days.
6. **Loose ends.** Show each with a suggested action, but do not take it:
   - idle tabs inactive for more than 3 days: suggest closing them;
   - background sessions blocked or done for more than 2 days with no in-progress bead: suggest `claude rm <id>`;
   - `dispatch-bead` slot leases whose bead has no session: suggest resuming or returning the slot;
   - `tal-bg` beads in progress with no session.

End with a count line: needs you, ready, open Plannotator sessions, working, open tabs, loose ends.

## 3. Walk through

If anything needs Tal, offer to go through the items one at a time. For each one:

1. Read the full last assistant message from the session's transcript (`~/.claude/projects/*/<sessionId>.jsonl`).
2. Show Tal the question, the options and the session's recommendation.
3. Ask with AskUserQuestion. Offer the session's options, plus "Skip".
4. Send Tal's answer to the session with SendMessage, addressed to the session's name as ListAgents shows it. Say it is Tal's answer, quote his words, and add nothing of your own. Confirm in one line that it was sent.

For "Ready for review" items, offer to relay "review" (the session opens the Plannotator review) or "integrate" (the session merges or offers a draft PR, closes the bead and returns its slot).

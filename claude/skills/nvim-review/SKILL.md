---
name: nvim-review
description: Have the user review a local diff in Neovide (diffview) with inline comments that come back to the agent. Use when the user says "review in nvim", "nvim review", "neovide review", or asks to review a branch or worktree diff in their editor instead of Plannotator.
---

# Neovim review

The user browses the diff in diffview, opens any other file freely, and leaves comments with `<leader>rc`
(line or visual selection). `<leader>re` opens the comments file for edits. The comments go to `$REVIEW_FILE`. Neovide
blocks until the user quits, so the launch is a gate.

## Run it

1. Commit the work first, so the diff is a fixed commit range.
2. Choose the base: the PR group branch or `origin/main`. Use the merge base form `<base>...HEAD`.
3. Launch from the repo or worktree root, in the background, and wait for the exit notification (a review can take
   longer than the foreground timeout):

   ```bash
   REVIEW_FILE="$(mktemp -t nvim-review).md"
   echo "$REVIEW_FILE"
   cd <repo-root> && REVIEW_FILE="$REVIEW_FILE" neovide --no-fork -- -c "DiffviewOpen <base>...HEAD"
   ```

4. Read `$REVIEW_FILE` after Neovide exits.
   - Empty or missing: the user approved.
   - Otherwise each `## path:start-end (rev)` entry has the quoted code and the comment. `rev` is a commit hash (that
     side of the diff, so line numbers refer to that revision) or `working tree` (the current file).
5. Address every comment, commit, and offer another review round.

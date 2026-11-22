# diffreview

Review the changes since the last review in Neovim (diffview.nvim), as a tab of the session's editor float (docview), and send comments back to the agent.

Run it from the agent's pane inside a git repo or worktree. It is linked as `~/.local/bin/diffreview`.

```sh
diffreview open [--base REV]   # diff tab in the editor float, shown on the caller's tab
diffreview mark [REV]          # set the marker (default HEAD)
diffreview status              # marker, and what the next review covers
diffreview base [--base REV]   # the base the next review would use
diffreview close [--quit]      # close the diff tab; --quit exits 2 on unsaved edits
```

Add `--json` for machine output. Outside the caller's pane, pass `--session` and `--agent-block`.

The marker is per session and repo. The diff is a tab (labelled `Review: <repo>`) in the session's one shared docview Neovim. The tab and the comments file are per repo and agent pane, so two agents reviewing the same checkout keep separate tabs and comments, and each tab's Space r s goes to its own agent. Running `diffreview open` again from the same pane replaces that pane's diff tab. The float moves to the caller's Rex tab if it was elsewhere. See `docview --help` for the shared editor, `docview toggle` and `docview ls`.

`--session` must be a Rex session id (`session:` plus hex digits and dashes); anything else, or a session dir that resolves outside `~/.local/state/rexwork/sessions/`, is refused before any write.

## What it shows

The diff runs from the base commit to the working tree, so it covers commits and uncommitted edits to tracked files. diffview cannot list untracked files against a commit; `diffreview` names them in its summary instead.

The base is, in order: `--base`, the session's marker for this repo, or the merge-base of HEAD with the default branch (`origin/HEAD`, `origin/main`, `origin/master`, `main`, `master`).

## In the review Neovim

| Keys | Action |
| --- | --- |
| Space r c | Comment on the line or selection (either side of the diff) |
| Space r e | Edit the saved comments |
| Space r s | Type the saved comments into the agent pane |
| Space r m | Mark reviewed: the HEAD the diff was opened at |

Space r m marks the commit that was HEAD when the diff was opened, not the current HEAD. If the agent committed since, it says `HEAD moved from A to B since this diff was opened; marked A; reopen to review the rest.` `diffreview mark` from a shell with no REV still marks HEAD; `--opened-head SHA` is what Space r m passes.

Space r s first asks Rex what runs in the foreground of the agent pane (`rex block call ... process`). It refuses when the pane is gone, its program has exited, or it shows a shell prompt (zsh, bash, sh, fish, dash, ksh, csh, tcsh), because a shell would run the review text as commands. If Rex cannot tell, it refuses unless forced: `:lua require('config.review').send({force=true})`. docview uses the same send.

Space r d in the session's editor runs `diffreview open` in the background for the current tab's session, agent pane and directory, so it takes the same lock and makes the same tab as a CLI open. In any other Neovim inside a repo it opens the same diff in place. Comments go to the same file. Space r s sends to the tab's agent pane (`vim.t.review_block`, set by `diffreview open` and carried over from the tab Space r d was pressed in); outside the shared editor, `REX_REVIEW_BLOCK` is the fallback.

A comment quotes the repo-relative path and the line numbers of the side it was made on:

    ## src/app.py:4-5 (working tree)
    ## src/app.py:2-2 (old side, 7c672438da7)

## State

- Marker: `~/.local/state/rexwork/sessions/<uuid>/session.json`, key `last_reviewed` (repo toplevel path to commit). Other keys belong to other tools and are kept. The update holds rexwork's lock (`flock` on `<session dir>/.lock`) and replaces the file atomically.
- Comments: `<session dir>/review/<repo>-<hash>.<block uuid>.diff-comments.md`. Marking moves a non-empty comments file aside (`...diff-comments.<commit>-<time>.md`) so the next review starts empty, and clears the inline comment marks.
- Review metadata (including the HEAD and dirty state the diff was opened at): `<session dir>/review/<repo>-<hash>.<block uuid>.viewer.json`.
- The diff tab: `vim.t.diffreview_key` (`<repo>-<hash>.<block uuid>`), `diffreview_head`, `diffreview_dirty`, `review_block`, `review_session`, `review_comments_file`, and a tab-local directory (`:tcd`) at the repo.
- Neovim socket and viewer: docview's, `<session dir>/nvim.sock` and `<session dir>/viewer.json`. Review Neovims from before the shared editor (`/tmp/diffreview-<uid>/*.sock`) are still closed by `diffreview close` and cleared by `mark`.

The marker can only be a commit. Marking with uncommitted changes says so; those changes show again in the next review.

`diffreview` loads `../docview/docview` for its Rex and Neovim helpers and the shared Neovim. Values such as paths reach that Neovim as JSON data, not Lua source. If the editor Neovim does not answer (for example, it waits at a prompt), the command fails with a message instead of hanging.

Tests: `python3 agents/diffreview/test_diffreview.py`, `python3 agents/docview/test_docview.py` and, from `nvim/`, `nvim --headless -u NONE -l tests/review_spec.lua`.

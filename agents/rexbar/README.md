# rexbar

A status bar for a Rex tab, in a thin pane along the bottom of the tab. It is linked as `~/.local/bin/rexbar`. Python 3 standard library only.

```sh
rexbar open   --session S --window W [--block B]  # add the bar to a tab (idempotent)
rexbar close  --session S --window W [--block B]  # remove it
rexbar toggle --session S --window W [--block B]
rexbar fit    --session S --window W              # size it to 3 rows again
rexbar once   --session S [--width N] [--rows N] [--no-color]  # print one frame
rexbar run    --session S [--window W] [--fit]    # the renderer the pane runs
```

`--session` must be a Rex session id (`session:` plus hex and dashes); anything else is refused before any write. Without `--session`, `$REX_SESSION` is used only when Rex knows that session. `--block` names a pane on the tab instead of `--window`. `open`, `close`, `toggle` and `fit` print one line of JSON.

## What it shows

Left to right, most important first; anything that does not fit is cut from the right with `…`:

1. The ticket (or `scratch`), done/total goals, `!N blocked` when any goal is blocked, this session's worktree marker (see [Worktrees](#worktrees)), and the deepest `doing` goal (`+N` when several are equally deep). From `session.json` and `objectives.md`.
2. The history strip: one cell per event, oldest at the left, newest at the right, oldest dropped (`…`) to fit. Colours use the 16-colour palette, so they follow the theme. `--no-color` prints a letter instead.

   | Event | Colour | Letter | Source |
   | --- | --- | --- | --- |
   | picture shown | magenta | p | `show/history.json` |
   | goal done | green | + | `bar/objectives-log.json` |
   | goal doing | yellow | > | same |
   | goal blocked | red | ! | same |
   | goal todo (added or reopened) | bright black | . | same |
   | review marked | cyan | r | `last_reviewed` changes in `session.json`, logged by the bar |
   | promoted | blue | P | `promoted_at` or `filing.at` |
   | landed | bright blue | L | `landed_at` |

3. Agents in the session from `rexboard ls --json`: working, need you (rexboard's `needs_you` group: unseen done, question, permission), idle (everything else, and stale rows), and `+N sub` for working subagents (`children.working`).
4. Plan usage from `ai-usage status --json`: `5h` and `wk` percent for the main agent's provider (Claude unless the main agent is Codex), then the other provider. Yellow from 50%, red from 80%, `–` when unknown. Then the worktrees held across all sessions (`held 4 (oldest 11d) slots 2, 1 landed`; left out when none), the main agent's model, effort, context percent and tokens when rexboard has its `usage`, and the session's `branch` when `session.json` has one.

With one row, everything is on that row. With more rows:

- Row 1: ticket, goal, agent counts, usage.
- Row 2: the strip, then the latest event (`picture: Status bar options 3m ago`; the age updates every second).
- Row 3: one item per agent pane in the session: state (`need you`, `working`, `done`, `idle`), the pane's title cut to 20 cells, `+N` working subagents, and rexboard's message. Panes that need Tal come first, then working ones, then the rest; within each, panes on the bar's own tab first. The first item gets half the row.
- Row 4: every held worktree, landed ones first, then oldest first (`TAL-180 wt 11d landed │ TAL-210 slot 3d`). When none is held, the current goal's parents instead, outermost first (`round 2 › bottom status bar`).
- Row 5: the goal's parents, when row 4 lists worktrees.

Further rows stay empty. When the status would squeeze the goal below about 24 cells, the branch is dropped first, then agents, usage and held worktrees switch to short forms (`w1 y1 i2 +3`, one provider, `held 4/11d`).

## Worktrees

`rexwork start TAL-N` can give a ticket session a worktree (a treehouse slot for precog, a plain git worktree elsewhere) that only `rexwork release TAL-N` gives back. The bar uses rexwork's definition of held (`held_worktree()`, which `rexwork ls` flags): `session.json` has `worktree` and a `worktree_created` record for that same path whose provider is `git` or `treehouse`. An `adopted` worktree, or a path with no creation record, is not held. The age runs from `worktree_created.at` (else `worktree_at`).

- This session, after the ticket on row 1: `wt 3d` (git) or `slot 3d` (treehouse) in cyan; `wt 3d landed` in bold red when the session has `landed_at` (PR opened) and still holds it, since that is the one to release; `wt pending` (yellow) when `pending` lists `worktree`; `wt failed` (bold red) when `worktree_error` is set.
- All sessions, with the usage group on row 1: `held N (oldest AGE)`, then `slots K` when any are treehouse slots, then `, L landed` in bold red. It sits right of the ticket and goal, so it is cut first.
- Row 4 lists them, as above.

The bar lists the state root's session directories at most every 5 s and reads a `session.json` again only when its mtime changes; no subprocess.

Minimized floating panes show as pills at the bottom right of the tab and can cover the right end of the bar's last row. That is why the most important content is at the left, and the strip is left-aligned.

## Goal and review history

`objectives.md` and `session.json` hold only the current state, so the bar keeps its own log, `<session dir>/bar/objectives-log.json`: the last status of every goal (keyed by its parent goals and text) and the last review markers, plus the changes it has seen with their times. The first read only records the baseline. A missing or unreadable `objectives.md` is not taken as every goal removed. Several bars (one per tab) share the log under `bar/lock`, so a change is logged once. Changes made while no bar runs are logged with the time the next bar sees them.

`run` and `once` write nothing in the session except `bar/`.

## Running cost

`rexboard`, `ai-usage` and `rex` are found on a PATH built from `~/.local/bin`, `~/.cargo/bin`, `/opt/homebrew/bin` and the system dirs ahead of the inherited one, and subprocesses get that PATH and `/dev/null` as stdin, so a pane started without the login shell's environment still finds them. Failed runs are appended to `<session dir>/bar/run.log`. At start, `run` shows the cached usage and waits up to 1.5 s for the first `rexboard` answer (normally ~30 ms), so the first frame is real data rather than `–` placeholders. On a resize it clears the screen and scrollback, so a frame drawn at the old size does not linger above the bar.

`run` checks the four files' mtimes once a second and re-reads only what changed. `rexboard ls --json` runs every 4 s and `ai-usage status --json` at most every 60 s, both in background threads, so a slow or failing command never delays a frame. The ai-usage result is cached in `~/.cache/rexbar/ai-usage.json` (`$REXBAR_CACHE`), shared by every bar and by `once`. A frame is written only when it changed: cursor home, then each line followed by clear-to-end-of-line (the frame always has exactly one line per row, so there is no clear-below), with the cursor hidden and line wrap off (both restored on exit). A line that fills the width gets no clear-to-end-of-line, because with wrap off the cursor rests on the last column and the clear would erase the last cell; a clear-below after the last row would do the same. SIGWINCH redraws at once.

## The pane

`open` splits the tab's tiled layer at its root: `session.new_split` with `anchor_id` set to the layer's top split (or its only block), `direction: vertical`, `side: after`. Anchoring the top split makes the bar span the full width under every tiled pane. The pane is labelled `Bar`, opened with `focus: false`, and runs `rexbar run --fit`. Its block is recorded per window in `<session dir>/bar/state.json` under rexwork's session lock (`flock` on `<session dir>/.lock`). `open` reuses the recorded bar, so running it twice leaves one bar.

rexbar only adopts, resizes or closes a pane it can show is its own: the block recorded in `bar/state.json` for that tab, still a tiled pane of the tab, whose foreground program (`rex block call ... process`) is the `rexbar` script running `run` for this session (checked with `ps` when the process is local). A pane that is merely labelled `Bar` is never touched: `open` creates a new bar beside it, and `close`, `toggle` and `fit` leave it alone. A recorded block that is gone or no longer runs rexbar is dropped from `state.json`, so its `panes` map lists only live bars. Only a confirmed mismatch drops a record. If the `process` query or `ps` fails (timeout, Rex busy), the check is retried once after 0.5 s; if it is still unknown, the record is kept, nothing is created, closed or resized, and the command exits 75 (EX_TEMPFAIL) with a message to try again. `run --fit` resizes only when its own `$REX_BLOCK` is the recorded bar, so a `rexbar run` started by hand elsewhere never moves a border.

Sizing aims for 3 rows. The Rex app never makes a tiled pane shorter than 3 rows: on a shown 36-row tab, ratios from 0.89 to 0.989 all gave the bar 3 rows (cell 21×42 px, 100 columns), so 2 is not reachable and 3 is the target. Rex sizes panes by ratio, so the ratio is computed from the tab's rows: `fraction = 4 / tab_rows` (3 rows plus one divider row; a side-by-side split loses one column the same way). On a 36-row tab that is ratio 0.889. The tab's rows come from its tallest pane's real size (`com.superlogical.terminal.size`, rows divided by the pane's layout height). After the split, `open` waits 0.6 s, because the app re-balances a new split on the tab it shows, and then `fit` sets the ratio with `session.resize_pane` and measures the bar up to 3 times, correcting by `(target + 1) / (rows + 1)`. It stops early when a smaller ratio does not change the bar's rows (the app's floor), so it never loops against it.

Pane sizes are real only while an app shows the tab (the size has an `owner`). On a tab nobody shows, every pane keeps Rex's default 80×24, so `open` falls back to a 50-row tab (ratio 0.92) and cannot check the result. `run --fit` corrects the height later: when its own height is not 3 rows and an app sizes the pane, it calls `fit` (at most every 3 s, 3 tries per size). This also undoes a later re-balance by the app. Resizing the bar by hand is undone the same way; open it without `--fit` (by running `rexbar run` yourself) to keep a manual size.

A pane split later at the layout root (not beside a leaf) can end up below the bar; `fit` then reports `the bar is not the bottom of the tab's top split`. Close and reopen the bar.

## Claude and Codex footers

Goal: let Tal hide Claude Code's and Codex's own footers. Not wired yet; neither config was changed.

Claude Code's `statusLine` runs `ai-usage claude-statusline`. Fed a sample status JSON it printed nothing visible; its job is to save the plan limits for `ai-usage status`. Codex's `[tui] status_line` shows `model-with-reasoning`, `git-branch`, `five-hour-limit`, `weekly-limit`, `context-used`, `used-tokens`.

| Item | Covered | How |
| --- | --- | --- |
| Claude 5-hour and weekly limit | yes | ai-usage |
| Codex weekly limit | yes | ai-usage |
| Codex five-hour limit | shown as `–` | ai-usage reports no 5-hour meter for Codex today |
| model and reasoning effort | only with rexboard usage | rexboard `usage.model_display` / `model`, `usage.effort` |
| context used | only with rexboard usage | `usage.ctx_pct` |
| used tokens | only with rexboard usage | `usage.tok` |
| git branch | only when `session.json` has `branch` | rexwork |
| Claude's permission mode, auto-compact warning, key hints | no | not exported by any source |
| Codex's own transient messages | no | not exported |

rexboard's `usage` is `null` for every pane today, because rexboard's hooks and status-line chaining are not installed (`rexboard hooks status` lists the changes `rexboard hooks install` would make). With them installed, rexboard records `model`, `model_display`, `effort`, `ctx_pct`, `ctx_window`, `tok`, `cost_usd`, `quota_5h` per pane, and the bar shows the session's main agent (`main_block` in `session.json`, else the newest agent on the bar's tab, else the newest in the session).

## Tests

`python3 -m unittest test_rexbar.py` from this directory. They cover goal parsing (nesting, tabs, blocked), the change log, history merging and strip fitting, truncation at widths 20/60/120/200 and rows 1/2/3/4, malformed inputs, the usage cache, and open/close/toggle/fit against a stubbed `rex` (including panes labelled `Bar` that are not rexbar's, stale records and dead bars) (including sizing to 3 rows when an app sizes panes, and stopping at the app's 3-row floor), the agent, held-worktree and goal-path rows, worktree ownership (git, treehouse, adopted, landed, pending, failed, ages, mtime caching), relative times advancing, the full-width last cell, and the built PATH. State goes to a temp dir by replacing `STATE`.

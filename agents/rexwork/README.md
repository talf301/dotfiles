# rexwork

One Rex session is one unit of work. `rexwork` starts a session in the
standard layout, turns a scratch session into a Linear ticket, and lands it.
It is linked as `~/.local/bin/rexwork`.

```sh
rexwork start --scratch NAME [--cwd DIR] [--agent claude|codex] [--no-agent] [--no-bar] [--focus]
rexwork start --draft NAME   [--cwd DIR] [--agent claude|codex] [--no-bar] [--focus]
rexwork start TAL-123 [--repo PATH | --cwd DIR] [--no-worktree] [--title T] [--agent ...] [--no-agent] [--no-linear] [--prompt TEXT] [--owner NAME] [--reuse-tab NAME] [--no-bar] [--focus] [-- COMMAND...]
rexwork adopt [--name NAME] [--repo PATH] [--no-bar]
rexwork promote --template
rexwork promote [--draft FILE] [--repo PATH | --no-worktree] [--dry-run] [--no-focus]
rexwork promote --file [--dry-run]      # the filing step; see "Filing needs Tal"
rexwork promote --recover [--dry-run]   # finish an interrupted or partial promotion; asks in a terminal
rexwork promote --clear-filing          # after an interrupted filing Linear has no issue for; asks in a terminal
rexwork land [--yes [--expect DIGEST]] [--keep] [--dry-run [--json]] [--no-pr] [--no-linear] [--linear-only]
rexwork release [TAL-123] [--yes] [--dry-run] [--force]   # free the ticket's worktree once its PR is merged
rexwork status [--json]
rexwork ls [--json]
```

Every command that acts on a session takes `--session SESSION` (default
`$REX_SESSION`). A session id must be a plain Rex id (`session:` plus 8-64 hex
digits and dashes); anything else, such as a path or `..`, is refused before
any file is read or written, and the state directory must be a direct child of
`~/.local/state/rexwork/sessions/` whose name, after resolving links, is still
that id (a link from one id to a sibling's directory is refused). Errors go to stderr as `rexwork: <reason>` with a non-zero
exit. `start`, `adopt`, `promote`, `land` and `release` print JSON on stdout.

## Sessions

| | Scratch | Ticketed |
|---|---|---|
| Session label | `NAME` | `TAL-123 short name` (first words of the title) |
| Tab label | `main` | `TAL-123` |
| Branch | none | `u/tfriedman/tal-123-<slug>` |
| Worktree | never | in a git repo by default: `~/.worktrees/<repo>-tal-123` (git) or a treehouse slot; see [Worktrees](#worktrees) |

Layout, the same for every session: one tab, the agent pane on the left
(`main_block`), the Show pane on the right at 40% (`show_block`, created with
`show --open-pane`, so `show` reuses it), and the status bar along the bottom
(`bar_block`, from `rexbar open --session S --window W`, after the Show pane
exists; `--no-bar` skips it). A failed Show pane or bar is a warning, never
fatal. Neovim and the peek open as floats on demand. `start` never moves focus
unless `--focus` is given.

## start

- `--scratch NAME`: a session with an agent (default `claude`) in `--cwd`
  (default: the caller's directory, else `$HOME`). `--no-agent` gives a shell.
- `--draft NAME`: the same, and the agent's first prompt asks it to interview
  Tal briefly, write `objectives.md`, write the draft ticket and run
  `rexwork promote`.
- `TAL-123`: for Symphony and scripts. Reads the ticket title from Linear
  (`--title` skips the lookup), labels the session, creates or reuses the
  ticket's worktree and branch (see [Worktrees](#worktrees); never forced),
  starts the agent in it, and moves the ticket to
  In Progress unless `--no-linear` or it is already started or closed. If a
  live session for the ticket exists (by state, or by a label whose first word
  is exactly the ticket: `TAL-1` never matches `TAL-10 ...`), it is reused.
  Durable state wins: a session labelled `TAL-1 ...` whose `session.json`
  records another ticket is not reused; a new session starts instead. Reused:
  its Show pane is added if missing, nothing else changes, and the output has
  `"reused": true`. Running it twice is safe.
  `--prompt TEXT` gives the agent a first prompt; `-- COMMAND...` runs that
  command in the main pane instead of an agent.
  With `--no-worktree`, or outside a git repo, the session runs in `--cwd`
  and records no worktree or branch. If the worktree it expects can not be
  made, nothing starts (see [Worktrees](#worktrees)).
  `--owner NAME` stores `"owner": NAME` in a new session's `session.json`
  (Symphony passes `symphony`); a reused session is not claimed.
  `--owner symphony` never creates or adopts a worktree (Symphony owns its
  workspace); `--repo` with it is refused.
  `--reuse-tab NAME` (needs `-- COMMAND`): when the session is reused, its
  tabs are left alone; COMMAND runs in a new background tab NAME with its own
  Show pane and bar, returned as `"tab": {"window_id", "main_block", "show_block", "bar_block"}`.
  No existing pane is touched, and nothing in the session's state directory
  is written (its `session.json` stays byte-identical, or absent). Without
  `--reuse-tab`, a reused session does not run COMMAND (a note says so).
  Once the session or tab exists and COMMAND is running, a failed Show pane
  (or state write, objectives skeleton or focus) is a warning on stderr: the
  exit is still 0, the JSON has `show_block: null` (`tab.show_block` with
  `--reuse-tab`) and the messages in `warnings`. A failure before COMMAND
  runs leaves nothing running (a half-made session is closed) and exits
  non-zero.
  The words after the first bare `--` reach the program unchanged (no shell
  splitting). Option values may start with `-` (`--title "- fix"`). Session
  and tab names may not.

Output (one JSON object):

```json
{"session_id": "session:...", "label": "TAL-123 Fix login", "kind": "ticketed",
 "ticket": "TAL-123", "title": "...", "url": "...", "window_id": "window:...",
 "main_block": "block:...", "show_block": "block:...",
 "bar_block": "block:...", "state_dir": "~/.local/state/rexwork/sessions/<uuid>",
 "repo": "...", "worktree": "...", "branch": "...", "worktree_provider": "git",
 "lease_id": null, "reused": false, "linear": "set In Progress",
 "worktree_action": "create branch", "pending": [], "warnings": []}
```

## adopt

For a session that predates rexwork. Writes state for the caller's session
(`$REX_SESSION`, main pane `$REX_BLOCK`) and adds the Show pane beside the
caller and the status bar if its tab has none (`--no-bar` skips the bar; a bar
failure is a warning). Other panes are left alone. It renames the session
only with `--name`; a session that still has a generated name (such as
`dawn-grotto`) needs `--name`. A label starting with a ticket ID makes the
session ticketed, and the branch and worktree are read from the caller's
directory (or `--repo`). The branch must be the ticket's: `u/tfriedman/tal-1`
or `u/tfriedman/tal-1-...`, never `tal-10-...`; `--repo` on another branch is
refused before anything changes. When the label's ticket differs from the
recorded one, the old ticket's title, url, repo, worktree and branch are
dropped; a session that still holds a worktree rexwork created is refused
(run `rexwork release` first). The worktree's provider is recorded: `git` only
when an earlier session's `worktree_created` record still matches it,
`treehouse` for a slot leased by `rexwork:TAL-123`, else `adopted` (never
removed by `release`; someone else's slot is also recorded as `adopted`).

## promote

Turns a scratch session into a ticket. The agent drafts, Tal files.

1. The agent runs `rexwork promote --template`, fills it in, and writes it to
   `<state dir>/ticket-draft.md`: `# Title` on line 1, the description below.
   (`--draft FILE` copies FILE there.)
2. The agent (or Tal) runs `rexwork promote`. The draft opens in the Neovim
   float. The output shows the issue that would be created.
3. Tal edits, then presses `Space r L` (or runs `:RexworkFile`). Neovim takes
   a snapshot of the buffer and shows the full title, the description's line
   and character counts, its first four lines, and whether a branch will be
   attached. Tal types `yes` to file exactly that snapshot. Closing the float,
   or any answer but `yes`, files nothing. The buffer is not saved; the filed
   text is kept in `ticket-filed.md`.

The prompt looks like this:

```
DRY RUN: nothing is written to Linear.        (dry run only)
Create a Linear issue in TAL, assigned to you, In Progress:
Title: <the full title>
Description: 6 lines, 145 characters, starting:
  | <line 1>
  | ... (first four lines)
Branch: No branch will be attached. The session's directory is in <repo>: ...
Type yes to file exactly this:
```

Filing creates the issue in team TAL, assigned to Tal, state In Progress,
with no labels (so never `agent`; Symphony ignores it). Then the session is
relabelled `TAL-123 short name`, its tab `TAL-123`, and `session.json` gets
`kind: ticketed`, `ticket`, `title`, `url`. It also creates the ticket's
worktree and branch (see [Worktrees](#worktrees)) in `--repo PATH` (given to
`promote`, or to `--file` in a terminal), else, by default, in the repo of the
session's directory (`cwd` in `session.json`, else the main pane's). With
`--no-worktree` it creates none: the repo is recorded as `repo` and the output
says how to attach a branch later (create the worktree, then
`rexwork adopt --repo WORKTREE`). Outside a git repo nothing is attached. The
agent pane keeps its directory; the output names the new worktree.

`--dry-run` checks the request the same way (token, digest, mode), resolves
the team, state and assignee with read-only queries and prints the
`issueCreate` input. It writes nothing else: no `session.json` change, no
`ticket-filed.md`, no Rex rename, no worktree, no Linear write, and it never
recovers an earlier attempt (it reports one as `pending`). It does use up the
authorization: after a dry run, run `rexwork promote` again to file. In the
float, `Space r L` then runs `promote --file --stdin --dry-run`.

### Filing needs Tal

`promote --file` creates the issue only when one of these holds:

- From the float (`--stdin`): the request on stdin carries the one-time token,
  the snapshot Tal confirmed and its sha256 digest (computed in Neovim). The
  content must match the digest, the token must match `promote.json` (which
  holds only its hash, the time and the mode), the authorization must be less
  than 12 hours old, and the mode (real or dry run) must match the one
  `rexwork promote` recorded. The draft file on disk is never read, so a
  rewrite after the prompt changes nothing. The token reaches Neovim in a 0600
  one-shot file (`promote.token`) that Neovim reads and deletes; it is never in
  any process's arguments.
- In a terminal: the draft is read once, shown in full, and that same text is
  filed after Tal types `yes`.

An agent running `--file` without a terminal or the token is refused.

### One confirmation, one issue

The whole filing step holds the session lock. In order: refuse if the
session's promotion is finished; check the request (before anything else); a
dry run stops here; delete `promote.json` (the authorization is used up);
write a `filing` record (`at`, `title`, `digest`, `issue_uuid`, `dry_run`) to
`session.json`; call `issueCreate` with `id: issue_uuid` (Linear accepts a
client-chosen UUID, so the same issue can not be created twice); record
`issue_id`, `ticket` and `done: []`, and set `kind: ticketed`. Then the
post-filing steps run, each added to `filing.done` when it finishes: `label`
(rename the session and tab) and, with a repo, `worktree`.

If an attempt fails or times out after the record is written, nothing is ever
created again for it. It is reconciled only by `issue_uuid` (read-only), never
by title, so an issue Tal made by hand with the same title is never adopted.
Reconciling happens on a real, confirmed `promote --file` (the float's prompt
and the terminal prompt say so) or with `rexwork promote --recover` in a
terminal, which shows what it will adopt and asks for `yes`
(`--recover --dry-run` only reports). If Linear has no issue with that id,
nothing was filed: run `rexwork promote --clear-filing` in a terminal (type
`yes`), then `rexwork promote` and confirm again.

If the issue was created but a post-filing step failed (the Rex rename, or the
worktree), the output lists it in `pending` and exits non-zero. Fix the cause
and run `rexwork promote --recover`: it does only the steps not in
`filing.done`, against the recorded issue id, and creates nothing. Until then
`rexwork promote` points at `--recover` instead of saying "already
ticketed".

With the `work_promote` action (or `--request-draft`) and no draft yet,
rexwork asks the session's agent to write the draft and run
`rexwork promote`.

## land

- Symphony's sessions (`owner: symphony`): refused. Symphony lands its own
  work.
- Ticketed with a worktree: checks that the branch is the ticket's (boundary
  match, as in `adopt`), that the worktree is on it and belongs to the recorded
  repo, and checks again just before pushing. Refuses if the worktree has
  uncommitted or untracked changes and lists them. Otherwise pushes exactly the
  confirmed commit (`git push origin SHA:refs/heads/BRANCH`, never forced), sets
  the upstream (a failure there is only a note), and says so if the local branch
  has moved past the pushed commit ("pushed SHA; local BRANCH is now at NEWER,
  not pushed"). It opens a PR with
  `gh pr create` if the branch has no open PR (title `TAL-123: <title>`, body
  from `objectives.md` and the ticket link), comments the PR link on the ticket
  and moves it to In Review, then closes the session. It never releases the
  worktree (review fixes need it): the plan says "keep the worktree ...", the
  output has `worktree_kept`, and a note says to run `rexwork release` once
  the PR is merged.
- Ticketed without a branch: refused, because nothing would be pushed and no
  PR opened. `--linear-only` moves the ticket to In Review without a PR. It is
  refused when a branch is attached; use `--no-pr` to push without a PR. The dry run
  reports `"no_branch": true` and says "no branch or PR".
- Scratch: moves the state directory to `~/.local/state/rexwork/archive/` and
  closes the session. With `--keep` it does nothing (the state stays while the
  session is open) and says so.

It never merges, approves or enables auto-merge. Any push, PR, Linear write,
archive or close needs `--yes`, or a `y` at a prompt in a terminal that lists
the plan; `--keep` does not waive it. Without either it is refused. It only
closes the session it was called from (or `--session`). `--dry-run` prints the
plan and changes nothing. `--no-pr` and `--no-linear` skip those steps.

The plan Tal sees is the plan that runs. `--dry-run` (`--json` is accepted;
output is always JSON) prints `plan` and a `digest` over the session's live
association (kind, ticket, repo, worktree, branch and branch head, whatever the
chosen variant does with them), the commit that would be pushed, and every
action (including close or keep). So attaching a branch after a Linear-only
plan was shown invalidates it. `land --yes --expect DIGEST` recomputes the
plan under the session lock and refuses with "the plan changed; look again"
if it differs. The terminal prompt does the same with the plan it showed.
The lock is held while landing; `session.json` and the branch head are read
again before the push, the PR, the Linear write and the archive, and land
stops if they changed.

## Worktrees

A ticketed session gets a worktree on `u/tfriedman/tal-123-<slug>` by default
when `start TAL-123` runs in a git repo (`--cwd`, else the caller's directory)
or `promote` files from a session whose directory is in one. `--repo PATH`
picks another repo; `--no-worktree` opts out. Scratch and draft sessions never
get one, and neither do Symphony's (`--owner symphony`).

In order, `start` uses:

1. the caller's directory, if it is already a worktree on the ticket's branch
   (`worktree_provider: adopted`);
2. the live worktree recorded in `session.json`;
3. (treehouse) the slot rexwork already leased for the ticket, found in
   `treehouse status --json` by lease holder `rexwork:TAL-123`;
4. any worktree of the repo already on the ticket's branch (`git worktree list`);
5. a new one from the repo's provider.

So one ticket never leases twice. A path inside a treehouse pool (a slot listed
by `treehouse status` for an opted-in repo, or any path under `~/.treehouse`,
`$REXWORK_TREEHOUSE_ROOT` in tests) is used in steps 1, 2 and 4 only if its
pool row has holder `rexwork:TAL-123` and, when one is recorded, the same lease
id. A slot anyone else holds is never used. Every start also checks its final
launch directory against the pool: a ticket start that expects a worktree
refuses (see below) if that directory is inside a slot not held by
`rexwork:TAL-123`. A scratch or draft session, a `--no-worktree` start and
Symphony's `--reuse-tab` tab expect no ticket worktree, so for them a launch
directory inside any slot they do not hold is redirected to that repo's main
checkout, with a note.

**Ownership.** rexwork owns a worktree only when it made it: right after its
own `git worktree add` it records `worktree_created` (`path`, `repo`, `branch`,
`gitdir` and its inode, `provider`, `at`); for treehouse the record holds the
lease id. A path name alone never counts, so a worktree made by hand at
`~/.worktrees/<repo>-tal-123` is `adopted`. A later session for the same ticket
takes over an earlier session dir's record only while the worktree is still
that checkout (same git dir and inode). When it does, the old record is cleared
(`worktree_moved_to`), so only one session holds it.

**Concurrency.** `start TAL-123` takes two flocks under the state root:
`locks/TAL-123.worktree.lock` for the whole start (waits 30 s, then fails
saying another start is acquiring the worktree) and
`locks/TAL-123.session.lock` around discovery and around creating and
registering the session (waits 25 s). After the worktree is acquired it looks
again for a live session under the session lock, and reuses one that appeared
meanwhile through the same dispatch as the first lookup, so `--reuse-tab NAME
-- COMMAND` still runs COMMAND in a new tab. The session records the worktree
if it has none and is not Symphony's; otherwise the worktree this start just
made (a fresh lease or `git worktree add`, so clean) is given back at once
(`treehouse return --if-lease-id`, or `git worktree remove` keeping the branch)
and a warning says so, so nothing is left that no record points at. Symphony's path
(`--owner symphony`) takes only the session lock, so a long treehouse lease never
blocks it within its 60 s budget.

### Providers

The provider is per repo, opt-in, in `$XDG_CONFIG_HOME/rexwork/config.json`
(else `~/.config/rexwork/config.json`; `$REXWORK_CONFIG` replaces both). Tal's
file is `/Users/tal/nikud/xdg_config_home/rexwork/config.json`, linked from
`~/.config/rexwork/config.json` because Rex runs actions without
`XDG_CONFIG_HOME` (treehouse itself reads `~/.config/treehouse`):

```json
{"repos": {"/Users/tal/Documents/precog": {"worktrees": "treehouse"}}}
```

A repo matches by its main checkout or top level. A `treehouse.toml` alone
does not opt in.

- `git` (default): `git worktree add` at `~/.worktrees/<repo>-tal-123`, from
  the existing branch, tracking `origin/<branch>`, or a new branch from the
  default base.
- `treehouse`: from the repo, `treehouse get --lease --json --lease-holder
  rexwork:TAL-123 -b BRANCH` for a new branch. For a branch that exists
  (resuming a ticket) it leases without `-b` (the slot comes detached at the
  pool's base) and runs `git checkout BRANCH` (or `--track -b BRANCH
  origin/BRANCH`) in the slot. Paths are `~/.treehouse/<pool>/N/<repo>`.
  `session.json` gets `worktree`, `branch`, `worktree_provider: treehouse`,
  `lease_id`, `worktree_at`, and `repo` as the slot's main checkout. `get`
  warms a cold slot, so the timeout is 15 minutes; a timeout says to check
  `treehouse status` and run the command again (it reuses the lease). Leases
  held by anyone else (such as `agent:TAL-159-*`) are never touched.

**No worktree, no agent.** When a ticket start expects a worktree (default on
in a repo, or `--repo`) and does not end up with a usable one, it starts
nothing: no Rex session, no agent, nothing in the main checkout. That covers a
lease that failed or timed out, a slot held by someone else, a failed
`git worktree add`, a failed checkout in the slot, and a launch directory that
would have been redirected to the main checkout. It exits non-zero naming the
cause and the two ways forward: fix it and run `rexwork start TAL-123` again,
or pass `--no-worktree` to work in the current checkout deliberately. It never
falls back to the other provider. A lease already taken stays held by
`rexwork:TAL-123`, so the rerun reuses it instead of leasing again, then starts
the agent in the worktree. An existing session for the ticket is left as it is.
Such a session can still have `pending: ["worktree"]` (from an older rexwork);
rerunning `start` retries only the worktree. In `promote`, which launches no
agent, the step is `worktree` in `filing.done`, finished by
`promote --recover`.

### When a worktree is released

Never at `land`: review fixes need the worktree after the PR opens. A
ticketed session is finished when its PR is merged, which rexwork does not
see, so release is the explicit `rexwork release`. `land` says so, and
`rexwork ls` flags every session that still holds a worktree, with its age.

`rexwork release [TAL-123] [--session S] [--dry-run] [--yes] [--force]`:

- Finds the session by `--session`/`$REX_SESSION`, or by ticket (the one
  session dir that holds that ticket's worktree; live or gone).
- Acts only on a worktree with a `worktree_created` record; an `adopted` one
  is refused, as are Symphony's sessions. For git it first checks that the live
  worktree is still the one it created: same path, repo, branch, git dir and
  git dir inode. Anything else is refused and left alone.
- Refuses when the worktree has uncommitted or untracked changes, or commits
  on no `origin` branch (`git log HEAD --not --remotes=origin`, local refs, no
  fetch; a repo without `origin` counts as unpushed). `--force` overrides
  that only in a terminal, after the work to be discarded is listed and Tal
  types `yes`; `--yes` does not waive it.
- Otherwise it needs `--yes`, or `y` at a terminal prompt listing the plan.
  `--dry-run` prints the plan.
- treehouse: checks `treehouse status` first; if the slot is now leased by
  someone else it refuses, and if the lease is gone it only forgets the
  record. Then `treehouse return [--force] --if-lease-id ID PATH`.
- git: `git worktree remove [--force] PATH`. If the path is already gone it
  only clears the record and says to run `git worktree prune` by hand; it never
  prunes repo-wide (that could drop the metadata of worktrees on an unmounted
  volume).
- Never deletes the branch. In one update `session.json` drops `worktree`,
  `lease_id`, `worktree_provider`, `worktree_created`, the pending `worktree`
  step and `worktree_error` (so a released partial slot is not retried), and
  records `released` (`worktree`, `provider`, `lease_id`, `branch`, `at`).

## State

`~/.local/state/rexwork/sessions/<uuid>/` (uuid is the session id without
`session:`):

| File | Owner |
|---|---|
| `session.json` | rexwork: `label`, `kind`, `ticket`, `title`, `url`, `repo`, `worktree`, `branch`, `worktree_provider`, `lease_id`, `worktree_at`, `worktree_created`, `pending`, `worktree_error`, `released`, `worktree_moved_to`, `agent`, `cwd`, `owner`, `session_id`, `window_id`, `main_block`, `show_block`, `bar_block`, `created_at`, `landed_at`, `pr_url`, `filing`. Other keys (diffreview's `last_reviewed`) are kept. Written atomically under a lock. |
| `objectives.md` | the agent; rexwork writes a skeleton |
| `ticket-draft.md` | the agent and Tal; read by `promote` |
| `ticket-filed.md` | rexwork: the exact text of the last filing (or dry run) |
| `promote.json` | rexwork: the open authorization (token hash, time, mode, repo) |
| `promote.token` | rexwork: the one-time token for the draft's Neovim, which reads and deletes it |
| `show/` | `show` |
| `bar/` | `rexbar` |
| `review/` | `diffreview` |

## Rex actions

| Action | Key | What it does |
|---|---|---|
| `work_start` "Start work" | cmd+shift+n | picker: Scratch session / Draft a ticket with an agent, then a name; runs `rexwork start ... --focus` |
| `work_promote` | none | `rexwork promote --request-draft` for the focused session |
| `work_land` | none | `rexwork land --dry-run --json` for each choice (close, keep; with `--linear-only` when no branch is attached; refuses Symphony's sessions), shows each choice's plan, then `rexwork land --yes --expect DIGEST` with the digest of the plan picked |
| `work_review` | none | `diffreview open` in the focused pane's directory |
| `work_editor` | cmd+e | `docview toggle` for the focused pane |
| `work_show_toggle` "Work: show/hide the picture pane" | cmd+i | `show --toggle --session S --block B` for the focused pane's tab |
| `work_bar_toggle` "Work: show/hide the status bar" | cmd+b | `rexbar toggle --session S --block B` for the focused pane's tab |

## Linear

GraphQL with the key in `$LINEAR_KEY_FILE` or `~/.linear_key` (as
`linear-description` uses). The key is never printed.

## Tests

`python3 -m unittest discover -s ~/dotfiles/agents/rexwork/tests`

`REXWORK_AGENT_STUB=/path/to/program` replaces the agent program (it gets the
agent name and the prompt as arguments). Tests use it to start sessions
without a real agent. `REXWORK_SHOW=/path` replaces the `show` program and
`REXWORK_BAR=/path` the `rexbar` program (tests point them at failing ones).
`REXWORK_TREEHOUSE=/path` replaces `treehouse`: the suite uses a stub whose
pool slots are git worktrees of a temp repo, and never runs the real one.
Worktree tests use real git in temp repos.

# symphony

Tal's Python implementation of OpenAI's Symphony spec (`docs/SPEC.md`), Codex workers only.
Tal labels a TAL ticket `agent`; a local service runs a Codex agent on it in its own
workspace, parks it (label `waiting`) while experiments run, wakes it when a run or Tal comments, and asks Tal
on Linear when it needs him. Linear is the source of truth.

- `docs/PLAN.md`: why, and what we take from the spec.
- `docs/DESIGN.md`: module interfaces and the test coverage map.
- `WORKFLOW.md`: the live config and agent prompt (reloaded on change).
- `local.symphony.plist`: the launchd job.

## Steps only Tal can do

Nothing below has been done by an agent.

1. **Log in to Linear's MCP server once:**
   `uv run --project ~/dotfiles/agents/symphony symphony login`
   It registers Symphony as its own OAuth client (dynamic registration, public client, PKCE,
   redirect to `http://127.0.0.1:47615/callback`), opens the browser for you to approve, and
   stores the token record in the Keychain (service `symphony-linear-mcp`). No API key, no
   file, nothing logged. Run it again if `check` or the log says `tracker_auth`.
2. **Linear setup on the TAL team:** nothing to add. Symphony uses the existing states
   (Todo, In Progress, In Review, Done, Canceled, Duplicate) and the labels `agent` and
   `waiting`, which exist.
3. **Repos.** Fill `workspace.repos` in `WORKFLOW.md` (`name: path`), then label code tickets
   `repo:<name>`.
4. **Preflight.** `uv run --project ~/dotfiles/agents/symphony symphony check` (one read-only
   Linear MCP call: `get_user` for `me`).
5. **For the experiment-run bridge:** put a `symphony` executable on PATH (for example
   `uv tool install --editable ~/dotfiles/agents/symphony`) before reinstalling the bridge, so
   its installer pins it; the bridge's `linear` provider writes through
   `symphony bridge-event`.
6. **Unload the run-ticket watcher**, so two services don't run the same tickets:
   `launchctl bootout gui/$(id -u)/local.run-ticket-watch`
7. **Load Symphony:**
   ```
   mkdir -p ~/.local/state/symphony
   cp ~/dotfiles/agents/symphony/local.symphony.plist ~/Library/LaunchAgents/
   launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/local.symphony.plist
   ```
   The plist assumes the code at `~/dotfiles/agents/symphony` (after this branch merges) and
   `uv` at `/opt/homebrew/bin/uv`. Stop with
   `launchctl bootout gui/$(id -u)/local.symphony`.

## Install and run by hand

```
cd ~/dotfiles/agents/symphony
uv sync
uv run symphony check                 # preflight
uv run symphony run                   # or: uv run symphony [PATH]; Ctrl-C stops
uv run --extra test pytest -q         # tests: fakes only, no network/Linear/Codex/Rex
```

Exit codes: 0 normal stop, 1 startup failure or abnormal exit, 2 missing workflow file.
SIGINT/SIGTERM interrupt running turns (a restart resumes the same threads) and exit 0.

## Operate

| Command | What it does |
|---|---|
| `symphony run [PATH \| --workflow PATH]` | The service. `symphony PATH` also works (spec §17.7). |
| `symphony check [PATH]` | Load, validate, check the Codex daemon socket, one read-only Linear MCP call. Says to run `symphony login` when the login is missing or expired. |
| `symphony ls` | Ticket, Linear state, claim (running, parking, retry n in Xs), thread id, tokens, last event; from `status.json`, flagged STALE if old or the service pid is gone. |
| `symphony open TICKET` | Opens (or reuses) the ticket's tab in the hidden Rex session `symphony`, running `codex resume <thread>`, and shows it in the connected Rex app. |
| `symphony logs TICKET [-n N]` | The ticket's JSONL log lines (default last 50; `-n 0` all). |
| `symphony board` | Agent tickets from Linear grouped by state: In Review first with the agent's latest comment, then Todo, In Progress (`running`, `waiting on runs` flags), last 5 Done. |
| `symphony show TICKET [-n N]` | Description and the last N comments (default 10, `-n 0` all), oldest first, with author (Tal, agent, experiment-run, symphony). |
| `symphony reply TICKET TEXT` | Posts TEXT as Tal (unsigned, so it wakes a parked ticket); `-` reads stdin. Refuses text starting with 🤖. |
| `symphony login [--port N]` | One-time browser OAuth to Linear's MCP server; token to Keychain `symphony-linear-mcp`. Starts from an empty record each time. With a browser on another machine (for example over Rex), the 127.0.0.1 page it lands on won't load: paste that page's address at the prompt. |
| `symphony bridge-event --issue TAL-N --json -` | Used by the experiment-run bridge. Reads `{"run", "status"}` and/or `{"event_id", "event"}` on stdin; creates or edits the run's status comment and posts the event comment unless one with that event ID exists. Prints a JSON result. Exit 0 written or already there, 2 bad input, 3 not logged in, 1 other failure (retry is safe). |

State lives in `~/.local/state/symphony/` (`$SYMPHONY_STATE_DIR` overrides):
`symphony.jsonl` (structured log), `status.json` (snapshot written each tick),
`launchd.out`/`launchd.err`. Per-ticket thread records and Rex tab ids live in
`~/symphony-workspaces/.symphony/<ticket>/`.

**Stop one ticket:** remove its `agent` label; the worker stops after its current turn and
the workspace and thread are kept. Moving it to Done/Canceled/Duplicate interrupts the
worker and removes the workspace (unless `before_remove` fails; then it is kept and you get
a notification). **Stop everything:** unload the launchd job.

macOS notifications: ticket reaches In Review or Done, 3 failed attempts in a row,
workspace kept after a failed removal. Agent comments post as Tal, so Linear itself won't
notify him.

## Approval, sandbox and user-input posture (spec §10.5)

- **Trust boundary:** Tal's laptop, Tal's own tickets only: team `TAL`, label `agent`,
  assigned to the logged-in user. Ticket text is still untrusted input to the agent.
- **Thread settings** on `thread/start` and `thread/resume`: `approvalPolicy: on-request`,
  `sandbox: workspace-write`, `approvalsReviewer: auto_review` (from `WORKFLOW.md`, passed to
  Codex unvalidated). Codex's auto-reviewer decides routine sandbox escapes.
- **Symphony never approves anything.** Any approval or permission request that reaches it,
  any user-input request and any MCP elicitation is declined or cancelled, the turn is
  interrupted, and Symphony posts a signed `🤖 symphony` "Question for Tal" comment and moves
  the ticket to In Review. The worker exits; nothing waits indefinitely. Tal's reply wakes
  the ticket on the same thread.
- **Dynamic tools:** none are advertised; an unsupported tool call gets a failure result and
  the turn continues.
- **Credentials:** Symphony's Linear MCP OAuth token lives only in the Keychain
  (`symphony-linear-mcp`), is read host-side, never logged, and never put in any child
  environment; the shared daemon never receives it. Keychain writes go through
  `security -i` on stdin, so the token is not in any process argument list. The agent's own
  Linear access is Codex's Linear MCP over its own OAuth.
- **Budget:** `max_turns` 20 per dispatch; per-ticket token cap 3M (label `budget:<n>k`
  overrides). Hitting the cap posts a question and moves the ticket to In Review.
- **Prompt rules** (in `WORKFLOW.md`): compute only as the ticket authorizes; any new AWS or
  Baseten resource needs a question every time; never merge, approve or push to main; code
  ends at In Review; park with the `waiting` label; Done only with evidence and no runs in flight; Claude review via the
  `review` skill with `claude -p --permission-mode plan`; no claude.ai artifact publishing.

## Linear adapter profile (spec §11.2)

| Item | Value |
|---|---|
| `tracker.kind` | `linear` |
| Transport | Linear's MCP server (`https://mcp.linear.app/mcp`, streamable HTTP) through the official `mcp` Python SDK, OAuth from `symphony login`; tokens refresh without a browser |
| `tracker.provider` keys | `team_key` (required, e.g. `TAL`); `endpoint` (default `https://mcp.linear.app/mcp`); `keychain_service` (default `symphony-linear-mcp`); `assignee` (default `me` = the logged-in user, resolved with `get_user`; else a Linear user id); `waiting_label` (default `waiting`); `wake_states` (default `[In Review]`); `in_progress_state` (default `In Progress`); `human_review_state` (default `In Review`); `comment_window` (default 50, 1..250: comments per `list_comments` page) |
| Secrets | none in the environment; the OAuth token record is in the Keychain only |
| Default states | active `[Todo, In Progress, In Review]`; terminal `[Done, Canceled, Duplicate]` |
| Validation errors | missing or blank `team_key`, non-list `wake_states`, non-string optional keys, bad `comment_window` -> `invalid_tracker_config`; other kind -> `unsupported_tracker_kind`. A missing or expired login is `tracker_auth` at the first call |
| Reads | candidates: `list_issues` per active state (`team`, `label` = first required label, `state`, `limit` 50, `fields`, `cursor`); for tickets assigned to the viewer, `get_issue` with `includeRelations` for `relations.blockedBy` (id and title only), then `get_issue` (`fields: [status]`) per blocker, cached per poll; `list_comments` (all pages) only for parked tickets. ID refresh: `get_issue` with all fields and relations; "not found" and other-team issues are omitted |
| Identity | `id` = issue UUID (`uuid` field); `identifier` = `TAL-N` (`id` field); `native_ref` = `{linear_issue_id, team_key}` |
| Normalization | state names kept as Linear spells them, compared trimmed and lowercased; labels trimmed, lowercased, deduped; priority 0 -> null, 1..4 kept; RFC 3339 timestamps parsed, bad ones -> null; `branch_name` from `gitBranchName` |
| Parked | `waiting` label (on an In Progress ticket) or a wake state (In Review) |
| `dispatchable` | false if not assigned to the viewer (or configured assignee); false if any blocker is not terminal (unknown or unreadable state blocks); when parked, true only if a wake comment is newer than the start of the agent's last turn (none recorded -> any wake comment) |
| Wake comments | first line not starting with `🤖 agent`, `🤖 symphony` or `🤖 experiment-run status`: bridge events (`🤖 experiment-run`) and unsigned (Tal) comments wake |
| Malformed records | state-list read: logged as `linear_malformed_issue` and omitted; id refresh: `tracker_response` |
| Writes (deviation 3) | before each turn a woken ticket loses the `waiting` label and moves In Review -> In Progress (`save_issue` with `removeLabels`/`state`); escalation posts a signed comment (`save_comment`) and moves to In Review; notes post a signed comment; `bridge-event` writes the bridge's run-status and event comments |
| Agent tools | none advertised (the agent uses Codex's Linear MCP) |
| Errors | `TrackerError`: `tracker_auth` (no or expired login: run `symphony login`), `tracker_request` (network, timeout), `tracker_status` (HTTP error), `tracker_rate_limited` (HTTP 429 or a rate-limit tool error), `tracker_tool_error` (a tool reported an error), `tracker_response` (unusable result), `tracker_pagination`. Messages never contain tokens |

## Deviations from the spec

1. **Transport (§10.1):** workers use Tal's shared Codex app-server daemon over its Unix
   socket, so threads are live in the Codex app and Rex. `codex.transport: stdio` is the
   spec's per-worker subprocess fallback.
2. **Workspace removal (§9.4):** if `before_remove` fails, the workspace is not deleted; it is
   logged (`workspace_remove_skipped`) and Tal is notified. `before_remove` runs
   `git worktree remove` without `--force`, so a dirty worktree is never deleted. A plain
   (non-worktree) workspace with any content is moved to `<workspace.root>/.archive/` instead.
3. **Orchestrator writes to Linear (§11.5):** escalations (comment + In Review) and, when a
   woken ticket is dispatched, removing its `waiting` label or moving it from In Review to In
   Progress. All other writes come from the agent or from the bridge (through
   `symphony bridge-event`).
4. **Hook environment:** hooks also get `SYMPHONY_ISSUE_IDENTIFIER`, `SYMPHONY_ISSUE_ID`,
   `SYMPHONY_ISSUE_LABELS` and `SYMPHONY_REPO`.
5. **Graceful park (§8.5):** a ticket the agent just parked (`waiting` label or In Review)
   finishes its current turn instead of being interrupted. Any other active but unroutable
   ticket (label removed, reassigned, newly blocked) is interrupted at once, so removing the
   `agent` label is a kill switch.
6. **Thread record location:** `<workspace.root>/.symphony/<key>/thread.json`, outside the
   git worktree, so `git worktree add`/`remove` are not disturbed.
7. **CLI:** `symphony run [--workflow PATH]`, the operator subcommands, `login` and
   `bridge-event` are additive; the
   spec's `symphony [PATH]` form still works.

8. **Tracker transport (§11.2):** Linear's MCP server over OAuth instead of GraphQL with a
   personal API key (Tal can't create one). Same adapter interface.

Not used from the spec: the Elixir reference, its sample `WORKFLOW.md`, the `linear_graphql`
agent tool, the HTTP dashboard (§13.7), SSH workers, and a persisted retry queue (retries
rebuild from Linear; only the thread id is persisted).

## Accepted gaps (v1)

- Laptop sleep stops polling, the bridge and Codex.
- No phone push beyond Linear's own notifications.
- The agent's Linear MCP can reach any ticket, not just its own; the prompt restricts it.
- One machine; `max_concurrent_agents` is 3.
- A worktree starts from the repo checkout's current HEAD, not a fresh `origin/main`.
- Real-integration checks (§17.8) are the trial tickets in `docs/PLAN.md`, not automated tests.

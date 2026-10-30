# W23b: Wider factory scan and Linear as ledger vs. view

Bead: precog-y8kf (epic precog-am8w, spec item W23 parts 2 and 3). Date: 2026-10-30.
The `br` → `bd` spike (W23 part 1) is a separate bead, precog-b7tf.

## Summary

- **No candidate replaces a W-item.** Gas Town parts do not run outside a Gas Town workspace.
  Gas City, Agent Mail, routines and GitHub Agentic Workflows each add a store, a server or a
  cloud runner that cannot see local state.
- **One W-item must change (W10).** `claude agents` stops the process of a waiting session after
  about 1 h. The current W10 `live`/`zombie` predicates then call a session that waits for Tal a
  zombie. Today 6 of 8 `blocked` background sessions have no pid.
- **Linear stays a view (W25 as written).** As a ledger it loses on offline behavior, OAuth
  fragility, org visibility and gating cost. It only ties on rate limits.
- No tracker switch is recommended here, so this report adds no migration bead.

## 1. Wider scan

Verdict scale: **replace** (removes a W-item), **simplify** (makes a W-item smaller), **nothing**.

| Candidate | State (2026-10-30) | Verdict | Affects |
|---|---|---|---|
| Gas Town Witness, `gt feed --problems`, `gt escalate`, `gt release` | gt 1.1.0 installed; last release v1.2.1, 2026-06-06 | nothing (patterns already in spec) | W10, W12, W2 |
| Gas City (new since first survey) | v1.5.1, 2026-10-24 | nothing now; recheck only if W23a picks `bd` | W10–W14 |
| MCP Agent Mail (Rust) | v0.3.39, 2026-10-13; Python version deprecated | nothing | W2, W13, W16 |
| Claude Code agent view (`claude agents`) | CLI 2.1.314 | simplify W10, W12; **changes W10 predicate** | W10, W12 |
| Claude Code scheduled tasks (routines) / Desktop local tasks | research preview | nothing; keep launchd | W14, E12 |
| GitHub Agentic Workflows (`gh aw`) | v1.0.1, 2026-10-27 | nothing | — |
| OpenAI Symphony | last push 2026-09-15 | no change; spec already ports SPEC.md | W8, W10–W14 |

### 1.1 Gas Town components

What they do (from `gt <cmd> --help`, gt 1.1.0):

- **Witness**: per-rig monitor. Detects stalled sessions, nudges them, cleans up zombies.
- **`gt feed --problems`**: `STALLED` = hooked work + 15 min no progress; "GUPP violation" =
  30 min. Same idea as W10 `stalled` (`stall_after_min: 30`).
- **`gt escalate`**: escalations are beads with label `gt:escalation`, routed by severity.
  `stale_threshold` 4 h re-escalates, `max_reescalations` 2. Same idea as W2 question beads with
  a 4 h deadline and one re-ping.
- **`gt release`**: runs `bd update <id> --status=open --assignee=` (seen in its error output).
  This is exactly the W12 zombie release.

Can they be adopted alone? No. Test in an empty directory:

```
$ gt feed --problems --plain   -> Error: not in a Gas Town workspace (run from ~/gt or a rig directory)
$ gt escalate list             -> Error: not in a Gas Town workspace
$ gt witness status            -> needs a <rig> argument (a Gas Town rig)
$ gt release foo-1             -> bd update foo-1 --status=open --assignee=: Error: no beads database found
```

All need a Gas Town HQ, its roles (Mayor, Deacon, Witness), tmux sessions and `bd`. The project
stopped releasing in June 2026 (v1.2.1); its successor is Gas City. **Verdict: nothing.** The
spec already holds the useful patterns (stall threshold, escalation as a bead, re-ping, release).

### 1.2 Gas City (new)

"Orchestration-builder SDK ... a controller/supervisor loop that reconciles desired state to
running state", with health patrol, Beads-backed waits and mail (README). This is the same shape
as W10–W14. Requirements (README table): tmux always; `bd` ≥ 1.3.0 with Dolt ≥ 2.6.0 and
`flock`, or a file store (`GC_BEADS=file`). It has no `br` provider and no Rex provider (it has
herdr, which W20–W22 remove). **Verdict: nothing now.** Adopting it means a tracker switch and
tmux. If W23a recommends `bd`, check Gas City again before W10 starts.

### 1.3 MCP Agent Mail

Rust rewrite (`mcp_agent_mail_rust`) is the maintained one; the Python repo says "Deprecated".
It gives agent identities, threaded inboxes, advisory file reservations with a pre-commit guard,
and a Human Overseer web composer. It runs as a long-lived server on `127.0.0.1:8765`, with a
per-project Git archive plus SQLite. It recommends "Beads as the task-status authority" and
"Use the Beads issue id ... as the Mail `thread_id`".

Note for W23a: the spec says Agent Mail needs `bd` ≥ 0.57 and Dolt. The Agent Mail README says
the opposite: its installer installs `br` (beads_rust) and aliases `bd` to it.

**Verdict: nothing.** It is a ninth store of messages, beside the Plannotator Inbox, which works
against outcome 1 (one ledger). Its file reservations act at commit time; W14 checks file overlap
at dispatch time, which is earlier and cheaper. The one useful idea (bead id = thread key) is
already in W2.

### 1.4 Claude Code agent view

Source: code.claude.com/docs/en/agent-view, plus live `claude agents --json --all` on 2026-10-30.

- `claude agents --json` is "the supported way to read session state from outside Claude Code".
  Fields: `state` (`working`, `blocked`, `done`, `failed`, `stopped`), and while the process is
  alive `pid` and `status` (`busy`, `waiting`, `idle`), plus `waitingFor`.
- The supervisor restarts a session that "exited unexpectedly". After a reboot, sessions active
  in the last 48 h show `failed`; "Replying or attaching restarts them from the saved
  conversation".
- **Idle cleanup:** "finished or waiting sessions unattached for about an hour get their process
  stopped". Replying resumes them.

Live evidence (49 sessions): 8 background sessions are `blocked`; **6 of them have no `pid` and
no `status`**. Example rows: `blocked None None precog-gywc: many-bindings benchmark`.

Effect on the spec:

- **W10 (change).** W10 defines `live` as "a pid is present and the process exists" and `zombie`
  as "`in_progress` and none of its attempts has a live session". A session that waits on Tal for
  more than 1 h has no pid, so W10 would class its bead `zombie` and W12 would release its claim.
  Proposed predicates:
  - `waiting`: the attempt's session has `state: blocked` (with or without a pid).
  - `zombie`: the bead is `in_progress` and no attempt session has state `working` or `blocked`,
    and none has a live pid. A missing session, or `failed`/`stopped`, counts as not running.
  - Add a fixture: `blocked` with no pid → not `zombie`.
- **W12 (simplify).** The supervisor already restarts crashed sessions, so true zombies are rarer.
  For a `failed` session after reboot, the flag text should give the resume command
  (`claude --resume <sessionId>`) instead of only "flagged".
- W10 can use `waitingFor` to tell a permission prompt from a question, for the board.

### 1.5 Scheduled tasks (routines) instead of launchd

Source: code.claude.com/docs/en/routines and /desktop-scheduled-tasks.

| | Cloud task | Desktop local task | launchd (current) |
|---|---|---|---|
| Sees `br` store, treehouse, `claude agents` | No ("fresh clone") | Yes | Yes |
| Runs with app closed / machine asleep | Yes / Yes | No / No (asleep = skipped) | Yes / on wake |
| Minimum interval | 1 h | 1 min only when Claude sets it; 1 h in forms | any |
| Each run | a full model session | a full model session | a plain script |

The reconciler is a deterministic script on a few-minute tick (W14, E12). Cloud tasks cannot see
local state; local tasks need the Desktop app open and spend a model session per tick.
**Verdict: nothing. Keep launchd.**

### 1.6 GitHub Agentic Workflows

Source: github.github.com/gh-aw; repo `github/gh-aw` v1.0.0 (2026-10-26), v1.0.1 (2026-10-27).
Markdown workflows compile to GitHub Actions; the agent runs read-only, and declared "safe
outputs" are written by a separate job; `staged: true` previews outputs. Engines include Claude
Code. They run on Actions runners, which cannot see the external Beads store, treehouse or local
sessions. **Verdict: nothing.** Its safe-outputs/staged idea is already in W11 (dry run and
per-class switches).

### 1.7 OpenAI Symphony

No change since the spec was written (repo last push 2026-09-15). OpenAI does not maintain it as
a product. The reference implementation reads Linear only; the spec ports the loop to `br`.
Nothing to change.

## 2. Linear: ledger vs. view

Context: workspace "Scaled Cognition"; team `RES` (Research) has `visibility: public`
(Linear MCP `list_teams`, read-only). Tal's Linear user has `isAdmin: false` (`whoami`).

| Failure mode | Linear as ledger | Linear as view (W25) | Evidence |
|---|---|---|---|
| Write latency, rate limit (~50 writes/h) | OK. Limits are far above need. | OK | Linear docs: API key 2,500 req/h per user, OAuth app 5,000 req/h, 3M/2M complexity points/h. Round trip to `api.linear.app` from this Mac: 0.22–1.25 s (5 unauthenticated probes, HTTP 401). Local `br comments add`: 0.13–0.15 s (5 runs, scratch store). |
| Offline / network errors | **Bad.** Every `ask`, claim and comment fails when the network fails. The reconciler would hit `source-error` (no actions) on each blip. | Fine. A failed mirror write is retried next tick. Work goes on. | The audit lists ENOTFOUND and 429 as recurring hand retries. `br` is a local file. |
| OAuth fragility | **Bad.** Since 2026-04-01, authorization-code tokens last 24 h and refresh tokens rotate; parallel agents can race a rotation and force a manual re-auth. Client-credentials tokens last 30 days but need an OAuth app with that grant switched on. The claude.ai Linear connector is not reachable from a launchd script. | Contained. Only the reconciler writes. One token, refreshed in one place. A failure stops the mirror only. | Linear OAuth 2.0 docs. |
| Visibility to the org | **Bad.** `RES` is public. Every question, `decision:` comment, `attempt:` comment (slot paths, session ids), budget and `reconcile:` record would be visible org-wide. | Good. Only title, status, bead id, Inbox link, short summary (Tal's 2026-10-30 decision). | `list_teams` shows `visibility: public`. |
| Cost of gating outward writes | **Bad.** The autonomy policy gates Linear writes. As ledger, every agent write is a Linear write, so the gate must be waived for ~50 writes/h, which empties it. Scratch stores for E1–E12 would need a second Linear workspace. | Small. One approval of the project and field set; then writes inside that project only. | Spec, "Autonomy policy" and W25 "Gate". |

**Recommendation: Linear stays a view.** It wins only on "Tal can see it on mobile", and W25
already gives that. W24 (`iar/ledger.py`) keeps a later switch to one file if this changes.

Open point for W25 (not a W23 decision): pick the auth path for the reconciler before W25 starts.
Options: a personal API key in the macOS Keychain (simplest; acts as Tal), or an OAuth app with
client credentials and `actor=app` (30-day token, shows as an app; may need a workspace admin to
create). Recommended: personal API key, because W25 writes are Tal's own mirror.

## 3. W-items this report changes

- **W10:** change the `live`/`zombie` predicates as in 1.4, and add the `blocked`-without-pid
  fixture. Without this, W12 releases claims of sessions that are waiting for Tal.
- **W12:** the zombie flag for a `failed` session gives `claude --resume <sessionId>`.
- **W25:** add "choose auth path" (personal API key recommended) to its first step.
- **To W23a (precog-b7tf):** Agent Mail installs `br`, not `bd`. Gas City needs `bd` ≥ 1.3.0 and
  Dolt ≥ 2.6.0 (or its file store).

No migration bead: no switch is recommended.

## Sources

- Gas Town: `gt --help`, `gt witness|feed|escalate|release|orphans --help` (gt 1.1.0, local);
  https://github.com/gastownhall/gastown (releases).
- Gas City: https://github.com/gastownhall/gascity (README, releases).
- MCP Agent Mail: https://github.com/Dicklesworthstone/mcp_agent_mail_rust (README, releases);
  https://github.com/Dicklesworthstone/mcp_agent_mail (deprecation notice).
- Claude Code agent view: https://code.claude.com/docs/en/agent-view
- Claude Code scheduled tasks: https://code.claude.com/docs/en/routines,
  https://code.claude.com/docs/en/desktop-scheduled-tasks
- GitHub Agentic Workflows: https://github.github.com/gh-aw/, https://github.com/github/gh-aw
- OpenAI Symphony: https://github.com/openai/symphony
- Linear rate limits: https://linear.app/developers/rate-limiting
- Linear OAuth: https://linear.app/developers/oauth-2-0-authentication
- Linear workspace facts: Linear MCP `whoami`, `list_teams` (read-only), 2026-10-30.
- Repo dates: `gh api repos/<repo>` and `gh api repos/<repo>/releases`, 2026-10-30.
- First survey: factory-research.html (2026-10-30), epic precog-am8w.

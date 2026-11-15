# Symphony design

Interfaces for the Python implementation described in `PLAN.md`. Section numbers
(§) refer to `SPEC.md`. Later stages implement against this file. If an
interface has to change, say so in the stage's return value and update this
file in the same change.

Targets Codex CLI 0.169 (`codex app-server generate-json-schema`, v2 protocol).

## Module layout

```
agents/symphony/
  pyproject.toml          uv project; deps pyyaml, python-liquid, mcp; extra "test" = pytest
  docs/{PLAN,SPEC,DESIGN}.md
  WORKFLOW.md             (Extensions stage) Tal's workflow file
  local.symphony.plist    (Extensions stage) launchd KeepAlive job; never loaded by an agent
  symphony/
    __init__.py           version only
    errors.py             SymphonyError and typed subclasses (categories)
    models.py             Issue, BlockerRef, Workspace, ThreadRecord, RetryEntry, RunningEntry,
                          AgentEvent, WorkerOutcome, OrchestratorState
    workflow.py           §5: path resolution, loader, WorkflowStore (reload), prompt rendering
    config.py             §5.3/§6: typed ServiceConfig, defaults, $VAR/~ resolution, preflight
    tracker.py            §11: Tracker protocol, make_tracker()
    linear.py             §11 Linear adapter over MCP tools: normalization, dispatchable/wake
                          rule (label `waiting` or In Review), signatures, orchestrator writes,
                          bridge_event (run-status and event comments)
    mcp_linear.py         Linear MCP transport: OAuth login (DCR, PKCE, localhost redirect),
                          Keychain token storage, synchronous McpSession over the async SDK
    workspace.py          §9: workspace keys, WorkspaceManager, hooks, thread record store
    appserver.py          §10: transports (shared daemon, stdio), AppServerClient, event mapping
    runner.py             §10.7/§16.5: CodexRunner.run_attempt (turn loop, thread affinity,
                          escalation, budget)
    orchestrator.py       §7/§8/§14/§16: Orchestrator (single authority), worker threads, run loop
    notify.py             macOS notifications
    rex.py                Rex tab per ticket (ported from run-ticket)
    log.py                structured key=value logging to stderr + JSONL file
    cli.py                `symphony [WORKFLOW.md]`, `ls`, `open`, `logs`, `check`, `login`,
                          `bridge-event`
  tests/
    conftest.py           fixtures: tmp workflow files, fake clock, fakes below
    fakes.py              FakeTracker, FakeMcp, FakeLinearBoard, FakeKeychain, FakeAppServer
                          (transport), FakeWorkerFactory, FakeNotifier, FakeClock
    test_workflow.py  test_config.py  test_linear.py  test_mcp_linear.py  test_workspace.py
    test_appserver.py
    test_runner.py  test_orchestrator.py  test_extensions.py  test_observability.py  test_cli.py
```

Concurrency: one orchestrator thread owns all scheduling state. Each worker is a
`threading.Thread` running `CodexRunner.run_attempt`. Workers never touch
orchestrator state; they put messages on the orchestrator's `queue.Queue`
(`("codex", issue_id, AgentEvent)` and `("exit", issue_id, WorkerOutcome)`).
The run loop drains the queue, fires due retry timers, reloads the workflow if
it changed, and runs `tick()` every `polling.interval_ms`. The only asyncio is inside
`mcp_linear.McpSession`, which runs the `mcp` SDK on one background event loop thread and
exposes a thread-safe synchronous `call(tool, args)`.

Tests drive `Orchestrator` methods directly with a `FakeClock` and a
`FakeWorkerFactory`; they never start the run loop with real time, except one
CLI lifecycle test with a fake tracker and a short stop.

## Errors (`errors.py`)

```python
class SymphonyError(Exception):
    category: str            # stable portable category
    def __init__(self, category: str, message: str): ...

class WorkflowError(SymphonyError): ...
    # missing_workflow_file, workflow_parse_error, workflow_front_matter_not_a_map,
    # template_parse_error, template_render_error
class ConfigError(SymphonyError): ...
    # invalid_config, unsupported_tracker_kind, invalid_tracker_config, missing_tracker_secret,
    # missing_codex_command
class TrackerError(SymphonyError): ...
    # tracker_request, tracker_status, tracker_response, tracker_pagination,
    # tracker_rate_limited, tracker_auth (no or expired Linear MCP login),
    # tracker_tool_error (an MCP tool returned isError)
class WorkspaceError(SymphonyError): ...
    # workspace_path_outside_root, workspace_not_directory, workspace_create_failed,
    # invalid_workspace_cwd
class HookError(SymphonyError): ...
    # hook_failed, hook_timeout   (attributes: hook: str, exit_code: int | None)
class AgentError(SymphonyError): ...
    # §10.6: codex_not_found, invalid_workspace_cwd, response_timeout, turn_timeout,
    # port_exit, response_error, turn_failed, turn_cancelled, turn_input_required,
    # startup_failed, thread_resume_failed
```

`str(err)` is `"<category>: <message>"`. Messages never contain secrets.

## Models (`models.py`)

All dataclasses; timestamps are timezone-aware `datetime` (UTC) unless named `*_ms`
(monotonic milliseconds).

```python
@dataclass(frozen=True)
class BlockerRef:
    id: str | None
    identifier: str | None
    state: str | None

@dataclass(frozen=True)
class Issue:                                    # §4.1.1, every field always present
    id: str
    identifier: str
    title: str
    state: str
    dispatchable: bool
    description: str | None = None
    priority: int | None = None
    branch_name: str | None = None
    url: str | None = None
    assignee_id: str | None = None
    labels: tuple[str, ...] = ()                # trimmed, lowercased, deduped
    blocked_by: tuple[BlockerRef, ...] = ()
    native_ref: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_template(self) -> dict: ...          # str keys; labels/blocked_by as lists of str/dict

@dataclass(frozen=True)
class Workspace:                                # §4.1.4
    path: Path
    workspace_key: str
    created_now: bool

@dataclass
class ThreadRecord:                             # persisted per ticket, see "Thread affinity"
    thread_id: str
    created_at: datetime
    last_turn_id: str | None = None
    last_turn_started_at: datetime | None = None   # wall clock, taken just BEFORE turn/start
    total_tokens: int = 0                           # latest absolute thread total

@dataclass
class AgentEvent:                               # §10.4
    event: str                                  # see event mapping table
    timestamp: datetime
    thread_id: str | None = None
    turn_id: str | None = None
    codex_app_server_pid: str | None = None
    usage: dict | None = None                   # absolute thread totals: input/output/total
    rate_limits: dict | None = None
    message: str | None = None                  # short summary, never a large raw payload
    method: str | None = None                   # raw protocol method, for logs

@dataclass
class WorkerOutcome:
    normal: bool                                # True = exit_normal (continuation retry)
    reason: str                                 # "completed", "max_turns", "not_routable",
                                                # "escalated", "budget", or an error category
    error: str | None = None
    final_issue: Issue | None = None            # last refreshed snapshot, for notifications

@dataclass
class RetryEntry:                               # §4.1.7
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: int
    error: str | None = None

@dataclass
class RunningEntry:                             # §16.4 fields
    issue: Issue
    identifier: str
    worker: "WorkerHandle"
    started_at: datetime
    started_ms: int
    retry_attempt: int | None
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    codex_app_server_pid: str | None = None
    last_codex_event: str | None = None
    last_codex_timestamp_ms: int | None = None
    last_codex_message: str | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0

@dataclass
class OrchestratorState:                        # §4.1.8
    poll_interval_ms: int
    max_concurrent_agents: int
    running: dict[str, RunningEntry]
    claimed: set[str]
    retry_attempts: dict[str, RetryEntry]
    completed: set[str]
    codex_totals: dict                          # input_tokens, output_tokens, total_tokens,
                                                # seconds_running (ended sessions only)
    codex_rate_limits: dict | None
    failure_notified: set[str]                  # issue ids already notified for repeated failure
```

## Workflow and config (`workflow.py`, `config.py`) — §5, §6, §12

```python
# workflow.py
@dataclass(frozen=True)
class WorkflowDefinition:
    config: dict
    prompt_template: str                        # trimmed body
    path: Path                                  # absolute

def resolve_workflow_path(explicit: str | Path | None, cwd: Path) -> Path
    # explicit wins; else cwd / "WORKFLOW.md". Does not check existence.
def load_workflow(path: Path) -> WorkflowDefinition
    # raises WorkflowError(missing_workflow_file | workflow_parse_error |
    #                      workflow_front_matter_not_a_map)
    # Front matter only if the first line is exactly "---"; ends at the next "---" line.

DEFAULT_PROMPT = "You are working on an issue from the configured tracker."
DEFAULT_CONTINUATION = (...)  # tells the agent: same ticket, re-read new Linear comments,
                              # continue, park per WORKFLOW rules; uses {{ issue.identifier }}

def render_prompt(template: str, issue: Issue, attempt: int | None, **extra) -> str
    # python-liquid Environment(undefined=StrictUndefined); unknown filter or variable raises
    # WorkflowError(template_parse_error | template_render_error). Empty template ->
    # DEFAULT_PROMPT. Variables: issue (Issue.to_template()), attempt, plus extra
    # (runner passes turn_number, max_turns).

class WorkflowStore:
    """Holds the last known good (definition, config). Reload by mtime+size+hash check."""
    def __init__(self, path: Path, env: Mapping[str, str] = os.environ): ...
    def load_initial(self) -> tuple[WorkflowDefinition, "ServiceConfig"]   # raises on error
    def reload_if_changed(self) -> bool
        # True if a new valid config was applied. On invalid file: keep last good, set
        # last_error, log "workflow_reload failed", return False. Never raises.
    @property
    def current(self) -> tuple[WorkflowDefinition, "ServiceConfig"]
    last_error: SymphonyError | None
```

The run loop calls `reload_if_changed()` once per loop iteration (about 1 s) and
again before each dispatch. Polling the file stat is the watch mechanism; no
watchdog dependency.

```python
# config.py — every section is a frozen dataclass; defaults per §6.4 unless noted
@dataclass(frozen=True)
class TrackerConfig:
    kind: str | None
    provider: dict                         # raw adapter-owned map, unknown keys preserved
    required_labels: tuple[str, ...]       # normalized; blank entries kept as "" (match nothing)
    active_states: tuple[str, ...]
    terminal_states: tuple[str, ...]

@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None
    before_run: str | None
    after_run: str | None
    before_remove: str | None
    timeout_ms: int = 60000                # <=0 or non-int -> ConfigError

@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = 3         # ours; spec default 10 (PLAN "Accepted gaps")
    max_turns: int = 20                    # must be >0
    max_retry_backoff_ms: int = 300000
    max_concurrent_agents_by_state: Mapping[str, int] = {}   # keys trim+lower, bad values dropped
    continuation_prompt: str | None = None # extension: Liquid template for resumed/continued turns

@dataclass(frozen=True)
class CodexConfig:
    command: str = "codex app-server"      # kept verbatim, never path-expanded
    transport: str = "daemon"              # extension: "daemon" | "stdio" (deviation 1)
    socket: Path = ~/.codex/app-server-control/app-server-control.sock   # extension
    approval_policy: Any = "on-request"    # pass-through Codex AskForApproval
    approvals_reviewer: str | None = "auto_review"   # extension, pass-through
    thread_sandbox: Any = "workspace-write"           # pass-through Codex SandboxMode
    turn_sandbox_policy: Any = None                   # pass-through Codex SandboxPolicy; None = omit
    turn_timeout_ms: int = 3600000
    read_timeout_ms: int = 5000
    stall_timeout_ms: int = 300000         # <=0 disables

@dataclass(frozen=True)
class ExtensionsConfig:                    # our top-level keys, all dynamic (apply on reload)
    repos: Mapping[str, Path]              # `repos:` {name: path}; label repo:<name> -> path
    token_cap: int = 3_000_000             # `budget.token_cap`; label budget:<n>k overrides
    notify_states: tuple[str, ...] = ("In Review", "Done")   # `notify.states`
    notify_failure_attempts: int = 3       # `notify.failure_attempts`
    notify_enabled: bool = True            # `notify.enabled`
    rex_enabled: bool = True               # `rex.enabled`
    rex_session: str = "symphony"          # `rex.session`

@dataclass(frozen=True)
class ServiceConfig:
    workflow_path: Path
    poll_interval_ms: int                  # polling.interval_ms, default 30000 (spec);
                                           # WORKFLOW.md sets 60000 for the MCP server
    workspace_root: Path                   # absolute; default tempfile.gettempdir()/symphony_workspaces
    tracker: TrackerConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    ext: ExtensionsConfig

def build_config(defn: WorkflowDefinition, env: Mapping[str, str]) -> ServiceConfig
    # defaults -> $VAR -> coerce. $VAR is resolved only for: workspace.root, codex.socket,
    # repos.* (paths: also ~), and adapter-documented secret keys (resolved by the adapter,
    # not here). Raises ConfigError(invalid_config) for bad types/values.
def resolve_path(value: str, env: Mapping[str, str], base_dir: Path) -> Path
    # "$VAR" or "${VAR}" whole-value or prefix expansion, then ~, then relative-to-base_dir.
def validate_dispatch_config(cfg: ServiceConfig, env: Mapping[str, str]) -> None
    # §6.3: tracker.kind present and supported (SUPPORTED_TRACKERS = {"linear"}), adapter accepts
    # tracker.provider (linear.validate_provider_config), codex.command non-empty when
    # transport == "stdio", codex.socket set when transport == "daemon". Raises ConfigError.
def state_key(name: str) -> str            # trim + lower; used for every state comparison
```

`tracker.active_states`/`terminal_states` defaults come from the adapter profile
(Linear: below). Front matter for Tal is written in the Extensions stage.

## Tracker contract (`tracker.py`) — §11

```python
class Tracker(Protocol):
    # §11.1 required reads
    def fetch_issues_by_states(self, state_names: Sequence[str]) -> list[Issue]
    def fetch_issues_by_ids(self, issue_ids: Sequence[str]) -> list[Issue]
    # Our generic write hooks (deviation 3). The orchestrator/runner call these without
    # knowing provider semantics; the adapter decides what they mean.
    def prepare_dispatch(self, issue: Issue) -> Issue
        # Called by the runner right before each turn. Linear: if the ticket is parked, remove
        # the `waiting` label and/or move In Review to In Progress, and return the refreshed
        # snapshot; else return issue.
    def escalate(self, issue: Issue, text: str) -> None
        # Post a signed orchestrator comment with text, move to the human-review state
        # (In Review).
    def post_note(self, issue: Issue, text: str) -> None
        # Post a signed orchestrator comment (e.g. "started a new Codex thread").
    def secret_environment_names(self) -> list[str]
        # Names stripped from hook and stdio app-server child environments. Linear: none (the
        # OAuth token is only in the Keychain).

def make_tracker(cfg: TrackerConfig, env: Mapping[str, str],
                 turn_started_at: Callable[[str], datetime | None]) -> Tracker
    # turn_started_at(identifier) -> last_turn_started_at from the ThreadRecord store; the Linear
    # adapter uses it for the wake rule. Raises ConfigError(unsupported_tracker_kind) etc.
```

All tracker failures raise `TrackerError`; the orchestrator only distinguishes
success from failure (§11.4).

## Linear adapter (`linear.py`)

Linear is reached through its MCP server (`https://mcp.linear.app/mcp`), not GraphQL: Tal
can't create a personal API key. Tool names and argument shapes follow the server's
tools/list input schemas. Result shapes are not published; the parsers accept the plausible
variants (`{issues: [...]}` / `{nodes}` / a bare list, `status` as a name or `{name}`, labels
as names or `{name}`), and phase 0 confirms them.

```python
AGENT_SIGNATURE = "🤖 agent"            # "🤖 agent (Codex, TAL-N)"
BRIDGE_SIGNATURE = "🤖 experiment-run"
RUN_STATUS_SIGNATURE = "🤖 experiment-run status"   # bridge's edited-in-place status comment
SYMPHONY_SIGNATURE = "🤖 symphony"

class Tools(Protocol):
    def call(self, name: str, args: dict) -> Any   # decoded tool payload; raises TrackerError

@dataclass(frozen=True)
class LinearComment:
    created_at: datetime
    body: str
    user_id: str | None
    id: str | None

def classify_comment(body: str) -> str     # "agent" | "status" | "bridge" | "symphony" | "human"
def is_wake_comment(c: LinearComment) -> bool   # bridge event or human
def is_parked(*, state, labels, wake_states, waiting_label="waiting") -> bool
    # `waiting` label, or state in wake_states (In Review)

def compute_dispatchable(*, state, assignee_id, viewer_id, blockers, comments,
                         last_turn_started_at, terminal_states, wake_states,
                         labels=(), waiting_label="waiting") -> bool
    # False if assignee_id != viewer_id; False if any blocker state is None or not terminal;
    # if parked: True only if some wake comment is newer than last_turn_started_at
    # (None -> any wake comment counts). Else True.

def parse_issue(item) -> RawIssue          # tracker_response if id/identifier/title/state missing
def build_issue(raw, *, team_key, dispatchable, blockers=()) -> Issue
def validate_provider_config(provider, env=None) -> None   # ConfigError(invalid_tracker_config)
def list_comments(tools, issue_ref, *, limit=50, max_pages=20) -> list[LinearComment]
def save_comment(tools, body, *, issue_ref=None, comment_id=None) -> str | None
def bridge_event(tools, issue_ref, event: dict) -> dict    # see "Bridge writes"

class LinearTracker:                       # implements Tracker
    def __init__(self, provider, *, active_states, terminal_states, tools: Tools,
                 turn_started_at, required_labels=()): ...
    def viewer_id(self) -> str             # cached get_user(query="me").id
    def fetch_issues_by_states(self, state_names) -> list[Issue]
    def fetch_issues_by_ids(self, issue_ids) -> list[Issue]
    def prepare_dispatch(self, issue) -> Issue
    def escalate(self, issue, text) -> None
    def post_note(self, issue, text) -> None
    def secret_environment_names(self) -> list[str]   # []
```

Tool calls:

| Operation | Tool and arguments |
|---|---|
| viewer | `get_user {query: "me"}` (once) |
| candidates | `list_issues {team, state, label: <first required label>, limit: 50, fields, cursor?}` once per active state; `fields` = id, uuid, title, description, priority, url, gitBranchName, createdAt, updatedAt, status, labels, assigneeId, team |
| blockers | for tickets assigned to the viewer: `get_issue {id, includeRelations: true, fields: [relations]}`; `relations.blockedBy` has id and title only, so each blocker's state is `get_issue {id, fields: [status]}`, cached per poll; an unreadable blocker blocks |
| wake comments | only for parked tickets: `list_comments {issueId, limit: comment_window, orderBy: createdAt, cursor?}`, all pages (max 20); the max `createdAt` of wake comments is used, so order doesn't matter |
| ID refresh | `get_issue {id, includeRelations: true, fields: [...all, relations]}` per id; "not found" tool errors and identifiers outside `team_key-` are omitted |
| un-park | `save_issue {id, removeLabels: [waiting]}` and/or `{state: In Progress}` (one call) |
| escalate | `save_comment {issueId, body}` then `save_issue {id, state: In Review}` |
| note | `save_comment {issueId, body}` |

### Linear MCP transport (`mcp_linear.py`)

```python
MCP_URL = "https://mcp.linear.app/mcp"
KEYCHAIN_SERVICE = "symphony-linear-mcp"; KEYCHAIN_ACCOUNT = "linear-mcp"
REDIRECT_PORT = 47615                    # http://127.0.0.1:47615/callback

class Keychain:                          # one generic-password item holding a JSON record
    def read(self) -> dict | None        # security find-generic-password -s -a -w
    def write(self, record) -> None      # `security -i` on stdin with -X <hex>; verified by read
    def delete(self) -> None
class KeychainTokenStorage:              # mcp TokenStorage; record = {client_info, metadata,
                                         # access_token, expires_at, refresh_token, scope}
class StoredOAuthProvider(OAuthClientProvider)
    # also restores the access-token expiry and the AS metadata from storage, so an expired
    # token is refreshed at the stored token endpoint instead of starting a browser flow
def service_auth(storage, url) -> StoredOAuthProvider   # redirect/callback raise LoginRequired
class McpSession:                        # mcp Client over streamable HTTP on a background loop
    def call(self, name, args) -> Any    # thread-safe; reconnects after a failure
    def list_tools(self) -> list[str]
def service_session(*, url, keychain_service) -> McpSession   # tracker_auth before any network
                                                               # call when no login is stored
def login(*, url, keychain, port, open_browser, ...) -> list[str]
    # empty record; DCR (public client, token_endpoint_auth_method none); PKCE; browser;
    # localhost callback; tokens and AS metadata to the Keychain; returns tool names
def map_error(exc) -> TrackerError       # LoginRequired/OAuth/401 -> tracker_auth; 429 ->
                                         # tracker_rate_limited; HTTP >= 400 -> tracker_status;
                                         # MCP error -> tracker_response; else tracker_request
def tool_payload(name, result) -> Any    # isError -> tracker_tool_error (rate-limit text ->
                                         # tracker_rate_limited); structured content, else JSON
                                         # text, else text
```

An access token is stored only with a known expiry. A token rotated by another process
(the service and `bridge-event` share the record) is picked up because every storage read
goes to the Keychain and a failed refresh re-reads it.

### Bridge writes (`bridge_event`)

`symphony bridge-event --issue TAL-N --json -` reads one JSON object:
`{"run": REG, "status": MARKDOWN}` and/or `{"event_id": ID, "event": MARKDOWN}`. It lists the
issue's comments once, then:

- status: the comment containing `` Run status: `REG` `` with first line
  `🤖 experiment-run status` is edited in place (`save_comment {id, body}`) if its body
  changed, else created. That signature never wakes a ticket.
- event: if a `🤖 experiment-run` comment containing `Event: ID` exists, nothing is posted;
  else the body is posted, with the signature and the `Event: ID` line added if missing.

It never changes state or labels. Exit 0 written or already there, 2 bad input, 3
`tracker_auth`, 1 other failure; stdout is a JSON result or `{"error": category}`. Any retry
is safe because both writes are found by marker first.

### Linear adapter profile (§11.2)

| Item | Value |
|---|---|
| `tracker.kind` | `linear` |
| `tracker.provider` keys | `team_key` (required, e.g. `TAL`); `endpoint` (default `https://mcp.linear.app/mcp`); `keychain_service` (default `symphony-linear-mcp`); `assignee` (default `me` = the logged-in user; else a Linear user id); `waiting_label` (default `waiting`); `wake_states` (default `[In Review]`); `in_progress_state` (default `In Progress`); `human_review_state` (default `In Review`); `comment_window` (default 50, `list_comments` page size) |
| Secret env names | none |
| Default states | active `[Todo, In Progress, In Review]`; terminal `[Done, Canceled, Duplicate]` |
| Validation errors | missing/blank `team_key`, non-list states, bad optional keys -> `invalid_tracker_config`; missing login -> `tracker_auth` at the first call |
| Scope | `list_issues` with `team` = `team_key`, `label` = the first required label, one call per state, page size 50 with cursor |
| `id` / `native_ref` | `id` = Linear issue UUID; `identifier` = `TAL-N`; `native_ref` = `{linear_issue_id, team_key}` |
| Normalization | labels lowercased; priority 0 -> null; RFC 3339 timestamps parsed or null; `branch_name` from `gitBranchName`; `assignee_id` from `assigneeId` |
| `dispatchable` | false if not assigned to the viewer; false if any `blockedBy` issue is not in a terminal state (unknown state counts as blocking); when parked (`waiting` label or In Review), true only with a wake comment newer than the last turn start (see below) |
| Wake comment | first line not starting with `🤖 agent`, `🤖 symphony` or `🤖 experiment-run status`. Bridge events (`🤖 experiment-run`) and unsigned (Tal) comments wake |
| Malformed | state-list: log `linear_malformed_issue` and omit; id-refresh: raise `tracker_response` |
| Writes (deviation 3) | `prepare_dispatch`: remove `waiting`, In Review -> `in_progress_state`; `escalate`: signed comment + `human_review_state`; `post_note`: signed comment; `bridge_event` for the bridge |
| Agent tools | none advertised; the agent uses the Linear MCP over its own OAuth (PLAN "Not used") |
| Errors | `TrackerError(category)`; categories `tracker_auth`, `tracker_request`, `tracker_status`, `tracker_response`, `tracker_tool_error`, `tracker_pagination`, `tracker_rate_limited`, plus `ConfigError` `invalid_tracker_config` / `unsupported_tracker_kind`. Messages never contain tokens |

Wake rule details: `last_turn_started_at` is the wall-clock time Symphony took just
before sending `turn/start` for the ticket's latest turn (from the ThreadRecord).
Linear `createdAt` is server time; clock skew of a few seconds only risks one
extra resume. The adapter reads all comment pages of a parked ticket and takes the max, so
the order `list_comments` returns doesn't matter.

## Workspaces and hooks (`workspace.py`) — §9

```python
def workspace_key(identifier: str) -> str
    # replace [^A-Za-z0-9._-] with "_"; if changed, append "-" + first 16 hex chars of
    # sha256(identifier) (64 bits). "." and ".." and leading "." are treated as changed.

class WorkspaceManager:
    def __init__(self, root: Path, hooks: HooksConfig, *, repos: Mapping[str, Path],
                 strip_env: Sequence[str], run=subprocess.run): ...
    def path_for(self, identifier: str) -> Path        # root/key, containment checked
    def ensure(self, issue: Issue) -> Workspace
        # mkdir (parents); created_now only if created by this call. Existing non-directory ->
        # WorkspaceError(workspace_not_directory) (no replace). If created_now, run after_create;
        # on failure remove the new directory (shutil.rmtree) and raise HookError.
    def run_hook(self, name: str, issue: Issue, workspace: Path) -> None
        # `bash -lc <script>`, cwd = workspace, timeout hooks.timeout_ms, env = hook_env().
        # Nonzero -> HookError(hook_failed); timeout -> HookError(hook_timeout). Output truncated
        # to 2 KB in logs. Missing script -> no-op.
    def run_hook_best_effort(self, name, issue, workspace) -> bool   # logs and swallows
    def remove(self, issue_or_identifier) -> bool
        # Deviation 2: run before_remove; on failure/timeout DO NOT delete, log
        # workspace_remove_skipped, return False (caller notifies). Else rmtree if still
        # present, delete the thread record dir, return True.
    def hook_env(self, issue: Issue, workspace: Path) -> dict[str, str]
        # os.environ minus strip_env, plus SYMPHONY_WORKSPACE, SYMPHONY_ISSUE_IDENTIFIER,
        # SYMPHONY_ISSUE_ID, SYMPHONY_ISSUE_LABELS (comma-joined), SYMPHONY_REPO (path from
        # repo:<name> label via repos, else "")
    def assert_agent_cwd(self, workspace: Path, cwd: Path) -> None
        # §9.5 invariants 1-2: both resolved; cwd == workspace and workspace under root, else
        # WorkspaceError(invalid_workspace_cwd / workspace_path_outside_root)

class ThreadStore:
    """Per-ticket ThreadRecord at <root>/.symphony/<workspace_key>/thread.json (0600, atomic)."""
    def __init__(self, root: Path): ...
    def dir_for(self, identifier: str) -> Path                # <root>/.symphony/<key>
    def read(self, identifier: str) -> ThreadRecord | None    # corrupt file -> None + log
    def write(self, identifier: str, rec: ThreadRecord) -> None
    def delete(self, identifier: str) -> None
    def turn_started_at(self, identifier: str) -> datetime | None   # passed to make_tracker
```

Deviation from PLAN: the thread record lives under `<workspace.root>/.symphony/`,
not inside the workspace. An untracked `.symphony/` inside a git worktree would make
`git worktree remove` (no `--force`) fail and `git worktree add` into the new
directory refuse a non-empty path. Workspace keys never start with `.`, so the
directory cannot collide with a workspace.

## Codex runner (`appserver.py`, `runner.py`) — §10

### Transports

```python
class Transport(Protocol):
    pid: str | None                        # stdio child pid; None for the daemon
    def send(self, msg: dict) -> None
    def recv(self, timeout_s: float) -> dict | None   # None on timeout; raises
                                                       # AgentError(port_exit) when closed
    def close(self) -> None

class DaemonTransport:                     # adapted from run-ticket/codex_rpc.py
    def __init__(self, socket_path: Path, connect_timeout_s: float): ...
    # WebSocket over the Unix socket; masked client frames; answers pings; fragmented frames.
class StdioTransport:                      # spec §10.1 default, config fallback
    def __init__(self, command: str, cwd: Path, env: Mapping[str, str]): ...
    # Popen(["bash", "-lc", command], cwd=cwd, stdin/stdout pipes, stderr to a separate reader
    # thread that logs lines as codex_stderr), JSON-lines framing, 10 MB max line.
    # FileNotFoundError / exit 127 at start -> AgentError(codex_not_found).

def open_transport(codex: CodexConfig, cwd: Path, env: Mapping[str, str]) -> Transport
```

### Client

```python
ServerRequestHandler = Callable[[str, dict], dict]   # (method, params) -> JSON-RPC result

class AppServerClient:
    def __init__(self, transport: Transport, *, read_timeout_s: float,
                 on_event: Callable[[AgentEvent], None], clock=time.monotonic): ...
    def request(self, method: str, params: dict) -> dict
        # sends with a fresh id; while waiting, routes notifications and server requests;
        # no reply in read_timeout_s -> AgentError(response_timeout); JSON-RPC error ->
        # AgentError(response_error)
    def initialize(self, client_name: str = "symphony") -> None
        # request("initialize", {"clientInfo": {"name", "version"}}), notify("initialized")
    def start_thread(self, *, cwd: Path, policy: CodexConfig) -> str
        # thread/start {cwd, approvalPolicy, approvalsReviewer, sandbox} -> result.thread.id
    def resume_thread(self, thread_id: str, *, cwd: Path, policy: CodexConfig) -> str
        # thread/resume {threadId, cwd, approvalPolicy, approvalsReviewer, sandbox,
        # excludeTurns: true}; error -> AgentError(thread_resume_failed). If thread.status
        # is active: thread/turns/list limit 1, turn/interrupt an inProgress turn and wait
        # read_timeout for its turn/completed, else AgentError(turn_in_progress)
    def set_title(self, thread_id: str, title: str) -> None    # thread/name/set; best effort
    def run_turn(self, thread_id: str, prompt: str, *, cwd: Path, policy: CodexConfig,
                 turn_timeout_s: float, cancel: threading.Event) -> "TurnResult"
        # turn/start {threadId, input: [{"type": "text", "text": prompt}], cwd,
        # sandboxPolicy (if set)} -> result.turn.id; then reads until turn/completed for this
        # turn. Silence > turn_timeout_s -> interrupt, TurnResult("timeout"). cancel set ->
        # turn/interrupt, TurnResult("cancelled").
    def interrupt(self, thread_id: str, turn_id: str) -> None   # turn/interrupt, best effort
    def close(self) -> None

@dataclass
class Escalation:
    kind: str          # command_approval | file_approval | permissions | user_input |
                       # elicitation
    summary: str       # command / reason / questions, <= 1 KB, for the Linear question

@dataclass
class TurnResult:
    status: str        # completed | failed | interrupted | timeout | cancelled | escalated
    turn_id: str | None
    error: str | None = None
    escalation: Escalation | None = None
```

Messages for other threads (the daemon multiplexes) are ignored by `threadId`.

### Event mapping (Codex 0.169 v2 protocol)

| Codex message | Symphony handling | `AgentEvent.event` |
|---|---|---|
| `turn/start` response, first turn of the worker | record turn id, `session_id = <thread>-<turn>` | `session_started` |
| `turn/started` (notification) | refresh activity | `notification` |
| `turn/completed`, `turn.status == "completed"` | end turn, success | `turn_completed` |
| `turn/completed`, `status == "failed"` | end turn, failure (`turn.error.message`) | `turn_failed` |
| `turn/completed`, `status == "interrupted"` | end turn, failure unless we escalated | `turn_cancelled` |
| `error` with `willRetry == false` | remember; turn end decides outcome | `turn_ended_with_error` |
| `thread/tokenUsage/updated` | `usage = tokenUsage.total` (absolute thread totals: `inputTokens`, `outputTokens`, `totalTokens`); ignore `last` | `notification` |
| `account/rateLimits/updated` | `rate_limits = params.rateLimits` | `notification` |
| `thread/status/changed` with `activeFlags` | logged only; requests below drive behavior | `notification` |
| `item/commandExecution/requestApproval`, `execCommandApproval` (legacy) | reply `{"decision": "cancel"}` / `{"decision": "abort"}`; Escalation(command_approval) | `approval_escalated` |
| `item/fileChange/requestApproval`, `applyPatchApproval` (legacy) | reply `{"decision": "cancel"}` / `{"decision": "abort"}`; Escalation(file_approval) | `approval_escalated` |
| `item/permissions/requestApproval` | reply `{"permissions": {}, "scope": "turn"}` (grant nothing), `turn/interrupt`; Escalation(permissions) | `approval_escalated` |
| `item/tool/requestUserInput` | reply `{"answers": {}}`, `turn/interrupt`; Escalation(user_input) | `turn_input_required` |
| `mcpServer/elicitation/request` | reply `{"action": "cancel"}`, `turn/interrupt`; Escalation(elicitation) | `turn_input_required` |
| `item/tool/call` (dynamic tool) | reply `{"success": false, "contentItems": [{"type": "inputText", "text": "unsupported tool"}]}`; turn continues | `unsupported_tool_call` |
| other server requests (`account/chatgptAuthTokens/refresh`, `attestation/generate`, unknown) | JSON-RPC error -32601 | `other_message` |
| `serverRequest/resolved`, `item/*`, deltas, other notifications | refresh activity only | `notification` |
| undecodable frame / non-object JSON | skip | `malformed` |

Every message for our thread resets the turn silence timer and becomes an
`AgentEvent` posted to the orchestrator (deltas are coalesced: at most one
`notification` event per second, so the queue stays small).

### Approval, sandbox and user-input posture (§10.5, §15.1)

- Trust boundary: Tal's own laptop, Tal's tickets only (team `TAL`, label `agent`,
  assigned to Tal). Tracker text is still treated as untrusted input to the agent.
- Thread settings sent on `thread/start`/`thread/resume`: `approvalPolicy:
  "on-request"`, `sandbox: "workspace-write"`, `approvalsReviewer: "auto_review"`
  (all overridable in `WORKFLOW.md`, passed through unvalidated). Codex's
  auto-reviewer decides routine sandbox escapes.
- Symphony never approves anything. Any approval or permission request that reaches
  the client, any user-input request and any MCP elicitation is declined/cancelled,
  the turn is interrupted, and the runner calls `tracker.escalate(issue, question)`:
  a signed `🤖 symphony` comment ("Question for Tal: Codex asked for approval to …")
  and a move to In Review. The worker then exits normally; the ticket is no
  longer routable until Tal replies. Nothing waits indefinitely.
- Unsupported dynamic tool calls fail with a structured result and the turn continues.
  No client-side tools are advertised.
- Tracker credentials: the Linear key is read host-side (env or Keychain) and is
  removed from hook and stdio child environments. The shared daemon never receives it.
- The agent's own Linear access is Codex's Linear MCP over OAuth (accepted gap: not
  scoped to one ticket).

Open: on the shared daemon a server request may also reach Tal's Codex app if it has
the thread open. Symphony answers immediately, so the app does not get a chance;
phase 0 should confirm requests are delivered to the client that started the turn.

### Runner (`runner.py`) — §10.7, §16.5, thread affinity, budget

```python
class CodexRunner:
    def __init__(self, *, config: ServiceConfig, workflow: WorkflowDefinition, tracker: Tracker,
                 workspaces: WorkspaceManager, threads: ThreadStore,
                 transport_factory: Callable[[CodexConfig, Path, Mapping[str, str]], Transport]
                     = open_transport,
                 on_event: Callable[[AgentEvent], None],
                 rex: Callable[[Issue, Path, str], None] | None = None,
                 wall: Callable[[], datetime] = utcnow): ...

    def run_attempt(self, issue: Issue, attempt: int | None, *, cancel: threading.Event,
                    stop_after_turn: threading.Event) -> WorkerOutcome

def token_cap(issue: Issue, default: int) -> int     # budget:<n>k label -> n * 1000
```

Algorithm (config and workflow are the snapshot taken at dispatch; reloads apply
to later attempts):

1. `ws = workspaces.ensure(issue)`; `run_hook("before_run")` (failure -> abnormal
   outcome `hook_failed`/`hook_timeout`).
2. Budget check: cap = `budget:<n>k` label × 1000, else `ext.token_cap`. If the
   ThreadRecord's `total_tokens >= cap`: `tracker.escalate(...)` ("token budget
   reached; raise the budget:<n>k label"), outcome normal `budget`.
3. Open transport (`cwd = ws.path`, env minus tracker secrets), `assert_agent_cwd`,
   `initialize`.
4. Thread affinity: if a ThreadRecord exists, `resume_thread`; on
   `thread_resume_failed` start a new thread and `tracker.post_note(...)` saying so.
   Otherwise `start_thread`, `set_title("<identifier>: <title>")`, write the record.
   Call `rex(issue, ws.path, thread_id)` best effort.
5. Turn loop, `turn_number` from 1 to `agent.max_turns`:
   - `issue = tracker.prepare_dispatch(issue)` (moves a woken ticket to In Progress).
   - Prompt: the full rendered `WORKFLOW.md` body only when this is a brand-new thread
     and `turn_number == 1`; otherwise the continuation template
     (`agent.continuation_prompt` or `DEFAULT_CONTINUATION`) rendered with the same
     variables. Render errors -> abnormal `template_render_error`.
   - Write `last_turn_started_at = wall()` to the ThreadRecord, then `run_turn`.
     Write `last_turn_id` and `total_tokens` after.
   - `escalated` -> `tracker.escalate(issue, question)`; outcome normal `escalated`.
   - `failed`/`timeout`/`cancelled`/`interrupted` -> abnormal with that category
     (`cancelled` from a reconciliation stop is reported as `canceled_by_reconciliation`).
   - Refresh `fetch_issues_by_ids([issue.id])`: error -> abnormal; missing, non-active
     or not routable -> normal `not_routable`.
   - Budget re-check -> escalate, normal `budget`.
   - `stop_after_turn` set -> normal `stopped`.
6. Always: close the client, `run_hook_best_effort("after_run")`. Outcome
   `final_issue` = last snapshot.

`max_turns` reached -> normal `max_turns`; the orchestrator's continuation retry
re-dispatches if the ticket is still routable.

## Orchestrator (`orchestrator.py`) — §7, §8, §14, §16

```python
class WorkerHandle(Protocol):
    def cancel(self, reason: str) -> None       # interrupt the turn now, exit
    def stop_after_turn(self) -> None           # finish the current turn, then exit
    def is_alive(self) -> bool

WorkerFactory = Callable[[Issue, int | None, "WorkerContext"], WorkerHandle]

@dataclass
class WorkerContext:          # snapshot handed to a worker at dispatch
    config: ServiceConfig
    workflow: WorkflowDefinition
    tracker: Tracker
    workspaces: WorkspaceManager
    threads: ThreadStore
    post: Callable[[tuple], None]               # orchestrator queue put

def thread_worker_factory(issue, attempt, ctx) -> WorkerHandle   # real: Thread + CodexRunner
def sort_for_dispatch(issues: list[Issue]) -> list[Issue]          # §8.2 order
def retry_delay_ms(attempt: int, *, continuation: bool, max_backoff_ms: int) -> int

class Orchestrator:
    def __init__(self, store: WorkflowStore, *, worker_factory: WorkerFactory,
                 tracker_factory=make_tracker, notifier: "Notifier",
                 clock_ms: Callable[[], int] = monotonic_ms,
                 wall: Callable[[], datetime] = utcnow,
                 status_path: Path | None = None): ...
    state: OrchestratorState
    inbox: queue.Queue

    def startup(self) -> None
        # validate_dispatch_config -> ConfigError propagates (CLI exits nonzero);
        # terminal cleanup: fetch_issues_by_states(terminal) -> workspaces.remove each;
        # fetch failure logs a warning and continues.
    def tick(self) -> None                       # §16.2
    def reconcile(self) -> None                  # §16.3 + stall detection §8.5 A
    def issue_routable(self, issue: Issue) -> bool    # dispatchable + required labels
    def should_dispatch(self, issue: Issue) -> bool   # §8.2
    def dispatch(self, issue: Issue, attempt: int | None) -> None   # §16.4
    def on_codex_update(self, issue_id: str, ev: AgentEvent) -> None   # §13.5 accounting
    def on_worker_exit(self, issue_id: str, outcome: WorkerOutcome) -> None   # §16.6
    def schedule_retry(self, issue_id: str, attempt: int, *, identifier: str,
                       error: str | None, continuation: bool) -> None
        # continuation: 1000 ms; else min(10000 * 2**(attempt-1), max_retry_backoff_ms)
    def on_retry_due(self, issue_id: str) -> None    # §16.6 on_retry_timer
    def fire_due_retries(self) -> None
    def drain_inbox(self, timeout_s: float) -> None
    def snapshot(self) -> dict                   # §13.3; also written to status_path each tick
    def run(self, stop: threading.Event) -> None
        # startup(); loop: reload_if_changed, drain_inbox, fire_due_retries, tick when due;
        # on stop: cancel workers (stop_after_turn), wait briefly, return.
```

Rules beyond the spec algorithms:

- `issue_routable(issue)` = `issue.dispatchable` and every required label present
  (blank configured label matches nothing).
- Reconciliation stop (deviation 5, below): terminal or non-active state ->
  `worker.cancel()` (interrupt now); terminal also removes the workspace. Active but
  not routable while parked with the required labels (the agent just added `waiting` or
  moved it to In Review) -> `worker.stop_after_turn()`, no cleanup. Any other active but
  unroutable ticket (label removed, reassigned, blocked) -> `cancel()` with reason
  `not_routable`, no cleanup. A WORKFLOW.md that fails to load (`store.last_error`)
  blocks dispatch like a validation failure. Stall -> `cancel()` and failure retry.
- `on_worker_exit`: add runtime seconds; `completed.add` on normal exit; normal ->
  continuation retry attempt 1; abnormal -> next attempt with backoff. Notify when
  `final_issue.state` is in `ext.notify_states` and differs from the state at
  dispatch, when attempt reaches `ext.notify_failure_attempts` (once per issue until
  a normal exit), and when `workspaces.remove` returns False.
- Token accounting: per running entry keep absolute thread totals from `usage`;
  add `max(0, new - last_reported)` to `codex_totals` and update last_reported.
  A resumed thread's first usage event is the thread's cumulative total, so the
  first event of a dispatch only sets the baseline from the ThreadRecord's
  `total_tokens` (no double count across dispatches).
- Tracker is rebuilt from the current config when `tracker` config changes.
  Running workers keep their own snapshot.

## Notifier (`notify.py`)

```python
class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None   # never raises

class MacNotifier:            # osascript "display notification", 10 s timeout, errors logged
class NullNotifier:           # when notify.enabled is false; tests use FakeNotifier
```

Title `"<TAL-N>: <reason>"`; message ends with `(symphony open TAL-N)`.

## Rex (`rex.py`)

Ported from run-ticket `ensure_tab`/`cmd_open`, keyed by identifier. Tab state
(session id, window id) lives in the ThreadStore directory as `rex.json`.

```python
def ensure_tab(identifier: str, cwd: Path, thread_id: str, *, session_label: str,
               store_dir: Path, rex=run_rex) -> None   # hidden tab running `codex resume <id>`
def show_tab(identifier: str, *, session_label: str, store_dir: Path, rex=run_rex) -> None
def run_rex(*args: str) -> dict | str                  # subprocess `rex ...`, 20 s timeout
```

## Logging (`log.py`) — §13

```python
def state_dir() -> Path          # $SYMPHONY_STATE_DIR or ~/.local/state/symphony
def setup_logging(jsonl_path: Path | None, *, stream=sys.stderr) -> None
def log(event: str, level: str = "info", **fields) -> None
    # stderr: "ts=... level=info event=dispatch issue_id=... issue_identifier=TAL-1 outcome=..."
    # JSONL: same fields as one object per line. A failing sink logs one warning to the other
    # sink and is disabled; log() never raises. Values are truncated to 500 chars.
```

Issue-related calls always pass `issue_id` and `issue_identifier`; session events
also pass `session_id`. Default paths: `~/.local/state/symphony/symphony.jsonl`
and `status.json` (overridable by `SYMPHONY_STATE_DIR`).

## CLI (`cli.py`) — §17.7

```python
def main(argv: list[str] | None = None) -> int
```

- `symphony [PATH]` runs the service. `PATH` defaults to `./WORKFLOW.md`. Missing
  file -> message on stderr, exit 2. Startup failure (load, validation) -> message,
  exit 1. SIGINT/SIGTERM -> graceful stop, exit 0. Unhandled exception in the loop
  -> exit 1.
- `symphony ls` prints the status file (running, retrying, tokens).
- `symphony open TICKET` shows the Rex tab (`rex.show_tab`).
- `symphony logs TICKET [-n N]` prints JSONL lines whose `issue_identifier` matches.
- `symphony check [PATH]` loads, validates and does one read-only tracker request
  (`get_user me`), exit 0/1. Without a login it prints `tracker_auth: ... run symphony login`.
- `symphony login [--port N]` runs `mcp_linear.login` and prints the server's tool names,
  never a token.
- `symphony bridge-event --issue TAL-N --json -` runs `linear.bridge_event` (see "Bridge
  writes") with `mcp_linear.service_session()`.

Subcommand names are reserved words; any other first argument is a workflow path.

## Deviations (adds to PLAN)

5. **Graceful park.** §8.5 says to terminate a worker whose ticket becomes active
   but unroutable. For a parked ticket (`waiting` label or In Review) we let the current turn finish
   (`stop_after_turn`) so the agent's parking turn isn't interrupted mid-message; any
   other unroutable ticket is interrupted. Terminal and non-active states still
   interrupt immediately; stall detection still applies.
6. **Thread record location.** `<workspace.root>/.symphony/<key>/thread.json`
   instead of inside the workspace (see Workspaces).

## Tests

Command: `cd agents/symphony && uv run --extra test pytest -q`. `pyproject.toml`
sets `pythonpath = [".", "tests"]`, so tests import `symphony` and `fakes` directly.
`tests/conftest.py` sets `SYMPHONY_STATE_DIR` to a tmp dir for every test, and provides
fixtures `write_workflow`, `clock`, `tracker`, `mcp`, `app_server`, `workers`, `notifier`.
No test reaches Linear, the real Keychain or a browser; `test_mcp_linear.py` runs the OAuth
flow against `httpx2.MockTransport` endpoints and `McpSession` against an in-process MCP
server on 127.0.0.1. `tests/test_imports.py` is
the skeleton smoke test.

## Test fakes (`tests/fakes.py`)

`make_issue(identifier="TAL-1", **fields)` builds an `Issue` with label `agent`.

- `FakeClock`: `now_ms()`, `advance(ms)`, `wall()` (UTC datetime moving with it).
- `FakeTracker(Tracker)`: dict of issues by id; `set_state`, `set_issue`; records
  calls (`fetch_issues_by_states`, `fetch_issues_by_ids`, `prepare_dispatch`,
  `escalate`, `post_note`); `fail_next(method, TrackerError)`.
- `FakeMcp` (`linear.Tools`): `on(tool, result | callable | Exception)`, one-shot
  `reply(tool, ...)`; records `calls`; `called(tool)`.
- `FakeLinearBoard`: a small stateful Linear behind a `FakeMcp` (`.mcp`): issues with
  status, labels, assignee and blockers; comments with paging; `save_comment` create and
  update; `save_issue` state and add/remove labels; `get_user`.
- `FakeKeychain`: in-memory `read`/`write`/`delete` record.
- `FakeAppServer(Transport)`: scripted server. `on(method, result | callable |
  Exception)` answers client requests (unregistered methods get no reply, for read
  timeouts); `after(method, msgs)` queues messages after that reply; `notify(method,
  **params)` / `server_request(method, **params) -> id` queue server messages;
  `script_turn(thread_id, turn_id, status=, events=, error=)` answers the next
  `turn/start` then emits `turn/started`, the events, `turn/completed`; `silence()`
  for turn timeouts; `close()` for port exit. Records `sent`, `replies` (server
  request id -> client result) and `requests(method)`.
- `FakeWorkerFactory`: returns `FakeWorkerHandle`s (records cancel/stop) and lists
  dispatches; tests call `orch.on_worker_exit` themselves.
- `FakeNotifier`: list of `(title, message)`.

## Coverage map

### §18.1 checklist

| Item | Module | Test file |
|---|---|---|
| Workflow path selection (explicit + cwd default) | workflow.py, cli.py | test_workflow.py, test_cli.py |
| Loader: front matter + body split | workflow.py | test_workflow.py |
| Typed config, defaults, `$` resolution | config.py | test_config.py |
| Dynamic watch/reload/re-apply | workflow.py (WorkflowStore), orchestrator.py | test_workflow.py, test_orchestrator.py |
| Polling orchestrator, single-authority state | orchestrator.py | test_orchestrator.py |
| Tracker adapter: state-list + ID refresh | linear.py | test_linear.py |
| Sanitized, collision-resistant workspaces | workspace.py | test_workspace.py |
| Hooks after_create/before_run/after_run/before_remove | workspace.py, runner.py | test_workspace.py, test_runner.py |
| `hooks.timeout_ms` default 60000 | config.py, workspace.py | test_config.py, test_workspace.py |
| App-server client with targeted transport/framing | appserver.py | test_appserver.py |
| `codex.command` default `codex app-server` | config.py, appserver.py | test_config.py, test_appserver.py |
| Strict prompt rendering with `issue`, `attempt` | workflow.py | test_workflow.py |
| Exponential retry + continuation retry | orchestrator.py | test_orchestrator.py |
| `agent.max_retry_backoff_ms` cap | config.py, orchestrator.py | test_orchestrator.py |
| Reconciliation stops terminal/non-active runs | orchestrator.py | test_orchestrator.py |
| Terminal workspace cleanup (startup + transition) | orchestrator.py, workspace.py | test_orchestrator.py |
| Structured logs with issue_id/identifier/session_id | log.py | test_observability.py |
| Operator-visible observability (logs, snapshot) | log.py, orchestrator.py, cli.py | test_observability.py |

### §17.1 Workflow and config parsing

| Bullet | Module | Test file |
|---|---|---|
| Explicit path used | workflow.py | test_workflow.py |
| cwd default `WORKFLOW.md` | workflow.py | test_workflow.py |
| Changes detected and re-applied | workflow.py, orchestrator.py | test_workflow.py, test_orchestrator.py |
| Invalid reload keeps last good + visible error | workflow.py | test_workflow.py |
| Missing file typed error | workflow.py | test_workflow.py |
| Invalid YAML typed error | workflow.py | test_workflow.py |
| Non-map front matter typed error | workflow.py | test_workflow.py |
| Defaults apply | config.py | test_config.py |
| `tracker.kind` validation | config.py | test_config.py |
| `tracker.provider` preserved and validated by adapter | config.py, linear.py | test_config.py, test_linear.py |
| `$VAR` for secret keys and paths | config.py, linear.py | test_config.py, test_linear.py |
| `~` expansion | config.py | test_config.py |
| `codex.command` kept as shell string | config.py | test_config.py |
| Per-state concurrency map normalized, invalid ignored | config.py | test_config.py |
| Prompt renders `issue` and `attempt` | workflow.py | test_workflow.py |
| Unknown variable fails (and unknown filter) | workflow.py | test_workflow.py |

### §17.2 Workspace manager and safety

| Bullet | Module | Test file |
|---|---|---|
| Deterministic path per identifier | workspace.py | test_workspace.py |
| Missing directory created | workspace.py | test_workspace.py |
| Existing directory reused | workspace.py | test_workspace.py |
| Existing non-directory handled (fail) | workspace.py | test_workspace.py |
| Population errors surfaced (after_create failure) | workspace.py | test_workspace.py |
| after_create only on new workspace | workspace.py | test_workspace.py |
| before_run each attempt; failure/timeout aborts | workspace.py, runner.py | test_workspace.py, test_runner.py |
| after_run each attempt; failure/timeout ignored | workspace.py, runner.py | test_workspace.py, test_runner.py |
| before_remove on cleanup; failure ignored (ours: skip delete + notify, deviation 2) | workspace.py, orchestrator.py | test_workspace.py, test_orchestrator.py |
| Sanitization, hash suffix, root containment before launch | workspace.py | test_workspace.py |
| Unchanged identifiers keep key; colliding sanitizations differ | workspace.py | test_workspace.py |
| Agent cwd = workspace; out-of-root rejected | workspace.py, runner.py | test_workspace.py, test_runner.py |

### §17.3 Issue tracker adapter

| Bullet | Module | Test file |
|---|---|---|
| Candidate fetch uses active states + team scope (`list_issues` per state) | linear.py | test_linear.py |
| Empty states -> no request | linear.py | test_linear.py |
| Empty ids -> no request | linear.py | test_linear.py |
| Pagination preserves order | linear.py | test_linear.py |
| Labels lowercased | linear.py | test_linear.py |
| Bad optional metadata -> null/empty | linear.py | test_linear.py |
| State-list omits+logs malformed; ID refresh fails | linear.py | test_linear.py |
| ID refresh returns full snapshots | linear.py | test_linear.py |
| `native_ref` preserves Linear ids | linear.py | test_linear.py |
| Routing/blocker/assignment -> `dispatchable` (+ wake rule) | linear.py | test_linear.py |
| Compact profile published | DESIGN.md (this file) | test_linear.py (asserts defaults match the profile table constants) |
| Error mapping: config, request, status, response, pagination, rate limit, auth | linear.py, mcp_linear.py | test_linear.py, test_mcp_linear.py |
| MCP tool call mapping (list_issues, get_issue + relations + blocker state, list_comments, save_comment, save_issue, get_user) | linear.py | test_linear.py |
| Login: DCR public client, PKCE, Keychain storage; refresh without a browser | mcp_linear.py | test_mcp_linear.py |
| `bridge-event` idempotency (status edited in place, event once, retry after lost response) | linear.py, cli.py | test_linear.py, test_cli.py |

### §17.4 Orchestrator

| Bullet | Module | Test file |
|---|---|---|
| Sort: priority, then oldest, then identifier | orchestrator.py | test_orchestrator.py |
| `dispatchable=false` not eligible | orchestrator.py | test_orchestrator.py |
| Required labels case-insensitive after normalization | orchestrator.py | test_orchestrator.py |
| Active refresh updates running entry | orchestrator.py | test_orchestrator.py |
| Non-active stops without cleanup | orchestrator.py | test_orchestrator.py |
| Terminal stops and cleans | orchestrator.py | test_orchestrator.py |
| No running issues -> reconcile no-op | orchestrator.py | test_orchestrator.py |
| Normal exit -> continuation retry attempt 1 (1 s) | orchestrator.py | test_orchestrator.py |
| Abnormal exit -> 10 s exponential backoff | orchestrator.py | test_orchestrator.py |
| Backoff cap from config | orchestrator.py | test_orchestrator.py |
| Retry entries carry attempt, due, identifier, error | orchestrator.py | test_orchestrator.py |
| Stall detection kills and retries | orchestrator.py | test_orchestrator.py |
| Slot exhaustion requeues with reason | orchestrator.py | test_orchestrator.py |
| Snapshot: running, retrying, totals, rate limits | orchestrator.py | test_observability.py |
| Snapshot timeout/unavailable surfaced (`ls` with missing/stale status file) | cli.py | test_cli.py |

### §17.5 App-server client

| Bullet | Module | Test file |
|---|---|---|
| stdio launch: workspace cwd, `bash -lc <command>` | appserver.py | test_appserver.py |
| Startup follows protocol (initialize, initialized, thread/start) | appserver.py | test_appserver.py |
| Client identity payload valid | appserver.py | test_appserver.py |
| Policy payloads use documented settings | appserver.py | test_appserver.py |
| Thread/turn ids extracted, `session_started` emitted | appserver.py, runner.py | test_appserver.py |
| Read timeout enforced | appserver.py | test_appserver.py |
| Turn timeout enforced | appserver.py | test_appserver.py |
| Framing: WebSocket frames (daemon) and JSON lines (stdio) | appserver.py | test_appserver.py |
| stderr separate from protocol stream | appserver.py | test_appserver.py |
| Approvals per documented policy (decline + escalate) | appserver.py, runner.py | test_appserver.py, test_runner.py |
| Unsupported dynamic tool calls rejected, no stall | appserver.py | test_appserver.py |
| User input per policy, no stall | appserver.py, runner.py | test_appserver.py, test_runner.py |
| Usage and rate-limit telemetry extracted | appserver.py | test_appserver.py |
| Approval/input/usage/rate-limit signals interpreted per protocol | appserver.py | test_appserver.py |
| Client-side tools advertised (none; extension not shipped) | n/a | n/a |
| Provider-native tools (not shipped) | n/a | n/a |

### §17.6 Observability

| Bullet | Module | Test file |
|---|---|---|
| Validation failures operator-visible | log.py, orchestrator.py, cli.py | test_observability.py, test_cli.py |
| Logs include issue/session fields | log.py | test_observability.py |
| Sink failure does not crash | log.py | test_observability.py |
| Token/rate-limit aggregation across repeated updates | orchestrator.py | test_observability.py |
| Status surface driven from orchestrator state (`ls`) | orchestrator.py, cli.py | test_cli.py |
| Humanized summaries (not shipped) | n/a | n/a |

### §17.7 CLI and host lifecycle

| Bullet | Module | Test file |
|---|---|---|
| Positional workflow path | cli.py | test_cli.py |
| Default `./WORKFLOW.md` | cli.py | test_cli.py |
| Error on missing explicit or default file | cli.py | test_cli.py |
| Startup failure surfaced cleanly | cli.py | test_cli.py |
| Exit 0 on normal start and shutdown | cli.py | test_cli.py |
| Nonzero on startup failure or abnormal exit | cli.py | test_cli.py |

### Extensions (PLAN step 3), all in `test_extensions.py`

Thread affinity (record written, resume on later dispatch, new thread + note when
resume fails); wake rule (comment after turn start wakes, before does not, bridge
wakes, signed agent/symphony comments do not, unsigned agent comment causes one
spurious wake, mid-turn race: comment created between turn start and park still
wakes; `waiting` label and In Review both park); `prepare_dispatch` removes `waiting` and
moves In Review to In Progress; escalation
on approval and user input; budget cap and `budget:<n>k`; graceful park; hook env
vars and `repo:` label mapping; notifications (In Review, Done, repeated failure,
skipped removal); Rex tab calls via a fake `rex`; launchd plist parses with
`plistlib` and points at the CLI.

## Open design questions

1. Parked ticket (`waiting` label or In Review) with no ThreadRecord (never run by Symphony):
   design wakes it on any wake comment. Alternative: never dispatch it.
2. Phase 0 probes: daemon `thread/resume` on an old thread streams turn events;
   which client receives server requests on the shared daemon;
   `git worktree add` into the empty directory Symphony creates; the Linear MCP result
   shapes and OAuth details listed in PLAN step 1.
3. Open decisions 1–3 in PLAN are assumed as recommended: daemon transport default,
   agent may close experiment/research tickets, labels `agent` and `waiting`.

"""Linear adapter (SPEC §11; profile in DESIGN.md "Linear adapter profile").

Calls Linear's MCP server tools through `mcp_linear.McpSession` (OAuth via `symphony login`).
Derives `dispatchable` from assignment, blockers and the wake rule. Writes only the
orchestrator's comments, state moves and `waiting` label removal (PLAN deviation 3), plus the
bridge's run-status and event comments (`bridge_event`).

Tool argument shapes follow the server's tools/list input schemas (list_issues, get_issue,
list_comments, save_comment, save_issue, get_user). Result shapes are not published, so the
parsers below accept the plausible variants; docs/PLAN.md phase 0 confirms them.
"""

from __future__ import annotations

import dataclasses
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Protocol

from .config import state_key
from .errors import ConfigError, TrackerError
from .log import log
from .models import BlockerRef, Issue

AGENT_SIGNATURE = "🤖 agent"
BRIDGE_SIGNATURE = "🤖 experiment-run"
RUN_STATUS_SIGNATURE = "🤖 experiment-run status"
SYMPHONY_SIGNATURE = "🤖 symphony"

DEFAULT_ACTIVE_STATES = ("Todo", "In Progress", "In Review")
DEFAULT_TERMINAL_STATES = ("Done", "Canceled", "Duplicate")
DEFAULT_WAKE_STATES = ("In Review",)
DEFAULT_WAITING_LABEL = "waiting"
DEFAULT_IN_PROGRESS_STATE = "In Progress"
DEFAULT_HUMAN_REVIEW_STATE = "In Review"
DEFAULT_COMMENT_WINDOW = 50
PAGE_SIZE = 50
MAX_COMMENT_PAGES = 20

ISSUE_FIELDS = ["id", "uuid", "title", "description", "priority", "url", "gitBranchName",
                "createdAt", "updatedAt", "status", "labels", "assigneeId", "team"]


class Tools(Protocol):
    def call(self, name: str, args: dict) -> Any:
        """Call one MCP tool; return its decoded payload. Raises TrackerError."""
        ...


@dataclass(frozen=True)
class LinearComment:
    created_at: datetime
    body: str
    user_id: str | None = None
    id: str | None = None


def classify_comment(body: str) -> str:
    """'agent' | 'status' | 'bridge' | 'symphony' | 'human' by the first non-blank line."""
    first = next((ln.strip() for ln in (body or "").splitlines() if ln.strip()), "")
    if first.startswith(AGENT_SIGNATURE):
        return "agent"
    if first.startswith(RUN_STATUS_SIGNATURE):
        return "status"
    if first.startswith(BRIDGE_SIGNATURE):
        return "bridge"
    if first.startswith(SYMPHONY_SIGNATURE):
        return "symphony"
    return "human"


def is_wake_comment(c: LinearComment) -> bool:
    """Bridge event and unsigned (Tal) comments wake a parked ticket; the bridge's edited-in-
    place run-status comment does not."""
    return classify_comment(c.body) in ("bridge", "human")


def is_parked(*, state: str, labels: Sequence[str], wake_states: Sequence[str],
              waiting_label: str = DEFAULT_WAITING_LABEL) -> bool:
    """The agent parked the ticket: `waiting` label, or a wake state (In Review)."""
    if waiting_label and state_key(waiting_label) in {state_key(x) for x in labels}:
        return True
    return state_key(state) in {state_key(s) for s in wake_states}


def compute_dispatchable(*, state: str, assignee_id: str | None, viewer_id: str,
                         blockers: Sequence[BlockerRef], comments: Sequence[LinearComment],
                         last_turn_started_at: datetime | None,
                         terminal_states: Sequence[str], wake_states: Sequence[str],
                         labels: Sequence[str] = (),
                         waiting_label: str = DEFAULT_WAITING_LABEL) -> bool:
    """Assignment, blocker and wake rules (PLAN "Linear setup")."""
    if not assignee_id or assignee_id != viewer_id:
        return False
    terminal = {state_key(s) for s in terminal_states}
    if any(b.state is None or state_key(b.state) not in terminal for b in blockers):
        return False
    if is_parked(state=state, labels=labels, wake_states=wake_states,
                 waiting_label=waiting_label):
        wakes = [c.created_at for c in comments if is_wake_comment(c)]
        if not wakes:
            return False
        return last_turn_started_at is None or max(wakes) > last_turn_started_at
    return True


# -- result parsing --------------------------------------------------------------------------

def _parse_ts(value) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _str_or_none(value) -> str | None:
    return value if isinstance(value, str) and value else None


def _name(value) -> str | None:
    """'In Progress' or {"name": "In Progress", ...} -> 'In Progress'."""
    if isinstance(value, dict):
        value = value.get("name")
    return value if isinstance(value, str) and value.strip() else None


def _ref_id(value) -> str | None:
    if isinstance(value, dict):
        value = value.get("id")
    return _str_or_none(value)


def _items(value, *keys: str) -> list[dict]:
    """A list, or {key: [...]}, or {key: {"nodes": [...]}} -> list of dicts."""
    if isinstance(value, dict):
        for k in (*keys, "nodes", "items", "results"):
            if k in value:
                return _items(value[k])
        return []
    return [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []


def _next_cursor(payload) -> str | None:
    if not isinstance(payload, dict):
        return None
    page = payload.get("pageInfo") if isinstance(payload.get("pageInfo"), dict) else payload
    if not page.get("hasNextPage"):
        return None
    cursor = page.get("cursor") or page.get("endCursor") or page.get("nextCursor")
    if not isinstance(cursor, str) or not cursor:
        raise TrackerError("tracker_pagination", "hasNextPage without a cursor")
    return cursor


def _priority(value) -> int | None:
    if isinstance(value, dict):
        value = value.get("value")
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if value != int(value) or not 1 <= int(value) <= 4:
        return None
    return int(value)


def _labels(value) -> tuple[str, ...]:
    raw = value if isinstance(value, list) else _items(value, "labels")
    out: list[str] = []
    for item in raw:
        name = _name(item)
        if name and name.strip().lower() not in out:
            out.append(name.strip().lower())
    return tuple(out)


_IDENTIFIER_RE = re.compile(r"^[A-Za-z][A-Za-z0-9]*-\d+$")


def _identity(item: dict) -> tuple[str | None, str | None]:
    """(uuid, identifier). list_issues/get_issue return `id` (identifier) and `uuid`."""
    ident = _str_or_none(item.get("identifier"))
    uuid = _str_or_none(item.get("uuid"))
    raw = _str_or_none(item.get("id"))
    if raw and _IDENTIFIER_RE.match(raw):
        ident = ident or raw
    elif raw:
        uuid = uuid or raw
    return uuid or ident, ident


def _comments(payload) -> list[LinearComment]:
    out = []
    for c in _items(payload, "comments"):
        created = _parse_ts(c.get("createdAt"))
        body = c.get("body")
        if created is None or not isinstance(body, str):
            continue
        author = c.get("author") or c.get("user")
        out.append(LinearComment(created_at=created, body=body, user_id=_ref_id(author),
                                 id=_str_or_none(c.get("id"))))
    return out


def blocker_refs(relations) -> list[dict]:
    """get_issue(includeRelations) -> relations.blockedBy entries ({id, title})."""
    if isinstance(relations, dict):
        rel = relations.get("relations", relations)
        blocked = rel.get("blockedBy") if isinstance(rel, dict) else None
        return _items(blocked) if blocked is not None else []
    return []


@dataclass(frozen=True)
class RawIssue:
    item: dict
    uuid: str
    identifier: str
    title: str
    state: str
    labels: tuple[str, ...]
    assignee_id: str | None


def parse_issue(item) -> RawIssue:
    """Raises TrackerError(tracker_response) if required fields are unusable."""
    if not isinstance(item, dict):
        raise TrackerError("tracker_response", "issue is not an object")
    if isinstance(item.get("issue"), dict):
        item = {**item["issue"], **{k: v for k, v in item.items() if k != "issue"}}
    uuid, ident = _identity(item)
    title = item.get("title")
    state = _name(item.get("status")) or _name(item.get("state"))
    bad = [k for k, v in (("id", uuid), ("identifier", ident), ("title", title),
                          ("state", state)) if not isinstance(v, str) or not v.strip()]
    if bad:
        raise TrackerError("tracker_response", f"issue missing {', '.join(bad)}")
    assignee = _str_or_none(item.get("assigneeId")) or _ref_id(item.get("assignee"))
    return RawIssue(item=item, uuid=uuid, identifier=ident, title=title, state=state,
                    labels=_labels(item.get("labels")), assignee_id=assignee)


def build_issue(raw: RawIssue, *, team_key: str, dispatchable: bool,
                blockers: Sequence[BlockerRef] = ()) -> Issue:
    item = raw.item
    return Issue(
        id=raw.uuid, identifier=raw.identifier, title=raw.title, state=raw.state,
        dispatchable=dispatchable,
        description=_str_or_none(item.get("description")),
        priority=_priority(item.get("priority")),
        branch_name=_str_or_none(item.get("gitBranchName") or item.get("branchName")),
        url=_str_or_none(item.get("url")),
        assignee_id=raw.assignee_id, labels=raw.labels, blocked_by=tuple(blockers),
        native_ref={"linear_issue_id": raw.uuid, "team_key": team_key},
        created_at=_parse_ts(item.get("createdAt")),
        updated_at=_parse_ts(item.get("updatedAt")))


# -- config ----------------------------------------------------------------------------------

def _state_list_ok(value) -> bool:
    return isinstance(value, (list, tuple)) and all(isinstance(s, str) for s in value)


def validate_provider_config(provider: dict, env: Mapping[str, str] | None = None) -> None:
    """Raise ConfigError(invalid_tracker_config). The login itself is checked on first use."""
    if not isinstance(provider, dict):
        raise ConfigError("invalid_tracker_config", "tracker.provider must be a map")
    team_key = provider.get("team_key")
    if not isinstance(team_key, str) or not team_key.strip():
        raise ConfigError("invalid_tracker_config", "tracker.provider.team_key is required")
    if "wake_states" in provider and not _state_list_ok(provider["wake_states"]):
        raise ConfigError("invalid_tracker_config", "tracker.provider.wake_states must be a list")
    for k in ("endpoint", "assignee", "in_progress_state", "human_review_state",
              "keychain_service", "waiting_label"):
        if k in provider and (not isinstance(provider[k], str) or not provider[k].strip()):
            raise ConfigError("invalid_tracker_config", f"tracker.provider.{k} must be a string")
    if "keychain_service" in provider and not re.match(r"^[\w.@-]+$",
                                                       provider["keychain_service"]):
        raise ConfigError("invalid_tracker_config",
                          "tracker.provider.keychain_service must be a simple name")
    cw = provider.get("comment_window", DEFAULT_COMMENT_WINDOW)
    if isinstance(cw, bool) or not isinstance(cw, int) or not 1 <= cw <= 250:
        raise ConfigError("invalid_tracker_config",
                          "tracker.provider.comment_window must be an int in 1..250")


# -- adapter ---------------------------------------------------------------------------------

def list_comments(tools: Tools, issue_ref: str, *, limit: int = DEFAULT_COMMENT_WINDOW,
                  max_pages: int = MAX_COMMENT_PAGES) -> list[LinearComment]:
    """All of an issue's comments (follows cursors up to max_pages)."""
    out: list[LinearComment] = []
    cursor = None
    for _ in range(max_pages):
        args = {"issueId": issue_ref, "limit": limit, "orderBy": "createdAt"}
        if cursor:
            args["cursor"] = cursor
        payload = tools.call("list_comments", args)
        out.extend(_comments(payload))
        cursor = _next_cursor(payload)
        if cursor is None:
            return out
    raise TrackerError("tracker_pagination", f"more than {max_pages} pages of comments")


def save_comment(tools: Tools, body: str, *, issue_ref: str | None = None,
                 comment_id: str | None = None) -> str | None:
    """Create (issue_ref) or update (comment_id) a comment; returns its id when reported."""
    args = {"id": comment_id, "body": body} if comment_id else {"issueId": issue_ref,
                                                                "body": body}
    payload = tools.call("save_comment", args)
    if isinstance(payload, dict):
        c = payload.get("comment") if isinstance(payload.get("comment"), dict) else payload
        return _str_or_none(c.get("id"))
    return None


class LinearTracker:
    """Implements tracker.Tracker for Linear over MCP tools."""

    def __init__(self, provider: dict, *, active_states: Sequence[str],
                 terminal_states: Sequence[str], tools: Tools,
                 turn_started_at: Callable[[str], datetime | None],
                 required_labels: Sequence[str] = ()):
        self.provider = provider
        self.active_states = tuple(active_states)
        self.terminal_states = tuple(terminal_states)
        self.tools = tools
        self.turn_started_at = turn_started_at
        self.required_labels = tuple(r for r in required_labels if r and r.strip())
        self.team_key = provider["team_key"]
        self.wake_states = tuple(provider.get("wake_states") or DEFAULT_WAKE_STATES)
        self.waiting_label = provider.get("waiting_label") or DEFAULT_WAITING_LABEL
        self.in_progress_state = provider.get("in_progress_state") or DEFAULT_IN_PROGRESS_STATE
        self.human_review_state = (provider.get("human_review_state")
                                   or DEFAULT_HUMAN_REVIEW_STATE)
        self.comment_window = int(provider.get("comment_window") or DEFAULT_COMMENT_WINDOW)
        self._viewer_id: str | None = None

    def viewer_id(self) -> str:
        if self._viewer_id is None:
            payload = self.tools.call("get_user", {"query": "me"})
            user = payload.get("user") if isinstance(payload, dict) and isinstance(
                payload.get("user"), dict) else payload
            vid = _ref_id(user) if isinstance(user, dict) else None
            if not vid:
                raise TrackerError("tracker_response", "get_user(me) returned no id")
            self._viewer_id = vid
        return self._viewer_id

    def _assignee_id(self) -> str:
        a = self.provider.get("assignee") or "me"
        return self.viewer_id() if a == "me" else a

    # -- reads -------------------------------------------------------------------------------

    def _blocker_state(self, ref: str, cache: dict) -> str | None:
        if ref not in cache:
            try:
                payload = self.tools.call("get_issue", {"id": ref, "fields": ["status"]})
            except TrackerError as e:
                if e.category != "tracker_tool_error":
                    raise
                payload = None
            item = payload.get("issue", payload) if isinstance(payload, dict) else None
            cache[ref] = (_name(item.get("status")) or _name(item.get("state"))
                          if isinstance(item, dict) else None)
        return cache[ref]

    def _blockers(self, relations, cache: dict) -> tuple[BlockerRef, ...]:
        out = []
        for b in blocker_refs(relations):
            uuid, ident = _identity(b)
            ref = ident or uuid
            out.append(BlockerRef(id=uuid, identifier=ident,
                                  state=self._blocker_state(ref, cache) if ref else None))
        return tuple(out)

    def _complete(self, raw: RawIssue, relations, *, viewer: str, cache: dict) -> Issue:
        """Fill blockers and dispatchable. Relations and comments are read only when they can
        change the answer (assigned to the viewer; parked)."""
        if raw.assignee_id != viewer:
            return build_issue(raw, team_key=self.team_key, dispatchable=False)
        if relations is None:
            relations = self.tools.call("get_issue", {"id": raw.uuid, "includeRelations": True,
                                                      "fields": ["relations"]})
        blockers = self._blockers(relations, cache)
        parked = is_parked(state=raw.state, labels=raw.labels, wake_states=self.wake_states,
                           waiting_label=self.waiting_label)
        comments = (list_comments(self.tools, raw.uuid, limit=self.comment_window)
                    if parked else [])
        dispatchable = compute_dispatchable(
            state=raw.state, assignee_id=raw.assignee_id, viewer_id=viewer, blockers=blockers,
            comments=comments, last_turn_started_at=self.turn_started_at(raw.identifier),
            terminal_states=self.terminal_states, wake_states=self.wake_states,
            labels=raw.labels, waiting_label=self.waiting_label)
        return build_issue(raw, team_key=self.team_key, dispatchable=dispatchable,
                           blockers=blockers)

    def _list_state(self, state: str) -> list[dict]:
        out: list[dict] = []
        cursor = None
        while True:
            args = {"team": self.team_key, "state": state, "limit": PAGE_SIZE,
                    "fields": ISSUE_FIELDS}
            if self.required_labels:
                args["label"] = self.required_labels[0]
            if cursor:
                args["cursor"] = cursor
            payload = self.tools.call("list_issues", args)
            if not isinstance(payload, (dict, list)):
                raise TrackerError("tracker_response", "list_issues returned no issue list")
            out.extend(_items(payload, "issues"))
            cursor = _next_cursor(payload)
            if cursor is None:
                return out

    def fetch_issues_by_states(self, state_names: Sequence[str]) -> list[Issue]:
        states = list(dict.fromkeys(s for s in state_names))
        if not states:
            return []
        viewer = self._assignee_id()
        cache: dict = {}
        out: list[Issue] = []
        seen: set[str] = set()
        for state in states:
            for item in self._list_state(state):
                try:
                    raw = parse_issue(item)
                    if raw.uuid in seen:
                        continue
                    seen.add(raw.uuid)
                    out.append(self._complete(raw, None, viewer=viewer, cache=cache))
                except TrackerError as e:
                    if e.category != "tracker_response":
                        raise
                    log("linear_malformed_issue", level="warning",
                        issue_identifier=item.get("id") if isinstance(item, dict) else None,
                        error=str(e))
        return out

    def fetch_issues_by_ids(self, issue_ids: Sequence[str]) -> list[Issue]:
        ids = list(dict.fromkeys(issue_ids))
        if not ids:
            return []
        viewer = self._assignee_id()
        cache: dict = {}
        out = []
        for issue_id in ids:
            try:
                payload = self.tools.call("get_issue", {
                    "id": issue_id, "includeRelations": True,
                    "fields": [*ISSUE_FIELDS, "relations"]})
            except TrackerError as e:
                if e.category == "tracker_tool_error" and "not found" in e.message.lower():
                    continue
                raise
            raw = parse_issue(payload)
            if not raw.identifier.upper().startswith(self.team_key.upper() + "-"):
                continue  # outside the configured team (the identifier prefix is the team key)
            out.append(self._complete(raw, payload, viewer=viewer, cache=cache))
        return out

    # -- writes (deviation 3) ----------------------------------------------------------------

    def _save_issue(self, issue_ref: str, **args) -> None:
        self.tools.call("save_issue", {"id": issue_ref, **args})

    def prepare_dispatch(self, issue: Issue) -> Issue:
        """A woken ticket: remove the `waiting` label and move In Review to In Progress."""
        changes: dict = {}
        if state_key(self.waiting_label) in {state_key(x) for x in issue.labels}:
            changes["removeLabels"] = [self.waiting_label]
        if state_key(issue.state) in {state_key(s) for s in self.wake_states}:
            changes["state"] = self.in_progress_state
        if not changes:
            return issue
        self._save_issue(issue.id, **changes)
        fresh = self.fetch_issues_by_ids([issue.id])
        if fresh:
            return fresh[0]
        labels = tuple(x for x in issue.labels if state_key(x) != state_key(self.waiting_label))
        return dataclasses.replace(issue, state=changes.get("state", issue.state), labels=labels)

    def escalate(self, issue: Issue, text: str) -> None:
        self.post_note(issue, text)
        self._save_issue(issue.id, state=self.human_review_state)

    def post_note(self, issue: Issue, text: str) -> None:
        save_comment(self.tools, f"{SYMPHONY_SIGNATURE}\n\n{text}", issue_ref=issue.id)

    def secret_environment_names(self) -> list[str]:
        return []  # the OAuth token lives in the Keychain, never in an environment variable


# -- experiment-run bridge -------------------------------------------------------------------

def status_marker(run: str) -> str:
    return f"Run status: `{run}`"


def event_marker(event_id: str) -> str:
    return f"Event: {event_id}"


def bridge_event(tools: Tools, issue_ref: str, event: dict) -> dict:
    """Idempotent bridge write for one issue.

    event = {"run": REG, "status": MARKDOWN}             -> upsert the run's status comment
          + {"event_id": ID, "event": MARKDOWN}           -> post the signed event comment once
    Comments are found by marker ("Run status: `REG`", "Event: ID"), so a retry after a lost
    response never double-posts. Never changes the issue's state or labels."""
    if not isinstance(event, dict):
        raise ValueError("event must be a JSON object")
    run, status = event.get("run"), event.get("status")
    event_id, body = event.get("event_id"), event.get("event")
    if status is not None and (not isinstance(run, str) or not run.strip()
                               or not isinstance(status, str)):
        raise ValueError("status needs a string run and status")
    if body is not None and (not isinstance(event_id, str) or not event_id.strip()
                             or not isinstance(body, str)):
        raise ValueError("event needs a string event_id and event")
    if status is None and body is None:
        raise ValueError("nothing to write: give status and/or event")
    comments = list_comments(tools, issue_ref)
    result: dict = {"issue": issue_ref}
    if status is not None:
        marker = status_marker(run)
        text = f"{RUN_STATUS_SIGNATURE}\n\n{status.strip()}\n\n{marker}"
        existing = next((c for c in comments if marker in c.body
                         and classify_comment(c.body) == "status"), None)
        if existing is None:
            result["status_comment"] = save_comment(tools, text, issue_ref=issue_ref)
            result["status"] = "created"
        elif existing.body.strip() == text.strip() or not existing.id:
            result["status_comment"] = existing.id
            result["status"] = "unchanged"
        else:
            save_comment(tools, text, comment_id=existing.id)
            result["status_comment"] = existing.id
            result["status"] = "updated"
    if body is not None:
        marker = event_marker(event_id)
        existing = next((c for c in comments if marker in c.body
                         and classify_comment(c.body) == "bridge"), None)
        if existing is not None:
            result["event_comment"] = existing.id
            result["event"] = "exists"
        else:
            text = body.strip()
            if classify_comment(text) != "bridge":
                text = f"{BRIDGE_SIGNATURE}\n\n{text}"
            if marker not in text:
                text = f"{text}\n\n{marker}"
            result["event_comment"] = save_comment(tools, text, issue_ref=issue_ref)
            result["event"] = "posted"
    return result

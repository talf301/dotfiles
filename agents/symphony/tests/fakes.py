"""Shared fakes (DESIGN.md "Test fakes"). No network, Linear, Codex daemon, launchd or Rex."""

from __future__ import annotations

import dataclasses
from collections import deque
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from symphony.errors import AgentError, TrackerError
from symphony.models import Issue

EPOCH = datetime(2026, 1, 1, tzinfo=timezone.utc)


class FakeClock:
    """Monotonic ms plus a wall clock that advance together."""

    def __init__(self, start_ms: int = 1_000_000):
        self.ms = start_ms
        self._wall0 = EPOCH
        self._start = start_ms

    def now_ms(self) -> int:
        return self.ms

    def monotonic(self) -> float:
        return self.ms / 1000

    def wall(self) -> datetime:
        return self._wall0 + timedelta(milliseconds=self.ms - self._start)

    def advance(self, ms: int) -> None:
        self.ms += ms


def make_issue(identifier: str = "TAL-1", **kw) -> Issue:
    """Issue with sensible defaults; id defaults to 'id-<identifier>'."""
    base = dict(id=f"id-{identifier}", identifier=identifier, title=f"Title {identifier}",
                state="Todo", dispatchable=True, labels=("agent",))
    base.update(kw)
    return Issue(**base)


class FakeTracker:
    """In-memory Tracker. Records calls in `calls` as (method, args)."""

    def __init__(self, issues: list[Issue] | None = None):
        self.issues: dict[str, Issue] = {i.id: i for i in issues or []}
        self.calls: list[tuple[str, Any]] = []
        self._fail: dict[str, TrackerError] = {}
        self.in_progress_state = "In Progress"
        self.human_review_state = "In Review"
        self.wake_states = ("in review",)
        self.waiting_label = "waiting"

    def set_issue(self, issue: Issue) -> None:
        self.issues[issue.id] = issue

    def set_state(self, issue_id: str, state: str, **changes) -> None:
        self.issues[issue_id] = dataclasses.replace(self.issues[issue_id], state=state, **changes)

    def fail_next(self, method: str, err: TrackerError | None = None) -> None:
        self._fail[method] = err or TrackerError("tracker_request", "fake failure")

    def _maybe_fail(self, method: str) -> None:
        if method in self._fail:
            raise self._fail.pop(method)

    def fetch_issues_by_states(self, state_names):
        self.calls.append(("fetch_issues_by_states", tuple(state_names)))
        self._maybe_fail("fetch_issues_by_states")
        wanted = {s.strip().lower() for s in state_names}
        return [i for i in self.issues.values() if i.state.strip().lower() in wanted]

    def fetch_issues_by_ids(self, issue_ids):
        self.calls.append(("fetch_issues_by_ids", tuple(issue_ids)))
        self._maybe_fail("fetch_issues_by_ids")
        return [issue for ref in issue_ids for issue in self.issues.values()
                if ref in (issue.id, issue.identifier)]

    def prepare_dispatch(self, issue):
        self.calls.append(("prepare_dispatch", issue.id))
        self._maybe_fail("prepare_dispatch")
        cur = self.issues.get(issue.id, issue)
        parked_label = self.waiting_label in cur.labels
        if cur.state.strip().lower() in self.wake_states or parked_label:
            labels = tuple(x for x in cur.labels if x != self.waiting_label)
            self.issues[issue.id] = dataclasses.replace(
                cur, labels=labels,
                state=self.in_progress_state if cur.state.strip().lower() in self.wake_states
                else cur.state)
            return self.issues[issue.id]
        return cur

    def escalate(self, issue, text):
        self.calls.append(("escalate", (issue.id, text)))
        self._maybe_fail("escalate")
        if issue.id in self.issues:
            self.set_state(issue.id, self.human_review_state)

    def post_note(self, issue, text):
        self.calls.append(("post_note", (issue.id, text)))
        self._maybe_fail("post_note")

    def secret_environment_names(self):
        return []

    def called(self, method: str) -> list[Any]:
        return [args for m, args in self.calls if m == method]


class FakeMcp:
    """Linear MCP tool stand-in for linear.py tests (implements linear.Tools).

    `on(tool, result)` answers every call of that tool; result may be a callable(args) or an
    Exception to raise. `reply(tool, result)` queues a one-shot answer used before `on`.
    Every call is recorded in `calls` as (tool, args)."""

    def __init__(self):
        self.handlers: dict[str, Any] = {}
        self.queued: dict[str, deque] = {}
        self.calls: list[tuple[str, dict]] = []

    def on(self, tool: str, result: Any) -> "FakeMcp":
        self.handlers[tool] = result
        return self

    def reply(self, tool: str, result: Any) -> "FakeMcp":
        self.queued.setdefault(tool, deque()).append(result)
        return self

    def call(self, name: str, args: dict) -> Any:
        self.calls.append((name, dict(args)))
        if self.queued.get(name):
            r = self.queued[name].popleft()
        elif name in self.handlers:
            r = self.handlers[name]
        else:
            raise AssertionError(f"unexpected MCP tool call: {name} {args}")
        if isinstance(r, Exception):
            raise r
        return r(args) if callable(r) else r

    def called(self, name: str) -> list[dict]:
        return [a for n, a in self.calls if n == name]


class FakeLinearBoard:
    """A tiny stateful Linear behind FakeMcp: issues, comments, labels, states.

    Supports list_issues (team/state/label filters), get_issue (fields, relations),
    list_comments (paged), save_comment (create/update), save_issue (state, add/remove
    labels) and get_user(me)."""

    def __init__(self, viewer: str = "user-tal", page_size: int | None = None):
        self.viewer = viewer
        self.issues: dict[str, dict] = {}       # identifier -> record
        self.comments: dict[str, list[dict]] = {}
        self.page_size = page_size
        self._n = 0
        self.mcp = FakeMcp()
        for tool in ("list_issues", "get_issue", "list_comments", "save_comment",
                     "save_issue", "get_user"):
            self.mcp.on(tool, getattr(self, "_" + tool))

    def add(self, identifier: str, *, state: str = "Todo", labels=("agent",),
            assignee: str | None = "user-tal", blocked_by=(), title: str | None = None,
            **extra) -> dict:
        rec = {"id": identifier, "uuid": f"uuid-{identifier}", "title": title or identifier,
               "status": state, "labels": list(labels), "assigneeId": assignee,
               "blockedBy": list(blocked_by), "team": "TAL",
               "createdAt": "2026-01-01T00:00:00.000Z", **extra}
        self.issues[identifier] = rec
        self.comments.setdefault(identifier, [])
        return rec

    def comment(self, identifier: str, body: str, at: datetime) -> dict:
        self._n += 1
        c = {"id": f"c{self._n}", "body": body, "createdAt": at.isoformat(),
             "author": {"id": self.viewer}}
        self.comments[identifier].append(c)
        return c

    def _find(self, ref: str) -> dict | None:
        for rec in self.issues.values():
            if ref in (rec["id"], rec["uuid"]):
                return rec
        return None

    def _public(self, rec: dict, fields) -> dict:
        out = {k: v for k, v in rec.items() if k != "blockedBy"}
        if fields:
            out = {k: v for k, v in out.items() if k in fields or k == "id"}
        if not fields or "relations" in fields:
            out["relations"] = {"blockedBy": [{"id": b, "title": b} for b in rec["blockedBy"]],
                                "blocks": [], "relatedTo": []}
        return out

    def _list_issues(self, args):
        items = [r for r in self.issues.values()
                 if r["team"] == args.get("team") and r["status"] == args.get("state")
                 and (not args.get("label") or args["label"] in r["labels"])]
        return {"issues": [self._public(r, args.get("fields")) for r in items],
                "hasNextPage": False}

    def _get_issue(self, args):
        rec = self._find(args["id"])
        if rec is None:
            raise TrackerError("tracker_tool_error", f"get_issue: Issue {args['id']} not found")
        return self._public(rec, args.get("fields"))

    def _list_comments(self, args):
        rec = self._find(args["issueId"])
        items = list(self.comments[rec["id"]])
        size = self.page_size or args.get("limit") or 50
        start = int(args.get("cursor") or 0)
        page = items[start:start + size]
        more = start + size < len(items)
        return {"comments": page, "hasNextPage": more,
                "cursor": str(start + size) if more else None}

    def _save_comment(self, args):
        if args.get("id"):
            for cs in self.comments.values():
                for c in cs:
                    if c["id"] == args["id"]:
                        c["body"] = args["body"]
                        return {"id": c["id"], "body": c["body"]}
            raise TrackerError("tracker_tool_error", "comment not found")
        rec = self._find(args["issueId"])
        return {"id": self.comment(rec["id"], args["body"], datetime.now(timezone.utc))["id"]}

    def _save_issue(self, args):
        rec = self._find(args["id"])
        if "state" in args:
            rec["status"] = args["state"]
        for label in args.get("addLabels", []):
            if label not in rec["labels"]:
                rec["labels"].append(label)
        for label in args.get("removeLabels", []):
            if label in rec["labels"]:
                rec["labels"].remove(label)
        return {"id": rec["id"], "status": rec["status"], "labels": rec["labels"]}

    def _get_user(self, args):
        assert args == {"query": "me"}
        return {"id": self.viewer, "name": "Tal"}


class FakeKeychain:
    """In-memory stand-in for mcp_linear.Keychain."""

    def __init__(self, record: dict | None = None):
        self.record = dict(record) if record else None
        self.writes = 0

    def read(self):
        return dict(self.record) if self.record else None

    def write(self, record):
        self.writes += 1
        self.record = dict(record)

    def delete(self):
        self.record = None


class FakeAppServer:
    """Scripted Codex app-server implementing appserver.Transport.

    - `on(method, result)` answers client requests (result may be a callable(params) -> result,
      or an Exception instance to answer with a JSON-RPC error).
    - `push(msg)` queues a server notification or server request for the client to receive.
    - `after(method, msgs)` queues msgs right after answering that request (e.g. turn events).
      Each call adds one batch; each answered request of that method releases the next batch.
    - `silence()` makes recv() time out forever; `close()` simulates the server going away.
    - `sent` holds every client message; `replies` maps server-request id -> client result.
    """

    def __init__(self, pid: str | None = None):
        self.pid = pid
        self.sent: list[dict] = []
        self.replies: dict[Any, dict] = {}
        self.handlers: dict[str, Any] = {}
        self.followups: dict[str, deque] = {}
        self._turns: deque = deque()
        self.inbox: deque = deque()
        self.closed = False
        self.silent = False
        self._req = 0

    def on(self, method: str, result: Any) -> "FakeAppServer":
        self.handlers[method] = result
        return self

    def after(self, method: str, msgs: list[dict]) -> "FakeAppServer":
        self.followups.setdefault(method, deque()).append(list(msgs))
        return self

    def push(self, msg: dict) -> "FakeAppServer":
        self.inbox.append(msg)
        return self

    def notify(self, method: str, **params) -> "FakeAppServer":
        return self.push({"jsonrpc": "2.0", "method": method, "params": params})

    def server_request(self, method: str, **params) -> int:
        self._req += 1
        rid = 1000 + self._req
        self.push({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        return rid

    def script_turn(self, thread_id: str, turn_id: str, *, status: str = "completed",
                    events: list[dict] | None = None, error: str | None = None) -> "FakeAppServer":
        """Answer the next turn/start with turn_id, then emit started, events, completed.
        Call once per expected turn; turns are answered in order."""
        turn = {"id": turn_id, "items": [], "status": "inProgress"}
        done = dict(turn, status=status, error={"message": error} if error else None)
        self._turns.append({"turn": turn})
        self.on("turn/start", lambda params: self._turns.popleft())
        self.after("turn/start", [
            {"jsonrpc": "2.0", "method": "turn/started",
             "params": {"threadId": thread_id, "turn": turn}},
            *(events or []),
            {"jsonrpc": "2.0", "method": "turn/completed",
             "params": {"threadId": thread_id, "turn": done}},
        ])
        return self

    def silence(self) -> None:
        self.silent = True

    # Transport interface
    def send(self, msg: dict) -> None:
        if self.closed:
            raise AgentError("port_exit", "fake server closed")
        self.sent.append(msg)
        method, mid = msg.get("method"), msg.get("id")
        if method is None and mid is not None:  # client reply to a server request
            self.replies[mid] = msg.get("result", msg.get("error"))
            return
        if mid is None:  # client notification
            return
        h = self.handlers.get(method)
        if isinstance(h, Exception):
            self.inbox.append({"jsonrpc": "2.0", "id": mid,
                               "error": {"code": -32000, "message": str(h)}})
        elif h is not None or method in self.handlers:
            result = h(msg.get("params") or {}) if callable(h) else h
            self.inbox.append({"jsonrpc": "2.0", "id": mid, "result": result or {}})
        else:
            return  # unanswered: lets tests exercise read timeouts
        batches = self.followups.get(method)
        if batches:
            self.inbox.extend(batches.popleft())

    def recv(self, timeout_s: float) -> dict | None:
        if self.closed and not self.inbox:
            raise AgentError("port_exit", "fake server closed")
        if self.silent or not self.inbox:
            return None
        return self.inbox.popleft()

    def close(self) -> None:
        self.closed = True

    def requests(self, method: str) -> list[dict]:
        return [m.get("params") or {} for m in self.sent if m.get("method") == method]


class FakeWorkerHandle:
    def __init__(self, issue: Issue, attempt: int | None):
        self.issue = issue
        self.attempt = attempt
        self.cancelled: str | None = None
        self.stopping = False
        self.alive = True

    def cancel(self, reason: str) -> None:
        self.cancelled = reason

    def stop_after_turn(self) -> None:
        self.stopping = True

    def is_alive(self) -> bool:
        return self.alive


class FakeWorkerFactory:
    """Records dispatches; tests call orchestrator.on_worker_exit themselves."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.spawned: list[FakeWorkerHandle] = []

    def __call__(self, issue: Issue, attempt: int | None, ctx) -> FakeWorkerHandle:
        if self.fail:
            raise RuntimeError("spawn failed")
        h = FakeWorkerHandle(issue, attempt)
        self.spawned.append(h)
        return h

    def for_issue(self, issue_id: str) -> list[FakeWorkerHandle]:
        return [h for h in self.spawned if h.issue.id == issue_id]


class FakeNotifier:
    def __init__(self):
        self.sent: list[tuple[str, str]] = []

    def notify(self, title: str, message: str) -> None:
        self.sent.append((title, message))

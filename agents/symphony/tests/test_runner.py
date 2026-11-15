"""CodexRunner tests (§10.7, §16.5, §17.5 runner parts) with FakeAppServer and FakeTracker."""

from __future__ import annotations

import dataclasses
import threading
from datetime import timedelta
from pathlib import Path

import pytest

from fakes import FakeAppServer, FakeClock, FakeTracker, make_issue
from symphony.config import (
    AgentConfig,
    CodexConfig,
    ExtensionsConfig,
    HooksConfig,
    ServiceConfig,
    TrackerConfig,
)
from symphony.models import ThreadRecord
from symphony.runner import CodexRunner, token_cap
from symphony.workflow import WorkflowDefinition
from symphony.workspace import ThreadStore, WorkspaceManager

ACTIVE = ("Todo", "In Progress", "In Review")
TEMPLATE = "FULL PROMPT for {{ issue.identifier }}: {{ issue.title }} (turn {{ turn_number }})"
CONT = "CONTINUE {{ issue.identifier }} turn {{ turn_number }}/{{ max_turns }}"


@pytest.fixture(autouse=True)
def quiet_log_stub(monkeypatch):
    """While log.py is still a stub, keep WorkspaceManager's log calls from raising."""
    from symphony import log as log_mod, workspace as ws_mod
    try:
        log_mod.log("test_probe", "debug")
    except NotImplementedError:
        monkeypatch.setattr(ws_mod, "log", lambda *a, **k: None)


def make_config(root: Path, *, hooks=None, max_turns=5, token_cap_=3_000_000,
                continuation=CONT) -> ServiceConfig:
    return ServiceConfig(
        workflow_path=root / "WORKFLOW.md", poll_interval_ms=30000, workspace_root=root,
        tracker=TrackerConfig(kind="linear", provider={"team_key": "TAL"},
                              required_labels=("agent",), active_states=ACTIVE,
                              terminal_states=("Done", "Canceled")),
        hooks=hooks or HooksConfig(),
        agent=AgentConfig(max_turns=max_turns, continuation_prompt=continuation),
        codex=CodexConfig(read_timeout_ms=200, turn_timeout_ms=60000),
        ext=ExtensionsConfig(token_cap=token_cap_))


def usage_note(thread_id, total):
    return {"jsonrpc": "2.0", "method": "thread/tokenUsage/updated",
            "params": {"threadId": thread_id, "turnId": "t", "tokenUsage": {
                "last": {"inputTokens": 0, "outputTokens": 0, "totalTokens": 0},
                "total": {"inputTokens": total, "outputTokens": 0, "totalTokens": total}}}}


class Harness:
    def __init__(self, tmp_path, *, issue=None, server=None, **cfg_kw):
        self.root = tmp_path / "workspaces"
        self.cfg = make_config(self.root, **cfg_kw)
        self.issue = issue or make_issue("TAL-1", state="Todo", title="Fix the loader")
        self.tracker = FakeTracker([self.issue])
        self.server = server or FakeAppServer(pid="99")
        self.server.on("initialize", {})
        self.server.on("thread/start", {"thread": {"id": "th-new"}})
        self.server.on("thread/name/set", {})
        self.server.on("turn/interrupt", {})
        self.clock = FakeClock()
        self.events = []
        self.rex_calls = []
        self.factory_calls = []
        self.workspaces = WorkspaceManager(self.root, self.cfg.hooks, repos={},
                                           strip_env=["LINEAR_API_KEY"])
        self.threads = ThreadStore(self.root)

    def factory(self, codex, cwd, env):
        self.factory_calls.append((codex, cwd, env))
        return self.server

    def runner(self):
        wf = WorkflowDefinition(config={}, prompt_template=TEMPLATE,
                                path=self.cfg.workflow_path)
        return CodexRunner(config=self.cfg, workflow=wf, tracker=self.tracker,
                           workspaces=self.workspaces, threads=self.threads,
                           on_event=self.events.append, transport_factory=self.factory,
                           rex=lambda i, p, t: self.rex_calls.append((i.identifier, p, t)),
                           wall=self.clock.wall)

    def run(self, attempt=None, *, cancel=None, stop=None):
        return self.runner().run_attempt(self.issue, attempt, cancel=cancel or threading.Event(),
                                         stop_after_turn=stop or threading.Event())

    def prompts(self):
        return [p["input"][0]["text"] for p in self.server.requests("turn/start")]


# -- normal flow --------------------------------------------------------------------------------

def test_new_thread_full_prompt_then_continuation_until_not_routable(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1")
    h.server.script_turn("th-new", "tu-2", events=[usage_note("th-new", 500)])
    real_fetch = h.tracker.fetch_issues_by_ids
    calls = {"n": 0}

    def fetch(ids):  # after turn 2 the agent parked with the waiting label (not dispatchable)
        calls["n"] += 1
        if calls["n"] == 2:
            h.tracker.set_state(h.issue.id, "In Progress", dispatchable=False,
                                labels=("agent", "waiting"))
        return real_fetch(ids)

    h.tracker.fetch_issues_by_ids = fetch
    out = h.run()
    assert (out.normal, out.reason) == (True, "not_routable")
    assert "waiting" in out.final_issue.labels
    assert h.prompts() == ["FULL PROMPT for TAL-1: Fix the loader (turn 1)",
                           "CONTINUE TAL-1 turn 2/5"]
    assert h.server.requests("thread/name/set") == [{"threadId": "th-new",
                                                      "name": "TAL-1: Fix the loader"}]
    ws = h.root / "TAL-1"
    assert all(p["cwd"] == str(ws) for p in h.server.requests("turn/start"))
    assert h.server.requests("thread/start")[0]["cwd"] == str(ws)
    rec = h.threads.read("TAL-1")
    assert rec.thread_id == "th-new" and rec.last_turn_id == "tu-2" and rec.total_tokens == 500
    assert rec.last_turn_started_at is not None
    assert h.rex_calls == [("TAL-1", ws, "th-new")]
    assert [e.event for e in h.events].count("session_started") == 2
    assert h.server.closed


def test_tracker_secrets_stripped_from_transport_env(tmp_path, monkeypatch):
    monkeypatch.setenv("SOME_TRACKER_SECRET", "secret")
    h = Harness(tmp_path)
    h.tracker.secret_environment_names = lambda: ["SOME_TRACKER_SECRET"]
    h.server.script_turn("th-new", "tu-1")
    h.tracker.set_state(h.issue.id, "Todo")
    h.tracker.fetch_issues_by_ids = lambda ids: []
    h.run()
    _, cwd, env = h.factory_calls[0]
    assert "SOME_TRACKER_SECRET" not in env and cwd == h.root / "TAL-1"


def test_max_turns_reached_is_normal(tmp_path):
    h = Harness(tmp_path, max_turns=2)
    h.server.script_turn("th-new", "tu-1").script_turn("th-new", "tu-2")
    out = h.run()
    assert (out.normal, out.reason) == (True, "max_turns")
    assert len(h.server.requests("turn/start")) == 2


def test_terminal_or_missing_issue_after_turn_stops(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1")
    orig = h.tracker.fetch_issues_by_ids

    def fetch(ids):
        h.tracker.set_state(h.issue.id, "Done")
        return orig(ids)

    h.tracker.fetch_issues_by_ids = fetch
    assert h.run().reason == "not_routable"


def test_label_removed_stops(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1")
    orig = h.tracker.fetch_issues_by_ids

    def fetch(ids):
        h.tracker.set_state(h.issue.id, "In Progress", labels=())
        return orig(ids)

    h.tracker.fetch_issues_by_ids = fetch
    assert h.run().reason == "not_routable"


def test_stop_after_turn(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1")
    stop = threading.Event()
    stop.set()
    out = h.run(stop=stop)
    assert (out.normal, out.reason) == (True, "stopped")
    assert len(h.server.requests("turn/start")) == 1


def test_prepare_dispatch_unparks_woken_ticket(tmp_path):
    issue = make_issue("TAL-1", state="In Progress", labels=("agent", "waiting"))
    h = Harness(tmp_path, issue=issue, max_turns=1)
    h.server.script_turn("th-new", "tu-1")
    h.run()
    assert h.tracker.called("prepare_dispatch") == ["id-TAL-1"]
    assert h.tracker.issues["id-TAL-1"].state == "In Progress"
    assert h.tracker.issues["id-TAL-1"].labels == ("agent",)


# -- thread affinity ----------------------------------------------------------------------------

def test_existing_thread_is_resumed_with_continuation_prompt(tmp_path):
    h = Harness(tmp_path, max_turns=1)
    h.threads.write("TAL-1", ThreadRecord(thread_id="th-old", created_at=h.clock.wall(),
                                          total_tokens=10))
    h.server.on("thread/resume", {"thread": {"id": "th-old"}})
    h.server.script_turn("th-old", "tu-9")
    h.run()
    assert h.server.requests("thread/start") == []
    assert h.server.requests("thread/resume")[0]["threadId"] == "th-old"
    assert h.server.requests("turn/start")[0]["threadId"] == "th-old"
    assert h.prompts() == ["CONTINUE TAL-1 turn 1/1"]
    assert h.server.requests("thread/name/set") == []
    assert h.tracker.called("post_note") == []


def test_resume_failure_starts_new_thread_and_posts_note(tmp_path):
    h = Harness(tmp_path, max_turns=1)
    h.threads.write("TAL-1", ThreadRecord(thread_id="th-gone", created_at=h.clock.wall()))
    h.server.on("thread/resume", RuntimeError("no rollout"))
    h.server.script_turn("th-new", "tu-1")
    h.run()
    assert h.threads.read("TAL-1").thread_id == "th-new"
    assert h.prompts()[0].startswith("FULL PROMPT")
    notes = h.tracker.called("post_note")
    assert len(notes) == 1 and "th-gone" in notes[0][1]


def test_turn_start_time_recorded_before_turn(tmp_path):
    h = Harness(tmp_path, max_turns=1)
    seen = {}

    def on_turn_start(params):
        seen["rec"] = h.threads.read("TAL-1")
        return {"turn": {"id": "tu-1"}}

    h.server.on("turn/start", on_turn_start)
    h.server.after("turn/start", [{"jsonrpc": "2.0", "method": "turn/completed", "params": {
        "threadId": "th-new", "turn": {"id": "tu-1", "status": "completed"}}}])
    before = h.clock.wall()
    h.run()
    assert seen["rec"].last_turn_started_at == before


# -- failures -----------------------------------------------------------------------------------

def test_turn_failure_is_abnormal(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1", status="failed", error="overloaded")
    out = h.run()
    assert (out.normal, out.reason, out.error) == (False, "turn_failed", "overloaded")


def test_cancel_is_reported_as_reconciliation(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1", status="interrupted")
    cancel = threading.Event()
    cancel.set()
    out = h.run(cancel=cancel)
    assert (out.normal, out.reason) == (False, "canceled_by_reconciliation")


def test_startup_failure_runs_after_run_hook(tmp_path):
    marker = tmp_path / "after_run.txt"
    h = Harness(tmp_path, hooks=HooksConfig(after_run=f"echo ran > {marker}"))
    h.server.handlers.pop("initialize")  # no reply -> response_timeout
    out = h.run()
    assert (out.normal, out.reason) == (False, "response_timeout")
    assert marker.exists()
    assert h.server.closed


def test_before_run_failure_aborts_without_starting_codex(tmp_path):
    marker = tmp_path / "after_run.txt"
    h = Harness(tmp_path, hooks=HooksConfig(before_run="exit 3",
                                            after_run=f"echo ran > {marker}"))
    out = h.run()
    assert (out.normal, out.reason) == (False, "hook_failed")
    assert h.factory_calls == [] and not marker.exists()


def test_after_run_failure_ignored(tmp_path):
    h = Harness(tmp_path, max_turns=1, hooks=HooksConfig(after_run="exit 1"))
    h.server.script_turn("th-new", "tu-1")
    assert h.run().normal


def test_template_error_is_abnormal(tmp_path):
    h = Harness(tmp_path, continuation="{{ nope.missing }}")
    h.server.script_turn("th-new", "tu-1")
    out = h.run()
    assert not out.normal and out.reason == "template_render_error"


def test_refresh_error_is_abnormal(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1")
    h.tracker.fail_next("fetch_issues_by_ids")
    out = h.run()
    assert (out.normal, out.reason) == (False, "tracker_request")


def test_port_exit_mid_turn_is_abnormal(tmp_path):
    h = Harness(tmp_path)
    h.server.on("turn/start", {"turn": {"id": "tu-1"}})
    orig_send = h.server.send

    def send(msg):
        orig_send(msg)
        if msg.get("method") == "turn/start":
            h.server.close()

    h.server.send = send
    out = h.run()
    assert (out.normal, out.reason) == (False, "port_exit")


# -- escalation and budget ----------------------------------------------------------------------

def test_approval_request_escalates_to_in_review(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1", status="interrupted", events=[
        {"jsonrpc": "2.0", "id": 77, "method": "item/commandExecution/requestApproval",
         "params": {"threadId": "th-new", "turnId": "tu-1", "itemId": "i", "startedAtMs": 0,
                    "command": "aws ec2 run-instances"}}])
    out = h.run()
    assert (out.normal, out.reason) == (True, "escalated")
    assert h.server.replies[77] == {"decision": "cancel"}
    (issue_id, text), = h.tracker.called("escalate")
    assert issue_id == "id-TAL-1"
    assert text.startswith("Question for Tal:") and "aws ec2 run-instances" in text
    assert h.tracker.issues["id-TAL-1"].state == "In Review"
    assert "approval_escalated" in [e.event for e in h.events]


def test_user_input_request_escalates(tmp_path):
    h = Harness(tmp_path)
    h.server.script_turn("th-new", "tu-1", status="interrupted", events=[
        {"jsonrpc": "2.0", "id": 78, "method": "item/tool/requestUserInput",
         "params": {"threadId": "th-new", "turnId": "tu-1", "itemId": "i", "isBlocking": True,
                    "questions": [{"id": "q", "header": "h", "question": "Which split?"}]}}])
    out = h.run()
    assert out.reason == "escalated"
    assert "Which split?" in h.tracker.called("escalate")[0][1]


def test_budget_exhausted_before_start(tmp_path):
    h = Harness(tmp_path, token_cap_=1000)
    h.threads.write("TAL-1", ThreadRecord(thread_id="th-old", created_at=h.clock.wall(),
                                          total_tokens=1000))
    out = h.run()
    assert (out.normal, out.reason) == (True, "budget")
    assert h.factory_calls == []
    assert "budget" in h.tracker.called("escalate")[0][1]


def test_budget_label_overrides_and_recheck_after_turn(tmp_path):
    issue = make_issue("TAL-1", labels=("agent", "budget:2k"))
    h = Harness(tmp_path, issue=issue, token_cap_=10_000_000)
    h.server.script_turn("th-new", "tu-1", events=[usage_note("th-new", 2500)])
    out = h.run()
    assert (out.normal, out.reason) == (True, "budget")
    assert len(h.server.requests("turn/start")) == 1


def test_token_cap_label_parsing():
    assert token_cap(make_issue(labels=("agent", "budget:500k")), 7) == 500_000
    assert token_cap(make_issue(labels=("agent",)), 7) == 7
    assert token_cap(make_issue(labels=("budget:abc",)), 7) == 7

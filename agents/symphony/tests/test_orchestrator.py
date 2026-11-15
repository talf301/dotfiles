"""Orchestrator: selection, dispatch, reconciliation, retry, stall, cleanup (SPEC §17.4) plus
our extensions (draining, graceful park, notifications, token baseline)."""

from __future__ import annotations

import dataclasses
import functools
import time
from datetime import timedelta

import pytest

from conftest import MINIMAL_WORKFLOW
from fakes import EPOCH, FakeAppServer, make_issue
from symphony.config import TrackerConfig
from symphony.errors import ConfigError
from symphony.models import AgentEvent, ThreadRecord, WorkerOutcome
from symphony.notify import MacNotifier
from symphony.orchestrator import (NO_SLOTS, Orchestrator, ThreadWorker, retry_delay_ms,
                                   sort_for_dispatch)
from symphony.runner import CodexRunner
from symphony.tracker import make_tracker
from symphony.workflow import WorkflowStore


def workflow(extra: str = "", required: str = "[agent]") -> str:
    return MINIMAL_WORKFLOW.replace("required_labels: [agent]", f"required_labels: {required}") \
        .replace("workspace:\n", extra + "workspace:\n", 1)


@pytest.fixture
def make_orch(write_workflow, tracker, workers, notifier, clock, tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))  # hooks run bash -l

    def _make(text: str = MINIMAL_WORKFLOW, *, start: bool = True, worker_factory=None,
              status_path=None) -> Orchestrator:
        store = WorkflowStore(write_workflow(text))
        orch = Orchestrator(store, worker_factory=worker_factory or workers, notifier=notifier,
                            tracker_factory=lambda cfg, env, started: tracker,
                            clock_ms=clock.now_ms, wall=clock.wall, status_path=status_path)
        if start:
            orch.startup()
        return orch

    return _make


def ev(event: str, **kw) -> AgentEvent:
    return AgentEvent(event=event, timestamp=EPOCH, **kw)


# -- selection -------------------------------------------------------------------------------

def test_sort_priority_then_oldest_then_identifier():
    t0 = EPOCH
    issues = [
        make_issue("TAL-5", priority=None, created_at=t0),
        make_issue("TAL-4", priority=0, created_at=t0),
        make_issue("TAL-3", priority=2, created_at=t0 + timedelta(days=1)),
        make_issue("TAL-2", priority=2, created_at=t0),
        make_issue("TAL-1", priority=1, created_at=None),
        make_issue("TAL-6", priority=2, created_at=None),
        make_issue("TAL-0", priority=2, created_at=None),
    ]
    assert [i.identifier for i in sort_for_dispatch(issues)] == [
        "TAL-1", "TAL-2", "TAL-3", "TAL-0", "TAL-6", "TAL-4", "TAL-5"]


def test_tick_dispatches_in_sorted_order_until_slots_run_out(make_orch, tracker, workers):
    for n, p in ((1, 3), (2, 1), (3, 2), (4, 4)):
        tracker.set_issue(make_issue(f"TAL-{n}", priority=p))
    orch = make_orch(workflow("agent:\n  max_concurrent_agents: 2\n"))
    orch.tick()
    assert [h.issue.identifier for h in workers.spawned] == ["TAL-2", "TAL-3"]
    assert set(orch.state.running) == {"id-TAL-2", "id-TAL-3"}
    assert orch.state.claimed == {"id-TAL-2", "id-TAL-3"}
    assert workers.spawned[0].attempt is None


def test_not_dispatchable_is_not_eligible(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1", dispatchable=False))
    orch = make_orch()
    orch.tick()
    assert workers.spawned == []


def test_required_labels_case_insensitive(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1", labels=("agent",)))
    tracker.set_issue(make_issue("TAL-2", labels=("other",)))
    orch = make_orch(workflow(required="[' AGENT ']"))
    orch.tick()
    assert [h.issue.identifier for h in workers.spawned] == ["TAL-1"]


def test_blank_required_label_matches_nothing(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1", labels=("agent", "")))
    orch = make_orch(workflow(required="['']"))
    orch.tick()
    assert workers.spawned == []


def test_terminal_or_inactive_candidates_not_dispatched(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1", state="Backlog"))
    tracker.set_issue(make_issue("TAL-2", state="Done"))
    orch = make_orch()
    assert not orch.should_dispatch(tracker.issues["id-TAL-1"])
    assert not orch.should_dispatch(tracker.issues["id-TAL-2"])


def test_claimed_or_running_not_redispatched(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.tick()
    assert len(workers.spawned) == 1


def test_per_state_concurrency_limit(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1", state="Todo"))
    tracker.set_issue(make_issue("TAL-2", state="Todo"))
    tracker.set_issue(make_issue("TAL-3", state="In Progress"))
    orch = make_orch(workflow("agent:\n  max_concurrent_agents_by_state: {todo: 1}\n"))
    orch.tick()
    assert sorted(h.issue.identifier for h in workers.spawned) == ["TAL-1", "TAL-3"]


def test_candidate_fetch_failure_skips_tick(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    tracker.fail_next("fetch_issues_by_states")
    orch.tick()
    assert workers.spawned == []
    orch.tick()
    assert len(workers.spawned) == 1


def test_validation_failure_skips_dispatch_but_reconciles(make_orch, tracker, workers,
                                                          write_workflow):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_issue(make_issue("TAL-2"))
    write_workflow(MINIMAL_WORKFLOW.replace("    team_key: TAL\n", "") + "\n")
    tracker.calls.clear()
    orch.tick()
    assert orch.validation_error is not None
    assert tracker.called("fetch_issues_by_ids") == [("id-TAL-1",)]
    assert tracker.called("fetch_issues_by_states") == []
    assert len(workers.spawned) == 1


def test_spawn_failure_schedules_retry(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    workers.fail = True
    orch = make_orch()
    orch.tick()
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.error) == (1, "failed to spawn agent")
    assert "id-TAL-1" not in orch.state.running


# -- startup ---------------------------------------------------------------------------------

def test_startup_validation_failure_raises(make_orch, tracker):
    with pytest.raises(ConfigError):
        make_orch(MINIMAL_WORKFLOW.replace("    team_key: TAL\n", ""))


def test_startup_removes_terminal_workspaces(make_orch, tracker, tmp_path):
    root = tmp_path / "workspaces"
    (root / "TAL-1").mkdir(parents=True)
    (root / "TAL-2").mkdir(parents=True)
    tracker.set_issue(make_issue("TAL-1", state="Done"))
    tracker.set_issue(make_issue("TAL-2", state="Todo"))
    make_orch()
    assert not (root / "TAL-1").exists()
    assert (root / "TAL-2").exists()


def test_startup_cleanup_fetch_failure_continues(make_orch, tracker):
    tracker.fail_next("fetch_issues_by_states")
    orch = make_orch()
    assert orch.state is not None


def test_startup_cleanup_skipped_removal_notifies(make_orch, tracker, notifier, tmp_path):
    (tmp_path / "workspaces" / "TAL-1").mkdir(parents=True)
    tracker.set_issue(make_issue("TAL-1", state="Done"))
    make_orch(workflow("hooks:\n  before_remove: exit 1\n"))
    assert (tmp_path / "workspaces" / "TAL-1").is_dir()
    assert notifier.sent and notifier.sent[0][0] == "TAL-1: workspace kept"
    assert notifier.sent[0][1].endswith("(symphony open TAL-1)")


# -- reconciliation --------------------------------------------------------------------------

def test_reconcile_without_running_is_noop(make_orch, tracker):
    orch = make_orch()
    tracker.calls.clear()
    orch.reconcile()
    assert tracker.calls == []


def test_active_refresh_updates_running_entry(make_orch, tracker):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_state("id-TAL-1", "In Progress", title="New title")
    orch.reconcile()
    assert orch.state.running["id-TAL-1"].issue.state == "In Progress"
    assert orch.state.running["id-TAL-1"].issue.title == "New title"


def test_non_active_stops_without_cleanup(make_orch, tracker, workers, tmp_path):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    ws = tmp_path / "workspaces" / "TAL-1"
    ws.mkdir(parents=True)
    tracker.set_state("id-TAL-1", "Backlog")
    orch.reconcile()
    h = workers.spawned[0]
    assert h.cancelled == "non_active_state"
    assert "id-TAL-1" not in orch.state.running and "id-TAL-1" not in orch.state.claimed
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "canceled_by_reconciliation"))
    assert ws.is_dir()
    assert orch.state.retry_attempts == {}


def test_terminal_stops_and_cleans_after_worker_exits(make_orch, tracker, workers, tmp_path):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    ws = tmp_path / "workspaces" / "TAL-1"
    ws.mkdir(parents=True)
    tracker.set_state("id-TAL-1", "Done")
    orch.reconcile()
    assert workers.spawned[0].cancelled == "terminal_state"
    assert ws.is_dir()  # the worker may still be in after_run
    orch.tick()
    assert len(workers.spawned) == 1
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "canceled_by_reconciliation"))
    assert not ws.exists()
    assert orch.state.retry_attempts == {} and orch.draining == {}


def test_missing_issue_stops_without_cleanup(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    del tracker.issues["id-TAL-1"]
    orch.reconcile()
    assert workers.spawned[0].cancelled == "issue_missing"


def test_active_but_unroutable_parks_after_turn(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_state("id-TAL-1", "In Progress", dispatchable=False,
                      labels=("agent", "waiting"))
    orch.reconcile()
    orch.reconcile()
    h = workers.spawned[0]
    assert h.stopping and h.cancelled is None
    assert "id-TAL-1" in orch.state.running
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "stopped"))
    orch.on_retry_due("id-TAL-1")
    assert "id-TAL-1" not in orch.state.claimed and len(workers.spawned) == 1


def test_refresh_failure_keeps_workers(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.fail_next("fetch_issues_by_ids")
    orch.reconcile()
    assert "id-TAL-1" in orch.state.running and workers.spawned[0].cancelled is None


# -- retries ---------------------------------------------------------------------------------

def test_retry_delay_formula():
    assert retry_delay_ms(1, continuation=True, max_backoff_ms=300000) == 1000
    assert [retry_delay_ms(n, continuation=False, max_backoff_ms=300000) for n in (1, 2, 3)] \
        == [10000, 20000, 40000]
    assert retry_delay_ms(10, continuation=False, max_backoff_ms=300000) == 300000


def test_normal_exit_schedules_continuation_retry(make_orch, tracker, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "max_turns"))
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.due_at_ms, r.identifier, r.error) == (1, clock.now_ms() + 1000,
                                                                "TAL-1", None)
    assert "id-TAL-1" in orch.state.completed and "id-TAL-1" in orch.state.claimed


def test_abnormal_exit_backoff_grows(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "turn_failed", "boom"))
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.due_at_ms - clock.now_ms(), r.error) == (
        1, 10000, "worker exited: turn_failed")
    clock.advance(10000)
    orch.fire_due_retries()
    assert workers.spawned[-1].attempt == 1
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "turn_failed"))
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.due_at_ms - clock.now_ms()) == (2, 20000)


def test_backoff_cap_from_config(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("agent:\n  max_retry_backoff_ms: 15000\n"))
    orch.dispatch(tracker.issues["id-TAL-1"], 4)
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "turn_failed"))
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.due_at_ms - clock.now_ms()) == (5, 15000)


def test_retry_not_fired_before_due(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "max_turns"))
    clock.advance(999)
    orch.fire_due_retries()
    assert len(workers.spawned) == 1
    clock.advance(1)
    orch.fire_due_retries()
    assert len(workers.spawned) == 2 and workers.spawned[1].attempt == 1


def test_slot_exhaustion_requeues_with_reason(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    tracker.set_issue(make_issue("TAL-2", priority=4))
    orch = make_orch(workflow("agent:\n  max_concurrent_agents: 1\n"))
    orch.schedule_retry("id-TAL-2", 2, identifier="TAL-2", error="x", continuation=False)
    orch.tick()  # TAL-1 takes the only slot
    clock.advance(20000)
    orch.fire_due_retries()
    r = orch.state.retry_attempts["id-TAL-2"]
    assert (r.attempt, r.error) == (3, NO_SLOTS)
    assert [h.issue.identifier for h in workers.spawned] == ["TAL-1"]


def test_retry_releases_missing_and_unroutable(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-2", dispatchable=False))
    orch = make_orch()
    for iid, ident in (("id-TAL-1", "TAL-1"), ("id-TAL-2", "TAL-2")):
        orch.schedule_retry(iid, 1, identifier=ident, error=None, continuation=True)
        orch.on_retry_due(iid)
        assert iid not in orch.state.claimed and iid not in orch.state.retry_attempts
    assert workers.spawned == []


def test_retry_terminal_cleans_workspace(make_orch, tracker, tmp_path):
    tracker.set_issue(make_issue("TAL-1", state="Canceled"))
    orch = make_orch()
    ws = tmp_path / "workspaces" / "TAL-1"
    ws.mkdir(parents=True)
    orch.schedule_retry("id-TAL-1", 1, identifier="TAL-1", error=None, continuation=True)
    orch.on_retry_due("id-TAL-1")
    assert not ws.exists() and "id-TAL-1" not in orch.state.claimed


def test_retry_refresh_failure_reschedules(make_orch, tracker):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.schedule_retry("id-TAL-1", 1, identifier="TAL-1", error=None, continuation=True)
    tracker.fail_next("fetch_issues_by_ids")
    orch.on_retry_due("id-TAL-1")
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.error) == (2, "retry refresh failed")


# -- stall detection -------------------------------------------------------------------------

def test_stall_cancels_and_retries_after_worker_exits(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("codex:\n  stall_timeout_ms: 60000\n"))
    orch.tick()
    clock.advance(30000)
    orch.on_codex_update("id-TAL-1", ev("notification"))
    clock.advance(60000)
    orch.reconcile()
    assert "id-TAL-1" in orch.state.running  # activity 60 s ago is not > 60 s
    clock.advance(1)
    orch.reconcile()
    assert workers.spawned[0].cancelled == "stalled"
    r = orch.state.retry_attempts["id-TAL-1"]
    assert (r.attempt, r.error) == (1, "stalled")
    clock.advance(10000)
    orch.fire_due_retries()  # old worker still draining: no second worker on the workspace
    assert len(workers.spawned) == 1 and "id-TAL-1" in orch.state.retry_attempts
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "canceled_by_reconciliation"))
    clock.advance(1000)
    orch.fire_due_retries()
    assert len(workers.spawned) == 2 and workers.spawned[1].attempt == 1


def test_stall_detection_disabled(make_orch, tracker, workers, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("codex:\n  stall_timeout_ms: 0\n"))
    orch.tick()
    clock.advance(10**9)
    orch.reconcile()
    assert workers.spawned[0].cancelled is None


# -- dynamic reload --------------------------------------------------------------------------

def test_reload_applies_poll_interval_and_concurrency(make_orch, tracker, workers,
                                                     write_workflow):
    tracker.set_issue(make_issue("TAL-1"))
    tracker.set_issue(make_issue("TAL-2"))
    orch = make_orch(workflow("agent:\n  max_concurrent_agents: 1\n"))
    orch.tick()
    assert len(workers.spawned) == 1
    write_workflow(workflow("polling:\n  interval_ms: 1234\nagent:\n  max_concurrent_agents: 2\n"))
    orch.tick()
    assert orch.state.poll_interval_ms == 1234 and len(workers.spawned) == 2


def test_step_ticks_on_poll_interval(make_orch, tracker, clock):
    orch = make_orch(workflow("polling:\n  interval_ms: 5000\n"))
    orch.step()
    n = len(tracker.called("fetch_issues_by_states"))
    clock.advance(4999)
    orch.step()
    assert len(tracker.called("fetch_issues_by_states")) == n
    clock.advance(1)
    orch.step()
    assert len(tracker.called("fetch_issues_by_states")) == n + 1


def test_inbox_messages_routed(make_orch, tracker):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.inbox.put(("codex", "id-TAL-1", ev("session_started", message="th-tu")))
    orch.inbox.put(("exit", "id-TAL-1", WorkerOutcome(True, "max_turns")))
    orch.drain_inbox(0)
    assert "id-TAL-1" not in orch.state.running and orch.state.retry_attempts


# -- notifications (extension) ---------------------------------------------------------------

def test_notify_in_review_after_turn(make_orch, tracker, notifier):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_codex_update("id-TAL-1", ev("session_started", message="th-tu"))
    final = dataclasses.replace(tracker.issues["id-TAL-1"], state="In Review")
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "not_routable", final_issue=final))
    assert notifier.sent == [("TAL-1: In Review",
                              "Title TAL-1 (symphony open TAL-1)")]


def test_no_state_notify_without_a_turn(make_orch, tracker, notifier):
    tracker.set_issue(make_issue("TAL-1", state="In Review"))
    orch = make_orch()
    orch.tick()
    final = tracker.issues["id-TAL-1"]
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "hook_failed", final_issue=final))
    assert notifier.sent == []


def test_notify_done_when_reconcile_sees_terminal(make_orch, tracker, notifier):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_codex_update("id-TAL-1", ev("session_started", message="th-tu"))
    tracker.set_state("id-TAL-1", "Done")
    orch.reconcile()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "canceled_by_reconciliation",
                                                  final_issue=tracker.issues["id-TAL-1"]))
    assert [t for t, _ in notifier.sent] == ["TAL-1: Done"]


def test_notify_escalation(make_orch, tracker, notifier):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "escalated",
                                                  final_issue=tracker.issues["id-TAL-1"]))
    assert notifier.sent[0][0] == "TAL-1: In Review"


def test_notify_repeated_failure_once(make_orch, tracker, notifier, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("notify:\n  failure_attempts: 2\n"))
    orch.tick()
    for _ in range(3):
        orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "turn_failed"))
        clock.advance(10**6)
        orch.fire_due_retries()
    assert [t for t, _ in notifier.sent] == ["TAL-1: failing"]
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "max_turns"))
    assert "id-TAL-1" not in orch.state.failure_notified


def test_notify_disabled(make_orch, tracker, notifier):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("notify:\n  enabled: false\n"))
    orch.tick()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "escalated"))
    assert notifier.sent == []


def test_terminal_cleanup_skipped_notifies(make_orch, tracker, notifier, tmp_path):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("hooks:\n  before_remove: exit 3\n"))
    orch.tick()
    ws = tmp_path / "workspaces" / "TAL-1"
    ws.mkdir(parents=True)
    tracker.set_state("id-TAL-1", "Done")
    orch.reconcile()
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "canceled_by_reconciliation"))
    assert ws.is_dir()
    assert [t for t, _ in notifier.sent] == ["TAL-1: workspace kept"]


def test_mac_notifier_uses_osascript_and_never_raises():
    calls = []
    MacNotifier(run=lambda argv, **kw: calls.append((argv, kw)) or
                type("R", (), {"returncode": 0})()).notify("TAL-1: Done", "msg")
    argv, kw = calls[0]
    assert argv[0] == "osascript" and argv[-2:] == ["TAL-1: Done", "msg"]
    assert kw["timeout"] == 10

    def boom(*a, **k):
        raise OSError("no osascript")
    MacNotifier(run=boom).notify("t", "m")


# -- token accounting with thread affinity (extension) ---------------------------------------

def test_resumed_thread_usage_uses_record_baseline(make_orch, tracker, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.threads.write("TAL-1", ThreadRecord(thread_id="th", created_at=clock.wall(),
                                             total_tokens=1000))
    orch.tick()
    assert orch.state.running["id-TAL-1"].thread_id == "th"
    orch.on_codex_update("id-TAL-1", ev("notification", usage={
        "input_tokens": 1200, "output_tokens": 300, "total_tokens": 1500}))
    orch.on_codex_update("id-TAL-1", ev("notification", usage={
        "input_tokens": 1300, "output_tokens": 400, "total_tokens": 1700}))
    t = orch.state.codex_totals
    assert t["total_tokens"] == 700
    assert t["input_tokens"] + t["output_tokens"] == 700


def test_new_thread_after_failed_resume_counts_from_zero(make_orch, tracker, clock):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.threads.write("TAL-1", ThreadRecord(thread_id="old", created_at=clock.wall(),
                                             total_tokens=5000))
    orch.tick()
    orch.on_codex_update("id-TAL-1", ev("notification", usage={
        "input_tokens": 80, "output_tokens": 20, "total_tokens": 100}))
    assert orch.state.codex_totals["total_tokens"] == 100


# -- real worker thread ----------------------------------------------------------------------

def test_make_tracker_kinds():
    with pytest.raises(ConfigError) as e:
        make_tracker(TrackerConfig(kind="jira", provider={}), {}, lambda i: None)
    assert e.value.category == "unsupported_tracker_kind"
    t = make_tracker(TrackerConfig(kind="linear", provider={"team_key": "TAL"},
                                   active_states=("Todo",), terminal_states=("Done",),
                                   required_labels=("agent",)),
                     {}, lambda i: None)
    # Built lazily: no Keychain read or network until the first tool call.
    assert t.team_key == "TAL" and t.required_labels == ("agent",)
    assert t.tools.url == "https://mcp.linear.app/mcp" and t.secret_environment_names() == []


class CrashingRunner:
    def __init__(self, **kw):
        pass

    def run_attempt(self, issue, attempt, *, cancel, stop_after_turn):
        raise RuntimeError("bug")


def test_thread_worker_posts_exit_on_crash(make_orch, tracker):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch(workflow("rex:\n  enabled: false\n"),
                     worker_factory=lambda i, a, ctx: ThreadWorker(
                         i, a, ctx, runner_factory=CrashingRunner))
    orch.tick()
    orch.state.running["id-TAL-1"].worker.join(5)
    orch.drain_inbox(1)
    r = orch.state.retry_attempts["id-TAL-1"]
    assert r.error == "worker exited: worker_crashed"


def test_end_to_end_one_turn_with_fake_app_server(make_orch, tracker, clock):
    """Real ThreadWorker + CodexRunner against FakeAppServer: dispatch, one turn, the agent
    parks the ticket, the worker exits normally and the continuation retry releases it."""
    issue = make_issue("TAL-1")
    tracker.set_issue(issue)
    server = FakeAppServer()
    server.on("initialize", {}).on("thread/start", {"thread": {"id": "th-1"}})
    server.on("thread/name/set", {})
    server.script_turn("th-1", "tu-1", events=[
        {"jsonrpc": "2.0", "method": "thread/tokenUsage/updated",
         "params": {"threadId": "th-1", "tokenUsage": {"total": {
             "inputTokens": 70, "outputTokens": 30, "totalTokens": 100}}}}])
    answer = server.handlers["turn/start"]

    def turn_start(params):  # the agent parks the ticket during its turn
        tracker.set_state(issue.id, "In Progress", dispatchable=False,
                          labels=("agent", "waiting"))
        return answer(params)
    server.on("turn/start", turn_start)

    runner = functools.partial(CodexRunner, transport_factory=lambda *a: server)
    orch = make_orch(workflow("rex:\n  enabled: false\n"),
                     worker_factory=lambda i, a, ctx: ThreadWorker(i, a, ctx,
                                                                   runner_factory=runner))
    orch.tick()
    worker = orch.state.running[issue.id].worker
    worker.join(10)
    assert not worker.is_alive()
    orch.drain_inbox(0)
    assert orch.state.codex_totals["total_tokens"] == 100
    r = orch.state.retry_attempts[issue.id]
    assert (r.attempt, r.error) == (1, None)
    assert orch.threads.read("TAL-1").thread_id == "th-1"
    clock.advance(1000)
    orch.fire_due_retries()
    assert issue.id not in orch.state.claimed


def test_shutdown_cancels_running_workers(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    workers.spawned[0].alive = False
    start = time.monotonic()
    orch.shutdown(grace_s=1)
    assert workers.spawned[0].cancelled == "shutdown"
    assert time.monotonic() - start < 1


# -- review fixes ----------------------------------------------------------------------------

def test_unparseable_workflow_blocks_dispatch_but_reconciles(make_orch, tracker, workers,
                                                            write_workflow):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_issue(make_issue("TAL-2"))
    write_workflow("---\ntracker: [unclosed\n---\nbody\n")
    tracker.calls.clear()
    orch.tick()
    assert orch.validation_error is not None
    assert tracker.called("fetch_issues_by_ids") == [("id-TAL-1",)]
    assert tracker.called("fetch_issues_by_states") == []
    assert len(workers.spawned) == 1 and workers.spawned[0].cancelled is None
    write_workflow(MINIMAL_WORKFLOW + "\n")
    orch.tick()
    assert orch.validation_error is None and len(workers.spawned) == 2


@pytest.mark.parametrize("change", [
    {"labels": ()},                                      # agent label removed
    {"state": "In Progress", "dispatchable": False},     # reassigned or newly blocked
])
def test_unroutable_outside_park_states_interrupts_now(make_orch, tracker, workers, change):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_state("id-TAL-1", change.pop("state", "Todo"), **change)
    orch.reconcile()
    h = workers.spawned[0]
    assert h.cancelled == "not_routable" and not h.stopping
    assert "id-TAL-1" not in orch.state.running and "id-TAL-1" in orch.draining


def test_label_removed_while_parked_still_interrupts(make_orch, tracker, workers):
    tracker.set_issue(make_issue("TAL-1"))
    orch = make_orch()
    orch.tick()
    tracker.set_state("id-TAL-1", "In Progress", dispatchable=False, labels=("waiting",))
    orch.reconcile()
    assert workers.spawned[0].cancelled == "not_routable"


def test_woken_ticket_over_budget_escalates_once(make_orch, tracker, clock, tmp_path):
    """Real wake rule + real runner: the budget escalation must mark the wake as handled."""
    from datetime import datetime, timezone
    from symphony.linear import LinearComment, compute_dispatchable

    now = datetime.now(timezone.utc)
    comments = [LinearComment(now - timedelta(minutes=5), "Go on please")]

    class WakeTracker(type(tracker)):
        started = None

        def _wake(self, issue):
            ok = compute_dispatchable(
                state=issue.state, assignee_id="me", viewer_id="me", blockers=(),
                comments=comments, last_turn_started_at=self.started(issue.identifier),
                terminal_states=("Done",), wake_states=("In Review",), labels=issue.labels)
            return dataclasses.replace(issue, dispatchable=ok)

        def fetch_issues_by_states(self, state_names):
            return [self._wake(i) for i in super().fetch_issues_by_states(state_names)]

        def fetch_issues_by_ids(self, issue_ids):
            return [self._wake(i) for i in super().fetch_issues_by_ids(issue_ids)]

    wt = WakeTracker([make_issue("TAL-1", state="In Review")])
    runner = functools.partial(CodexRunner, transport_factory=lambda *a: FakeAppServer())
    store = WorkflowStore(write_wf := tmp_path / "WORKFLOW.md")
    write_wf.write_text(workflow("rex:\n  enabled: false\nbudget:\n  token_cap: 1000\n"))

    def factory(cfg, env, started):
        wt.started = started
        return wt

    orch = Orchestrator(store, notifier=MacNotifier.__new__(MacNotifier),
                        worker_factory=lambda i, a, ctx: ThreadWorker(i, a, ctx,
                                                                      runner_factory=runner),
                        tracker_factory=factory, clock_ms=clock.now_ms, wall=clock.wall)
    orch.notifier = type("N", (), {"notify": lambda self, t, m: None})()
    orch.startup()
    orch.threads.write("TAL-1", ThreadRecord(thread_id="th-1", created_at=now,
                                             last_turn_started_at=now - timedelta(minutes=10),
                                             total_tokens=5000))
    for _ in range(5):
        orch.tick()
        for e in list(orch.state.running.values()):
            e.worker.join(5)
        orch.drain_inbox(0)
        clock.advance(1000)
        orch.fire_due_retries()
        for e in list(orch.state.running.values()):
            e.worker.join(5)
        orch.drain_inbox(0)
    assert len(wt.called("escalate")) == 1

"""Structured logs, sink failures, token/rate-limit aggregation and the snapshot (SPEC §17.6)."""

from __future__ import annotations

import io
import json
import sys

import pytest

from conftest import MINIMAL_WORKFLOW
from fakes import EPOCH, make_issue
from symphony import log as logmod
from symphony.errors import ConfigError
from symphony.log import log, setup_logging
from symphony.models import AgentEvent, WorkerOutcome
from symphony.orchestrator import Orchestrator
from symphony.workflow import WorkflowStore


@pytest.fixture
def sinks(tmp_path):
    stream = io.StringIO()
    path = tmp_path / "logs" / "symphony.jsonl"
    setup_logging(path, stream=stream)
    yield stream, path
    setup_logging(None, stream=sys.stderr)


def records(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.fixture
def orch(write_workflow, tracker, workers, notifier, clock, tmp_path):
    store = WorkflowStore(write_workflow())
    o = Orchestrator(store, worker_factory=workers, notifier=notifier,
                     tracker_factory=lambda cfg, env, started: tracker,
                     clock_ms=clock.now_ms, wall=clock.wall,
                     status_path=tmp_path / "state" / "status.json")
    o.startup()
    return o


def ev(event, **kw):
    return AgentEvent(event=event, timestamp=EPOCH, **kw)


def test_log_writes_key_value_and_jsonl(sinks):
    stream, path = sinks
    log("dispatch", issue_id="u1", issue_identifier="TAL-1", note="two words", n=3)
    line = stream.getvalue().strip()
    assert "level=info event=dispatch issue_id=u1 issue_identifier=TAL-1" in line
    assert 'note="two words"' in line and "n=3" in line
    rec = records(path)[0]
    assert rec["event"] == "dispatch" and rec["issue_identifier"] == "TAL-1" and rec["n"] == 3


def test_log_truncates_values(sinks):
    _, path = sinks
    log("x", big="a" * 2000)
    assert len(records(path)[0]["big"]) <= logmod.VALUE_LIMIT + 3


def test_jsonl_sink_failure_does_not_crash(tmp_path):
    stream = io.StringIO()
    bad = tmp_path / "dir.jsonl"
    bad.mkdir()  # opening a directory for append fails
    setup_logging(bad, stream=stream)
    try:
        log("one")
        log("two")
    finally:
        setup_logging(None, stream=sys.stderr)
    out = stream.getvalue()
    assert out.count("event=log_sink_failed sink=jsonl") == 1
    assert "event=two" in out


def test_stream_sink_failure_falls_back_to_jsonl(tmp_path):
    class Broken:
        def write(self, s):
            raise OSError("closed")

        def flush(self):
            pass
    path = tmp_path / "s.jsonl"
    setup_logging(path, stream=Broken())
    try:
        log("one")
        log("two")
    finally:
        setup_logging(None, stream=sys.stderr)
    events = [r["event"] for r in records(path)]
    assert events == ["log_sink_failed", "one", "two"]


def test_session_logs_carry_issue_and_session_fields(sinks, orch, tracker):
    _, path = sinks
    tracker.set_issue(make_issue("TAL-1"))
    orch.tick()
    orch.on_codex_update("id-TAL-1", ev("session_started", message="th-tu"))
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(False, "turn_failed", "boom"))
    by_event = {r["event"]: r for r in records(path)}
    assert by_event["dispatch"]["issue_id"] == "id-TAL-1"
    s = by_event["session_started"]
    assert (s["issue_identifier"], s["session_id"]) == ("TAL-1", "th-tu")
    x = by_event["worker_exit"]
    assert (x["session_id"], x["outcome"], x["reason"]) == ("th-tu", "failed", "turn_failed")
    assert by_event["retry_scheduled"]["issue_identifier"] == "TAL-1"


def test_startup_validation_failure_is_logged(sinks, write_workflow, workers, notifier, tracker):
    _, path = sinks
    store = WorkflowStore(write_workflow(MINIMAL_WORKFLOW.replace("    team_key: TAL\n", "")))
    o = Orchestrator(store, worker_factory=workers, notifier=notifier,
                     tracker_factory=lambda *a: tracker)
    with pytest.raises(ConfigError):
        o.startup()
    rec = [r for r in records(path) if r["event"] == "startup_validation_failed"][0]
    assert rec["level"] == "error" and rec["category"] == "invalid_tracker_config"


def test_tick_validation_failure_visible_in_snapshot(sinks, orch, write_workflow):
    _, path = sinks
    write_workflow(MINIMAL_WORKFLOW.replace("    team_key: TAL\n", "") + "\n")
    orch.tick()
    assert any(r["event"] == "dispatch_validation_failed" for r in records(path))
    assert "invalid_tracker_config" in orch.snapshot()["error"]


def test_token_and_rate_limit_aggregation(orch, tracker, clock):
    tracker.set_issue(make_issue("TAL-1"))
    tracker.set_issue(make_issue("TAL-2"))
    orch.tick()
    for usage in ((10, 5, 15), (30, 10, 40), (30, 10, 40)):
        orch.on_codex_update("id-TAL-1", ev("notification", usage=dict(
            input_tokens=usage[0], output_tokens=usage[1], total_tokens=usage[2])))
    orch.on_codex_update("id-TAL-2", ev("notification", usage=dict(
        input_tokens=7, output_tokens=3, total_tokens=10)))
    orch.on_codex_update("id-TAL-1", ev("notification", rate_limits={"primary": 1}))
    orch.on_codex_update("id-TAL-2", ev("notification", rate_limits={"primary": 2}))
    t = orch.state.codex_totals
    assert (t["input_tokens"], t["output_tokens"], t["total_tokens"]) == (37, 13, 50)
    assert orch.state.codex_rate_limits == {"primary": 2}
    e = orch.state.running["id-TAL-1"]
    assert (e.codex_total_tokens, e.last_reported_total_tokens) == (40, 40)
    clock.advance(4000)
    orch.on_worker_exit("id-TAL-1", WorkerOutcome(True, "max_turns"))
    assert orch.state.codex_totals["seconds_running"] == pytest.approx(4.0)
    # late events from an exited worker are ignored
    orch.on_codex_update("id-TAL-1", ev("notification", usage=dict(
        input_tokens=999, output_tokens=999, total_tokens=1998)))
    assert orch.state.codex_totals["total_tokens"] == 50


def test_snapshot_and_status_file(orch, tracker, clock, tmp_path):
    tracker.set_issue(make_issue("TAL-1", url="https://linear.app/x/TAL-1"))
    tracker.set_issue(make_issue("TAL-2", url="https://linear.app/x/TAL-2"))
    orch.tick()
    orch.on_codex_update("id-TAL-1", ev("session_started", message="th-tu", thread_id="th",
                                        turn_id="tu"))
    orch.on_worker_exit("id-TAL-2", WorkerOutcome(False, "turn_failed"))
    clock.advance(2000)
    snap = orch.snapshot()
    (row,) = snap["running"]
    assert (row["issue_identifier"], row["turn_count"], row["session_id"], row["url"]) == (
        "TAL-1", 1, "th-tu", "https://linear.app/x/TAL-1")
    (retry,) = snap["retrying"]
    assert (retry["issue_identifier"], retry["attempt"], retry["due_in_ms"], retry["url"]) == (
        "TAL-2", 1, 8000, "https://linear.app/x/TAL-2")
    assert snap["codex_totals"]["seconds_running"] == pytest.approx(2.0)  # live TAL-1 only
    assert set(snap["codex_totals"]) == {"input_tokens", "output_tokens", "total_tokens",
                                         "seconds_running"}
    assert "rate_limits" in snap
    orch.tick()
    status = json.loads((tmp_path / "state" / "status.json").read_text())
    assert status["running"][0]["issue_identifier"] == "TAL-1"


def test_status_write_failure_does_not_crash(orch, tmp_path):
    orch.status_path = tmp_path / "missing-parent-is-a-file" / "status.json"
    (tmp_path / "missing-parent-is-a-file").write_text("x")
    orch.tick()

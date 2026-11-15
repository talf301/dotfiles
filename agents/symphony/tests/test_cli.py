"""CLI and host lifecycle (SPEC §17.7), operator commands, Rex tabs, WORKFLOW.md and the plist."""

from __future__ import annotations

import json
import plistlib
import sys
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from conftest import MINIMAL_WORKFLOW
from fakes import FakeTracker, FakeWorkerFactory, FakeNotifier, make_issue
from symphony import cli, linear, mcp_linear, rex
from symphony.config import validate_dispatch_config
from symphony.log import setup_logging, state_dir
from symphony.models import ThreadRecord
from symphony.workflow import WorkflowStore, render_prompt
from symphony.workspace import ThreadStore

PKG = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def no_keychain_and_reset_logging(monkeypatch):
    def no_security(*a, **kw):
        raise AssertionError("tests must not touch the Keychain")
    monkeypatch.setattr(mcp_linear.Keychain, "read", lambda self: None)
    monkeypatch.setattr(mcp_linear.Keychain, "write", no_security)
    monkeypatch.setattr(mcp_linear.Keychain, "delete", no_security)
    yield
    setup_logging(None, stream=sys.stderr)


def run_cli(argv, tracker=None, **kw):
    stop = threading.Event()
    stop.set()  # start up, then shut down immediately
    t = tracker or FakeTracker()
    return cli.main(argv, stop=stop, tracker_factory=lambda cfg, env, started: t,
                    worker_factory=FakeWorkerFactory(), notifier=FakeNotifier(), **kw)


def status():
    return json.loads((state_dir() / "status.json").read_text())


# -- §17.7 path selection and exit codes ------------------------------------------------------

def test_positional_workflow_path_is_used(write_workflow):
    path = write_workflow(name="custom.md")
    assert run_cli([str(path)]) == 0
    assert status()["workflow"] == str(path)


def test_run_subcommand_with_workflow_flag(write_workflow):
    path = write_workflow(name="other.md")
    assert run_cli(["run", "--workflow", str(path)]) == 0
    assert status()["workflow"] == str(path)


def test_default_workflow_in_cwd(write_workflow, tmp_path, monkeypatch):
    write_workflow()
    monkeypatch.chdir(tmp_path)
    assert run_cli([]) == 0
    assert status()["workflow"] == str(tmp_path / "WORKFLOW.md")


def test_missing_explicit_workflow_exits_2(tmp_path, capsys):
    assert run_cli([str(tmp_path / "nope.md")]) == 2
    assert "workflow file not found" in capsys.readouterr().err


def test_missing_default_workflow_exits_2(tmp_path, monkeypatch, capsys):
    monkeypatch.chdir(tmp_path)
    assert run_cli(["run"]) == 2
    assert "WORKFLOW.md" in capsys.readouterr().err


def test_path_given_twice_is_rejected(write_workflow):
    path = write_workflow()
    assert run_cli(["run", str(path), "--workflow", str(path)]) == 2


def test_invalid_yaml_is_a_clean_startup_failure(write_workflow, capsys):
    path = write_workflow("---\ntracker: [unclosed\n---\nbody\n")
    assert run_cli([str(path)]) == 1
    err = capsys.readouterr().err
    assert "startup failed: workflow_parse_error" in err and "Traceback" not in err


def test_validation_failure_exits_1(write_workflow, capsys):
    path = write_workflow(MINIMAL_WORKFLOW.replace("kind: linear", "kind: jira"))
    assert run_cli([str(path)]) == 1
    assert "unsupported_tracker_kind" in capsys.readouterr().err


def test_startup_needs_no_env_secret(write_workflow, monkeypatch):
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)
    assert run_cli([str(write_workflow())]) == 0


def test_abnormal_exception_exits_1(write_workflow, capsys):
    class Broken(FakeTracker):
        def fetch_issues_by_states(self, state_names):
            raise RuntimeError("boom")

    assert run_cli([str(write_workflow())], tracker=Broken()) == 1
    assert "stopped abnormally: RuntimeError: boom" in capsys.readouterr().err


def test_run_loop_stops_on_event_and_exits_0(write_workflow):
    stop = threading.Event()
    threading.Timer(0.3, stop.set).start()
    rc = cli.main([str(write_workflow())], stop=stop,
                  tracker_factory=lambda cfg, env, started: FakeTracker(),
                  worker_factory=FakeWorkerFactory(), notifier=FakeNotifier())
    assert rc == 0 and status()["running"] == []


# -- check ------------------------------------------------------------------------------------

class ViewerTracker(FakeTracker):
    def viewer_id(self):
        return "user-123"


def socket_workflow(write_workflow, tmp_path):
    sock = tmp_path / "codex.sock"
    sock.write_text("")
    return write_workflow(MINIMAL_WORKFLOW.replace("workspace:", f"codex:\n  socket: {sock}\n"
                                                                 "workspace:"))


def test_check_ok(write_workflow, tmp_path, capsys):
    path = socket_workflow(write_workflow, tmp_path)
    rc = cli.main(["check", str(path)], tracker_factory=lambda c, e, s: ViewerTracker())
    out = capsys.readouterr().out
    assert rc == 0 and "linear viewer user-123" in out


def test_check_without_login_says_run_symphony_login(write_workflow, tmp_path, capsys):
    rc = cli.main(["check", str(socket_workflow(write_workflow, tmp_path))])
    out = capsys.readouterr().out
    assert rc == 1
    assert "tracker_auth" in out and "symphony login" in out


def test_check_fails_when_daemon_socket_missing(write_workflow, tmp_path, capsys):
    path = write_workflow(MINIMAL_WORKFLOW.replace(
        "workspace:", f"codex:\n  socket: {tmp_path / 'absent.sock'}\nworkspace:"))
    rc = cli.main(["check", str(path)], tracker_factory=lambda c, e, s: ViewerTracker())
    assert rc == 1 and "codex daemon socket missing" in capsys.readouterr().out


def test_check_missing_file_exits_2(tmp_path):
    assert cli.main(["check", str(tmp_path / "x.md")]) == 2


# -- ls ---------------------------------------------------------------------------------------

def write_status(**over):
    st = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "pid": __import__("os").getpid(),
        "workflow": "/nonexistent/WORKFLOW.md", "poll_interval_ms": 30000,
        "running": [{"issue_identifier": "TAL-1", "state": "In Progress",
                     "thread_id": "thr-1", "retry_attempt": None, "parking": False,
                     "last_event": "turn_completed",
                     "tokens": {"total_tokens": 1234}}],
        "retrying": [{"issue_identifier": "TAL-2", "attempt": 2, "due_in_ms": 9000,
                      "error": "turn_failed: boom"}],
        "codex_totals": {"total_tokens": 5000, "seconds_running": 12.5},
        "error": None,
    }
    st.update(over)
    p = state_dir() / "status.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(st))
    return st


def test_ls_without_status_reports_unavailable(capsys):
    assert cli.main(["ls"]) == 1
    assert "is the service running?" in capsys.readouterr().out


def test_ls_shows_running_and_retrying(capsys):
    write_status()
    assert cli.main(["ls"]) == 0
    out = capsys.readouterr().out
    assert "STALE" not in out
    row1 = next(line for line in out.splitlines() if line.startswith("TAL-1"))
    for part in ("In Progress", "running", "thr-1", "1234", "turn_completed"):
        assert part in row1
    row2 = next(line for line in out.splitlines() if line.startswith("TAL-2"))
    assert "retry 2 in 9s" in row2 and "turn_failed: boom" in row2
    assert "tokens total 5000" in out


def test_ls_flags_stale_status(capsys):
    old = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    write_status(generated_at=old, running=[], retrying=[])
    assert cli.main(["ls"]) == 0
    out = capsys.readouterr().out
    assert "STALE" in out and "no tickets claimed" in out


def test_ls_reads_thread_for_retrying_ticket(write_workflow, tmp_path, capsys):
    path = write_workflow()
    ThreadStore(tmp_path / "workspaces").write(
        "TAL-2", ThreadRecord(thread_id="thr-2", created_at=datetime.now(timezone.utc),
                              total_tokens=77))
    write_status(workflow=str(path))
    cli.main(["ls"])
    row2 = next(line for line in capsys.readouterr().out.splitlines()
                if line.startswith("TAL-2"))
    assert "thr-2" in row2 and "77" in row2


# -- open and Rex tabs ------------------------------------------------------------------------

class FakeRex:
    def __init__(self, sessions=(), inspect="", apps=("app-1",)):
        self.calls = []
        self.sessions = list(sessions)
        self.inspect = inspect
        self.apps = apps

    def __call__(self, *args):
        self.calls.append(args)
        if args[:2] == ("ls", "--json"):
            return {"sessions": self.sessions}
        if args[:2] == ("session", "inspect"):
            return {"windows": [self.inspect]}
        if args[0] == "new":
            return {"session_id": "s-1", "windows": [{"window_id": "w-1"}]}
        if args[:2] == ("window", "new"):
            return {"window_id": "w-2"}
        if args[:2] == ("client", "ls"):
            return {"clients": [{"client_id": a, "info": {"kind": "app"},
                                 "connection_state": "connected"} for a in self.apps]}
        return ""


def test_open_creates_hidden_tab_and_shows_it(write_workflow, tmp_path, capsys):
    path = write_workflow()
    threads = ThreadStore(tmp_path / "workspaces")
    threads.write("TAL-5", ThreadRecord(thread_id="thr-5", created_at=datetime.now(timezone.utc)))
    fake = FakeRex()
    assert cli.main(["open", "tal-5", "--workflow", str(path)], rex=fake) == 0
    new = next(c for c in fake.calls if c[0] == "new")
    assert new[:4] == ("new", "symphony", "--window", "TAL-5")
    assert new[-3:] == ("codex", "resume", "thr-5")
    assert ("-C", "app-1", "do", "session.select", "session_id=s-1",
            "window_id=w-1") in fake.calls
    saved = json.loads((threads.dir_for("TAL-5") / "rex.json").read_text())
    assert saved == {"session_id": "s-1", "window_id": "w-1", "thread_id": "thr-5"}


def test_open_uses_status_workflow_by_default(write_workflow, tmp_path, monkeypatch):
    path = write_workflow()
    write_status(workflow=str(path))
    monkeypatch.chdir(state_dir())  # no WORKFLOW.md here
    ThreadStore(tmp_path / "workspaces").write(
        "TAL-5", ThreadRecord(thread_id="thr-5", created_at=datetime.now(timezone.utc)))
    assert cli.main(["open", "TAL-5"], rex=FakeRex()) == 0


def test_open_without_thread_fails(write_workflow, capsys):
    assert cli.main(["open", "TAL-9", "--workflow", str(write_workflow())], rex=FakeRex()) == 1
    assert "no Codex thread recorded for TAL-9" in capsys.readouterr().err


def test_open_without_connected_app_fails(write_workflow, tmp_path, capsys):
    path = write_workflow()
    ThreadStore(tmp_path / "workspaces").write(
        "TAL-5", ThreadRecord(thread_id="thr-5", created_at=datetime.now(timezone.utc)))
    assert cli.main(["open", "TAL-5", "--workflow", str(path)], rex=FakeRex(apps=())) == 1
    assert "No Rex app is connected" in capsys.readouterr().err


def test_ensure_tab_reuses_live_tab(tmp_path):
    (tmp_path / "rex.json").write_text(json.dumps(
        {"session_id": "s-1", "window_id": "w-1", "thread_id": "thr"}))
    fake = FakeRex(sessions=[{"label": "symphony", "session_id": "s-1"}], inspect="w-1")
    rex.ensure_tab("TAL-1", tmp_path, "thr", session_label="symphony", store_dir=tmp_path,
                   rex=fake)
    assert not any(c[0] in ("new", "window") for c in fake.calls)


def test_ensure_tab_adds_window_for_new_thread(tmp_path):
    (tmp_path / "rex.json").write_text(json.dumps(
        {"session_id": "s-1", "window_id": "w-1", "thread_id": "old"}))
    fake = FakeRex(sessions=[{"label": "symphony", "session_id": "s-1"}], inspect="w-1")
    rex.ensure_tab("TAL-1", tmp_path, "new-thr", session_label="symphony",
                   store_dir=tmp_path, rex=fake)
    win = next(c for c in fake.calls if c[0] == "window")
    assert win[:6] == ("window", "new", "-s", "s-1", "TAL-1", "--focus=false")
    assert json.loads((tmp_path / "rex.json").read_text())["window_id"] == "w-2"


# -- logs -------------------------------------------------------------------------------------

def test_logs_filters_by_ticket(capsys):
    p = state_dir() / "symphony.jsonl"
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = [{"event": "dispatch", "issue_identifier": "TAL-1", "n": i} for i in range(3)]
    lines.insert(1, {"event": "dispatch", "issue_identifier": "TAL-2"})
    p.write_text("\n".join(json.dumps(x) for x in lines) + "\nnot json\n")
    assert cli.main(["logs", "tal-1", "-n", "2"]) == 0
    out = [json.loads(x) for x in capsys.readouterr().out.splitlines()]
    assert [x["n"] for x in out] == [1, 2]


def test_logs_without_file_fails():
    assert cli.main(["logs", "TAL-1"]) == 1


# -- shipped files ----------------------------------------------------------------------------

def test_real_workflow_loads_validates_and_renders(monkeypatch):
    defn, cfg = WorkflowStore(PKG / "WORKFLOW.md").load_initial()
    validate_dispatch_config(cfg, {})
    assert cfg.tracker.provider["team_key"] == "TAL"
    assert cfg.tracker.provider["assignee"] == "me"
    assert cfg.tracker.required_labels == ("agent",)
    assert cfg.tracker.active_states == ("Todo", "In Progress", "In Review")
    assert cfg.tracker.provider["waiting_label"] == "waiting"
    assert cfg.poll_interval_ms == 60000
    assert cfg.tracker.terminal_states == ("Done", "Canceled", "Duplicate")
    assert cfg.workspace_root == Path("~/symphony-workspaces").expanduser()
    assert cfg.agent.max_concurrent_agents == 3 and cfg.agent.max_turns == 20
    assert cfg.codex.transport == "daemon" and cfg.hooks.before_remove
    assert "--force" not in cfg.hooks.before_remove
    issue = make_issue("TAL-42", url="https://linear.app/x/TAL-42", labels=("agent", "repo:x"))
    text = render_prompt(defn.prompt_template, issue, None, turn_number=1, max_turns=20)
    for part in ("🤖 agent (Codex, TAL-42)", "--provider linear --issue TAL-42",
                 "claude -p --permission-mode plan", "In Review", "`waiting` label",
                 "AWS or Baseten", "Never merge, approve, or push to main",
                 "Do not publish claude.ai artifacts", "https://linear.app/x/TAL-42"):
        assert part in text


def test_launchd_plist_runs_the_service():
    with open(PKG / "local.symphony.plist", "rb") as f:
        pl = plistlib.load(f)
    assert pl["Label"] == "local.symphony" and pl["KeepAlive"] is True
    args = pl["ProgramArguments"]
    assert args[0].endswith("/uv") and args[1] == "run"
    assert args[args.index("symphony") + 1] == "run"
    assert args[-1].endswith("/WORKFLOW.md")
    assert "/.local/state/symphony/" in pl["StandardErrorPath"]


def test_second_service_instance_refused(write_workflow, capsys):
    import fcntl
    lock_file = state_dir() / "symphony.lock"
    lock_file.parent.mkdir(parents=True, exist_ok=True)
    with open(lock_file, "a") as held:
        fcntl.flock(held, fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert run_cli([str(write_workflow())]) == 1
    assert "another symphony service is running" in capsys.readouterr().err
    assert run_cli([str(write_workflow())]) == 0  # lock released


def _real_hook_manager(root):
    from symphony.workspace import WorkspaceManager
    defn, cfg = WorkflowStore(PKG / "WORKFLOW.md").load_initial()
    return WorkspaceManager(root, cfg.hooks, repos={}, strip_env=[])


def test_real_before_remove_archives_plain_workspace_with_content(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    root = tmp_path / "ws"
    (root / "TAL-1").mkdir(parents=True)
    (root / "TAL-1" / "report.md").write_text("result")
    (root / "TAL-2").mkdir()
    m = _real_hook_manager(root)
    assert m.remove("TAL-1") and m.remove("TAL-2")
    assert not (root / "TAL-1").exists() and not (root / "TAL-2").exists()
    archived = list((root / ".archive").iterdir())
    assert [p.name.startswith("TAL-1-") for p in archived] == [True]
    assert (archived[0] / "report.md").read_text() == "result"


# -- login and bridge-event -------------------------------------------------------------------

def test_login_prints_tools_never_tokens(capsys):
    calls = []

    def fake_login(*, port):
        calls.append(port)
        return ["save_comment", "list_issues"]

    assert cli.main(["login"], login=fake_login) == 0
    out = capsys.readouterr().out
    assert calls == [47615] and "symphony-linear-mcp" in out and "list_issues" in out
    assert cli.main(["login", "--port", "5000"], login=fake_login) == 0 and calls[-1] == 5000


def test_login_failure_exits_1_without_details(capsys):
    def broken(*, port):
        raise RuntimeError("secret-token-in-message")

    assert cli.main(["login"], login=broken) == 1
    err = capsys.readouterr().err
    assert "login failed: RuntimeError" in err and "secret-token" not in err


def bridge(board, issue, event, capsys):
    import io
    rc = cli.main(["bridge-event", "--issue", issue, "--json", "-"], tools=board.mcp,
                  stdin=io.StringIO(json.dumps(event) if not isinstance(event, str) else event))
    return rc, json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def test_bridge_event_cli_is_idempotent_by_event_id(capsys):
    from fakes import FakeLinearBoard

    board = FakeLinearBoard()
    board.add("TAL-5", state="In Progress", labels=("agent", "waiting"))
    event = {"run": "reg-1", "status": "**pilot / one**: completed",
             "event_id": "ev-1", "event": "🤖 experiment-run\ncompleted\n\nEvent: ev-1"}
    rc, out = bridge(board, "tal-5", event, capsys)
    assert rc == 0 and out["status"] == "created" and out["event"] == "posted"
    rc, out = bridge(board, "TAL-5", event, capsys)
    assert rc == 0 and out["status"] == "unchanged" and out["event"] == "exists"
    assert len(board.comments["TAL-5"]) == 2
    assert board.mcp.called("save_issue") == []


def test_bridge_event_cli_exit_codes(capsys):
    from fakes import FakeLinearBoard, FakeMcp
    from symphony.errors import TrackerError

    rc, out = bridge(FakeLinearBoard(), "TAL-5", "not json", capsys)
    assert rc == 2 and out == {"error": "bad_event"}
    rc, out = bridge(FakeLinearBoard(), "TAL-5", {"event": "x"}, capsys)
    assert rc == 2
    no_login = FakeMcp().on("list_comments", TrackerError(
        "tracker_auth", "Linear MCP login required: run `symphony login`"))
    rc, out = bridge(type("B", (), {"mcp": no_login})(), "TAL-5", {"event_id": "e",
                                                                      "event": "x"}, capsys)
    assert rc == 3 and out == {"error": "tracker_auth"} and no_login.called("save_comment") == []
    flaky = FakeMcp().on("list_comments", TrackerError("tracker_request", "reset"))
    rc, out = bridge(type("B", (), {"mcp": flaky})(), "TAL-5", {"event_id": "e", "event": "x"},
                     capsys)
    assert rc == 1 and out == {"error": "tracker_request"}


def test_bridge_event_cli_without_login_touches_no_network(capsys):
    import io
    rc = cli.main(["bridge-event", "--issue", "TAL-5"],
                  stdin=io.StringIO(json.dumps({"event_id": "e", "event": "x"})))
    assert rc == 3
    assert json.loads(capsys.readouterr().out) == {"error": "tracker_auth"}

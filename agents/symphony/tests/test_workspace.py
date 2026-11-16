"""§9 / §17.2 workspace manager, hooks and thread record store."""

from __future__ import annotations

import os
import stat
from datetime import datetime, timezone
from pathlib import Path

import pytest

import symphony.workspace as ws_mod
from fakes import make_issue
from symphony.config import HooksConfig
from symphony.errors import HookError, TrackerError, WorkspaceError
from symphony.models import ThreadRecord
from symphony.workspace import (HOOK_OUTPUT_LOG_LIMIT, ThreadStore, WorkspaceManager,
                                cleanup_terminal_workspaces, workspace_key)


@pytest.fixture(autouse=True)
def logs(monkeypatch, tmp_path):
    """Capture log() calls; keep bash -l from reading the real profile."""
    records = []
    monkeypatch.setattr(ws_mod, "log", lambda event, level="info", **f: records.append(
        dict(event=event, level=level, **f)))
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return records


def events(logs):
    return [r["event"] for r in logs]


@pytest.fixture
def root(tmp_path):
    r = tmp_path / "ws"
    r.mkdir()
    return r


def manager(root, **hooks):
    timeout_ms = hooks.pop("timeout_ms", 5000)
    return WorkspaceManager(root, HooksConfig(timeout_ms=timeout_ms, **hooks),
                            repos={}, strip_env=())


# --- keys and paths -----------------------------------------------------------------

def test_key_unchanged_identifier_is_kept():
    assert workspace_key("TAL-123") == "TAL-123"
    assert workspace_key("a.b_c-D9") == "a.b_c-D9"


def test_key_sanitized_gets_hash_suffix():
    key = workspace_key("TAL/12 x")
    assert key.startswith("TAL_12_x-")
    suffix = key.rsplit("-", 1)[1]
    assert len(suffix) == 16 and all(c in "0123456789abcdef" for c in suffix)
    assert all(c.isalnum() or c in "._-" for c in key)


def test_key_collisions_differ():
    a, b = workspace_key("TAL/1"), workspace_key("TAL:1")
    assert a != b
    assert a.startswith("TAL_1-") and b.startswith("TAL_1-")
    assert workspace_key("TAL_1") == "TAL_1"
    assert workspace_key("TAL_1") not in (a, b)


@pytest.mark.parametrize("ident", [".", "..", ".hidden", "../etc", ""])
def test_key_dot_names_are_never_bare(ident):
    key = workspace_key(ident)
    assert key not in (".", "..", "")
    assert not key.startswith(".") or "-" in key
    assert "/" not in key


def test_key_deterministic():
    assert workspace_key("TAL 9") == workspace_key("TAL 9")


def test_path_for_is_deterministic_and_inside_root(root):
    m = manager(root)
    p = m.path_for("TAL-1")
    assert p == root.resolve() / "TAL-1"
    assert m.path_for("TAL-1") == p
    assert m.path_for("../escape").parent == root.resolve()


# --- create / reuse ------------------------------------------------------------------

def test_ensure_creates_missing_then_reuses(root):
    m = manager(root)
    issue = make_issue("TAL-1")
    w1 = m.ensure(issue)
    assert w1.created_now and w1.path.is_dir() and w1.workspace_key == "TAL-1"
    (w1.path / "keep.txt").write_text("x")
    w2 = m.ensure(issue)
    assert not w2.created_now and w2.path == w1.path
    assert (w2.path / "keep.txt").read_text() == "x"


def test_ensure_creates_missing_root(tmp_path):
    m = manager(tmp_path / "a" / "b")
    assert m.ensure(make_issue("TAL-1")).path.is_dir()


def test_ensure_existing_file_fails(root):
    (root / "TAL-1").write_text("not a dir")
    with pytest.raises(WorkspaceError) as e:
        manager(root).ensure(make_issue("TAL-1"))
    assert e.value.category == "workspace_not_directory"
    assert (root / "TAL-1").read_text() == "not a dir"


def test_ensure_symlink_outside_root_rejected(root, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (root / "TAL-1").symlink_to(outside)
    with pytest.raises(WorkspaceError) as e:
        manager(root).ensure(make_issue("TAL-1"))
    assert e.value.category == "workspace_path_outside_root"


def test_after_create_runs_only_on_new_workspace(root):
    m = manager(root, after_create="echo ran >> created.log")
    issue = make_issue("TAL-1")
    w = m.ensure(issue)
    m.ensure(issue)
    assert (w.path / "created.log").read_text() == "ran\n"


def test_after_create_failure_removes_new_dir_and_raises(root, logs):
    m = manager(root, after_create="echo boom; exit 3")
    with pytest.raises(HookError) as e:
        m.ensure(make_issue("TAL-1"))
    assert e.value.category == "hook_failed" and e.value.exit_code == 3
    assert e.value.hook == "after_create"
    assert not (root / "TAL-1").exists()
    failed = [r for r in logs if r["event"] == "hook_failed"][0]
    assert "boom" in failed["output"]


def test_after_create_timeout_is_fatal(root):
    m = manager(root, after_create="sleep 5", timeout_ms=200)
    with pytest.raises(HookError) as e:
        m.ensure(make_issue("TAL-1"))
    assert e.value.category == "hook_timeout"
    assert not (root / "TAL-1").exists()


# --- hooks ---------------------------------------------------------------------------

def test_hooks_run_in_workspace_with_bash_login_shell(root):
    m = manager(root, before_run='echo "$PWD" > pwd.txt; shopt -q login_shell && echo y > l.txt')
    issue = make_issue("TAL-1")
    w = m.ensure(issue)
    m.run_hook("before_run", issue, w.path)
    assert Path((w.path / "pwd.txt").read_text().strip()).resolve() == w.path
    assert (w.path / "l.txt").read_text().strip() == "y"


def test_missing_hook_is_noop(root, logs):
    m = manager(root)
    w = m.ensure(make_issue("TAL-1"))
    m.run_hook("before_run", make_issue("TAL-1"), w.path)
    assert "hook_started" not in events(logs)


def test_before_run_failure_and_timeout_raise(root):
    issue = make_issue("TAL-1")
    m = manager(root, before_run="exit 1")
    w = m.ensure(issue)
    with pytest.raises(HookError) as e:
        m.run_hook("before_run", issue, w.path)
    assert e.value.category == "hook_failed"
    m = manager(root, before_run="sleep 5", timeout_ms=200)
    with pytest.raises(HookError) as e:
        m.run_hook("before_run", issue, w.path)
    assert e.value.category == "hook_timeout"


def test_after_run_failure_and_timeout_ignored(root, logs):
    issue = make_issue("TAL-1")
    m = manager(root, after_run="exit 7")
    w = m.ensure(issue)
    assert m.run_hook_best_effort("after_run", issue, w.path) is False
    m = manager(root, after_run="sleep 5", timeout_ms=200)
    assert m.run_hook_best_effort("after_run", issue, w.path) is False
    assert events(logs).count("hook_ignored") == 2
    m = manager(root, after_run="true")
    assert m.run_hook_best_effort("after_run", issue, w.path) is True


def test_default_hook_timeout_is_60s():
    assert HooksConfig().timeout_ms == 60000


def test_hook_output_truncated_in_logs(root, logs):
    issue = make_issue("TAL-1")
    m = manager(root, before_run="head -c 10000 /dev/zero | tr '\\0' a; echo END")
    w = m.ensure(issue)
    m.run_hook("before_run", issue, w.path)
    done = [r for r in logs if r["event"] == "hook_completed"][0]
    assert len(done["output"]) <= HOOK_OUTPUT_LOG_LIMIT + 3
    assert done["output"].rstrip().endswith("END")


def test_hook_env(root, monkeypatch):
    monkeypatch.setenv("SECRET_KEY", "s3cret")
    monkeypatch.setenv("KEEP_ME", "1")
    repo = root.parent / "repo"
    m = WorkspaceManager(root, HooksConfig(before_run="env > env.txt"),
                         repos={"Dotfiles": repo}, strip_env=["SECRET_KEY"])
    issue = make_issue("TAL-5", labels=("agent", "repo:dotfiles"))
    w = m.ensure(issue)
    m.run_hook("before_run", issue, w.path)
    env = dict(line.split("=", 1) for line in (w.path / "env.txt").read_text().splitlines()
               if "=" in line)
    assert env["SYMPHONY_ISSUE_IDENTIFIER"] == "TAL-5"
    assert env["SYMPHONY_ISSUE_ID"] == "id-TAL-5"
    assert env["SYMPHONY_ISSUE_LABELS"] == "agent,repo:dotfiles"
    assert env["SYMPHONY_REPO"] == str(repo)
    assert env["SYMPHONY_WORKSPACE"] == str(w.path)
    assert env["KEEP_ME"] == "1"
    assert "SECRET_KEY" not in env


def test_hook_env_repo_empty_without_matching_label(root):
    m = WorkspaceManager(root, HooksConfig(), repos={"a": Path("/x")}, strip_env=())
    assert m.hook_env(make_issue("T-1"), root)["SYMPHONY_REPO"] == ""
    assert m.hook_env(make_issue("T-1", labels=("repo:b",)), root)["SYMPHONY_REPO"] == ""


# --- remove (deviation 2) ------------------------------------------------------------

def test_remove_runs_before_remove_and_deletes(root):
    marker = root.parent / "removed.txt"
    m = manager(root, before_remove=f'echo "$SYMPHONY_ISSUE_IDENTIFIER" > {marker}')
    issue = make_issue("TAL-1")
    w = m.ensure(issue)
    m.threads.write("TAL-1", ThreadRecord("th", datetime.now(timezone.utc)))
    assert m.remove(issue) is True
    assert not w.path.exists()
    assert marker.read_text().strip() == "TAL-1"
    assert m.threads.read("TAL-1") is None


def test_remove_when_before_remove_deletes_the_directory(root):
    # `git worktree remove` deletes the workspace itself before Symphony's rmtree.
    m = manager(root, before_remove='cd .. && rmdir "$SYMPHONY_WORKSPACE"')
    issue = make_issue("TAL-1")
    w = m.ensure(issue)
    assert m.remove(issue) is True
    assert not w.path.exists()


def test_remove_skips_delete_when_before_remove_fails(root, logs):
    m = manager(root, before_remove="exit 1")
    issue = make_issue("TAL-1")
    w = m.ensure(issue)
    m.threads.write("TAL-1", ThreadRecord("th", datetime.now(timezone.utc)))
    assert m.remove(issue) is False
    assert w.path.is_dir()
    assert m.threads.read("TAL-1") is not None
    assert "workspace_remove_skipped" in events(logs)


def test_remove_skips_delete_on_before_remove_timeout(root):
    m = manager(root, before_remove="sleep 5", timeout_ms=200)
    w = m.ensure(make_issue("TAL-1"))
    assert m.remove("TAL-1") is False
    assert w.path.is_dir()


def test_remove_by_identifier_and_missing_dir(root):
    m = manager(root, before_remove="exit 1")
    assert m.remove("TAL-404") is True  # nothing there, hook not run
    m = manager(root)
    m.ensure(make_issue("TAL-2"))
    assert m.remove("TAL-2") is True
    assert not (root / "TAL-2").exists()


# --- agent cwd invariants ------------------------------------------------------------

def test_assert_agent_cwd(root, tmp_path):
    m = manager(root)
    w = m.ensure(make_issue("TAL-1"))
    m.assert_agent_cwd(w.path, w.path)
    with pytest.raises(WorkspaceError) as e:
        m.assert_agent_cwd(w.path, root)
    assert e.value.category == "invalid_workspace_cwd"
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    with pytest.raises(WorkspaceError) as e:
        m.assert_agent_cwd(outside, outside)
    assert e.value.category == "workspace_path_outside_root"
    with pytest.raises(WorkspaceError) as e:
        m.assert_agent_cwd(root, root)
    assert e.value.category == "workspace_path_outside_root"
    with pytest.raises(WorkspaceError):
        m.assert_agent_cwd(w.path / ".." / "..", w.path / ".." / "..")


# --- startup terminal cleanup --------------------------------------------------------

class _Tracker:
    def __init__(self, issues=None, error=None):
        self.issues, self.error, self.asked = issues or [], error, None

    def fetch_issues_by_states(self, states):
        self.asked = states
        if self.error:
            raise self.error
        return self.issues


def test_cleanup_terminal_workspaces(root):
    m = manager(root, before_remove='[ "$SYMPHONY_ISSUE_IDENTIFIER" != TAL-2 ]')
    a, b = make_issue("TAL-1", state="Done"), make_issue("TAL-2", state="Done")
    m.ensure(a)
    m.ensure(b)
    t = _Tracker([a, b, make_issue("TAL-3", state="Canceled")])
    skipped = cleanup_terminal_workspaces(m, t, ["Done", "Canceled"])
    assert t.asked == ["Done", "Canceled"]
    assert [i.identifier for i in skipped] == ["TAL-2"]
    assert not (root / "TAL-1").exists() and (root / "TAL-2").is_dir()


def test_cleanup_tracker_failure_logged_and_continues(root, logs):
    m = manager(root)
    t = _Tracker(error=TrackerError("tracker_request", "down"))
    assert cleanup_terminal_workspaces(m, t, ["Done"]) == []
    assert "startup_cleanup_failed" in events(logs)


# --- thread record store -------------------------------------------------------------

def test_thread_store_roundtrip_and_permissions(root):
    s = ThreadStore(root)
    t0 = datetime(2026, 1, 2, 3, 4, 5, tzinfo=timezone.utc)
    assert s.read("TAL-1") is None
    assert s.turn_started_at("TAL-1") is None
    rec = ThreadRecord("thread-1", t0, last_turn_id="turn-2", last_turn_started_at=t0,
                       total_tokens=1234)
    s.write("TAL-1", rec)
    path = root / ".symphony" / "TAL-1" / "thread.json"
    assert s.dir_for("TAL-1") == root / ".symphony" / "TAL-1"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o600
    assert s.read("TAL-1") == rec
    assert s.turn_started_at("TAL-1") == t0
    rec.total_tokens = 99
    s.write("TAL-1", rec)
    assert s.read("TAL-1").total_tokens == 99
    assert [p.name for p in s.dir_for("TAL-1").iterdir()] == ["thread.json"]
    s.delete("TAL-1")
    assert s.read("TAL-1") is None
    s.delete("TAL-1")


def test_thread_store_corrupt_file_returns_none(root, logs):
    s = ThreadStore(root)
    s.dir_for("TAL-1").mkdir(parents=True)
    (s.dir_for("TAL-1") / "thread.json").write_text("{not json")
    assert s.read("TAL-1") is None
    assert "thread_record_corrupt" in events(logs)


def test_thread_store_dir_never_collides_with_workspace(root):
    m = manager(root)
    w = m.ensure(make_issue(".symphony"))
    assert w.path != root.resolve() / ".symphony"


@pytest.mark.parametrize("exit_code", [0, 1])
def test_archive_marker_requires_success_and_survives_log_truncation(root, exit_code):
    archived = []
    hook = ("echo SYMPHONY_WORKSPACE_ARCHIVED=/archive/report; "
            "echo SYMPHONY_WORKSPACE_ARCHIVED=/archive/duplicate; "
            "head -c 10000 /dev/zero | tr '\\0' a; "
            f"exit {exit_code}")
    m = WorkspaceManager(root, HooksConfig(before_remove=hook), repos={}, strip_env=(),
                         on_archive=lambda issue, path: archived.append((issue.id, path)))
    issue = make_issue("TAL-1")
    m.ensure(issue)
    assert m.remove(issue) is (exit_code == 0)
    assert archived == ([(issue.id, "/archive/report")] if exit_code == 0 else [])

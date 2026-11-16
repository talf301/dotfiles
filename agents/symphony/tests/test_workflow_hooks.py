"""The real WORKFLOW.md hooks against throwaway git repos: worktree on create, and on removal
a push of the ticket branch only when the ticket is Done."""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from fakes import make_issue
from symphony.workflow import WorkflowStore
from symphony.workspace import WorkspaceManager

PKG = Path(__file__).resolve().parents[1]


def git(cwd, *args) -> str:
    return subprocess.run(["git", "-C", str(cwd), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


@pytest.fixture
def setup(tmp_path):
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "-q", "--bare", "-b", "master", str(origin)], check=True)
    repo = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "master", str(repo)], check=True)
    for k, v in (("user.name", "t"), ("user.email", "t@example.invalid")):
        git(repo, "config", k, v)
    (repo / "a").write_text("a")
    git(repo, "add", "a")
    git(repo, "commit", "-qm", "init")
    git(repo, "remote", "add", "origin", str(origin))
    git(repo, "push", "-q", "origin", "master")
    _, cfg = WorkflowStore(PKG / "WORKFLOW.md").load_initial()
    notes = []
    m = WorkspaceManager(tmp_path / "ws", cfg.hooks, repos={"x": repo}, strip_env=(),
                         on_note=lambda issue, text: notes.append(text))
    return m, repo, origin, notes


def _work(m, repo, ident):
    w = m.ensure(make_issue(ident, labels=("agent", "repo:x")))
    (w.path / "b").write_text("b")
    git(w.path, "add", "b")
    git(w.path, "-c", "user.name=t", "-c", "user.email=t@example.invalid", "commit", "-qm", "work")
    return w


def test_done_pushes_ticket_branch_and_notes(setup):
    m, repo, origin, notes = setup
    w = _work(m, repo, "TAL-7")
    head = git(w.path, "rev-parse", "HEAD")
    assert m.remove(make_issue("TAL-7", state="Done", labels=("agent", "repo:x")))
    assert git(origin, "rev-parse", "refs/heads/u/tfriedman/tal-7") == head
    assert len(notes) == 1 and notes[0].startswith("Pushed branch `u/tfriedman/tal-7` to origin at `")
    assert head.startswith(notes[0].split("`")[3])
    assert not w.path.exists()


@pytest.mark.parametrize("state", ["Canceled", "Duplicate"])
def test_other_terminal_states_do_not_push(setup, state):
    m, repo, origin, notes = setup
    _work(m, repo, "TAL-8")
    assert m.remove(make_issue("TAL-8", state=state, labels=("agent", "repo:x")))
    assert "u/tfriedman/tal-8" not in git(origin, "branch", "--list")
    assert notes == []


def test_done_without_new_commits_does_not_push(setup):
    m, repo, origin, notes = setup
    m.ensure(make_issue("TAL-9", labels=("agent", "repo:x")))
    assert m.remove(make_issue("TAL-9", state="Done", labels=("agent", "repo:x")))
    assert "u/tfriedman/tal-9" not in git(origin, "branch", "--list")
    assert notes == []


def test_failed_push_keeps_worktree(setup):
    m, repo, origin, notes = setup
    w = _work(m, repo, "TAL-10")
    git(repo, "remote", "set-url", "origin", str(origin.parent / "missing.git"))
    assert m.remove(make_issue("TAL-10", state="Done", labels=("agent", "repo:x"))) is False
    assert w.path.exists() and notes == []

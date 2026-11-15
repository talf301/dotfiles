"""Shared fixtures. Tests never touch the network, real Linear, the Codex daemon, launchd or Rex."""

from __future__ import annotations

import pytest

from fakes import (
    FakeAppServer,
    FakeClock,
    FakeMcp,
    FakeNotifier,
    FakeTracker,
    FakeWorkerFactory,
)

MINIMAL_WORKFLOW = """\
---
tracker:
  kind: linear
  provider:
    team_key: TAL
  required_labels: [agent]
workspace:
  root: ./workspaces
---
Work on {{ issue.identifier }}: {{ issue.title }}.
"""


@pytest.fixture(autouse=True)
def isolate_env(monkeypatch, tmp_path):
    """Keep state, logs and secrets inside tmp_path for every test."""
    monkeypatch.setenv("SYMPHONY_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.delenv("LINEAR_API_KEY", raising=False)


@pytest.fixture
def write_workflow(tmp_path):
    """write_workflow(text=MINIMAL_WORKFLOW, name="WORKFLOW.md") -> Path"""

    def _write(text: str = MINIMAL_WORKFLOW, name: str = "WORKFLOW.md") -> Path:
        p = tmp_path / name
        p.write_text(text)
        return p

    return _write


@pytest.fixture
def clock():
    return FakeClock()


@pytest.fixture
def tracker():
    return FakeTracker()


@pytest.fixture
def mcp():
    return FakeMcp()


@pytest.fixture
def app_server():
    return FakeAppServer()


@pytest.fixture
def workers():
    return FakeWorkerFactory()


@pytest.fixture
def notifier():
    return FakeNotifier()

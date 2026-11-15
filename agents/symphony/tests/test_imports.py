"""Skeleton smoke test: every module imports and the shared fakes behave."""

import importlib

import pytest

from fakes import FakeAppServer, make_issue

MODULES = ["errors", "models", "workflow", "config", "tracker", "linear", "mcp_linear", "workspace",
           "appserver", "runner", "orchestrator", "notify", "rex", "log", "cli"]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name):
    importlib.import_module(f"symphony.{name}")


def test_issue_to_template_keeps_lists():
    d = make_issue("TAL-9", labels=("agent", "repo:x")).to_template()
    assert d["identifier"] == "TAL-9" and d["labels"] == ["agent", "repo:x"]


def test_fake_app_server_scripts_a_turn():
    s = FakeAppServer().script_turn("th", "tu")
    s.send({"jsonrpc": "2.0", "id": 1, "method": "turn/start", "params": {}})
    got = [s.recv(0) for _ in range(3)]
    assert got[0]["result"]["turn"]["id"] == "tu"
    assert [m["method"] for m in got[1:3]] == ["turn/started", "turn/completed"]

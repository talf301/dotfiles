"""§17.1 typed config, defaults, $VAR/~ resolution and dispatch preflight (config.py)."""

import tempfile
from pathlib import Path

import pytest

from symphony.config import (
    build_config,
    resolve_path,
    state_key,
    validate_dispatch_config,
)
from symphony.errors import ConfigError
from symphony.workflow import WorkflowDefinition


def cfg_of(fm: dict, tmp_path: Path, env: dict | None = None):
    defn = WorkflowDefinition(config=fm, prompt_template="", path=tmp_path / "WORKFLOW.md")
    return build_config(defn, env or {})


LINEAR = {"tracker": {"kind": "linear", "provider": {"team_key": "TAL"}}}


@pytest.fixture
def fake_adapter(monkeypatch):
    """Stand-in for linear.validate_provider_config; records calls."""
    calls = []

    def validate(provider, env):
        calls.append((provider, env))
        if not str(provider.get("team_key") or "").strip():
            raise ConfigError("invalid_tracker_config", "team_key is required")
    monkeypatch.setattr("symphony.linear.validate_provider_config", validate)
    return calls


# --- defaults ---------------------------------------------------------------------------------

def test_defaults_apply(tmp_path):
    c = cfg_of({}, tmp_path)
    assert c.poll_interval_ms == 30000
    assert c.workspace_root == Path(tempfile.gettempdir()) / "symphony_workspaces"
    assert c.workflow_path == tmp_path / "WORKFLOW.md"
    assert c.tracker.kind is None and c.tracker.provider == {}
    assert c.tracker.required_labels == ()
    assert c.hooks.after_create is None and c.hooks.before_remove is None
    assert c.hooks.timeout_ms == 60000
    assert c.agent.max_concurrent_agents == 3
    assert c.agent.max_turns == 20
    assert c.agent.max_retry_backoff_ms == 300000
    assert c.agent.max_concurrent_agents_by_state == {}
    assert c.agent.continuation_prompt is None
    assert c.codex.command == "codex app-server"
    assert c.codex.transport == "daemon"
    assert c.codex.socket == Path("~/.codex/app-server-control/app-server-control.sock").expanduser()
    assert c.codex.approval_policy == "on-request"
    assert c.codex.approvals_reviewer == "auto_review"
    assert c.codex.thread_sandbox == "workspace-write"
    assert c.codex.turn_sandbox_policy is None
    assert (c.codex.turn_timeout_ms, c.codex.read_timeout_ms, c.codex.stall_timeout_ms) == (
        3600000, 5000, 300000)
    assert c.ext.repos == {} and c.ext.token_cap == 3_000_000
    assert c.ext.notify_states == ("In Review", "Done")
    assert c.ext.notify_failure_attempts == 3 and c.ext.notify_enabled is True
    assert c.ext.rex_enabled is True and c.ext.rex_session == "symphony"


def test_null_sections_use_defaults(tmp_path):
    c = cfg_of({"polling": None, "hooks": None, "agent": None, "codex": None}, tmp_path)
    assert c.poll_interval_ms == 30000 and c.agent.max_turns == 20


def test_linear_adapter_default_states(tmp_path):
    c = cfg_of(LINEAR, tmp_path)
    assert c.tracker.active_states == ("Todo", "In Progress", "In Review")
    assert c.tracker.terminal_states == ("Done", "Canceled", "Duplicate")


def test_explicit_states_override(tmp_path):
    fm = {"tracker": {"kind": "linear", "active_states": ["Todo"], "terminal_states": ["Done"]}}
    c = cfg_of(fm, tmp_path)
    assert c.tracker.active_states == ("Todo",) and c.tracker.terminal_states == ("Done",)


def test_unknown_keys_ignored(tmp_path):
    c = cfg_of({"future_ext": {"x": 1}, "agent": {"what": 2}}, tmp_path)
    assert c.agent.max_turns == 20


def test_overrides_and_extensions(tmp_path):
    fm = {
        "polling": {"interval_ms": 5000},
        "hooks": {"after_create": "git worktree add .\n", "timeout_ms": 1000},
        "agent": {"max_concurrent_agents": 2, "max_turns": 5, "max_retry_backoff_ms": 1000,
                  "continuation_prompt": "go on {{ issue.identifier }}"},
        "codex": {"transport": "stdio", "approval_policy": {"reject": {"sandbox_approval": True}},
                  "approvals_reviewer": None, "turn_sandbox_policy": {"type": "readOnly"},
                  "stall_timeout_ms": 0},
        "budget": {"token_cap": 500000},
        "notify": {"states": ["Done"], "failure_attempts": 5, "enabled": False},
        "rex": {"enabled": False, "session": "sym2"},
    }
    c = cfg_of(fm, tmp_path)
    assert c.poll_interval_ms == 5000
    assert c.hooks.after_create == "git worktree add .\n" and c.hooks.timeout_ms == 1000
    assert (c.agent.max_concurrent_agents, c.agent.max_turns, c.agent.max_retry_backoff_ms) == (
        2, 5, 1000)
    assert c.agent.continuation_prompt == "go on {{ issue.identifier }}"
    assert c.codex.transport == "stdio"
    assert c.codex.approval_policy == {"reject": {"sandbox_approval": True}}
    assert c.codex.approvals_reviewer is None
    assert c.codex.turn_sandbox_policy == {"type": "readOnly"}
    assert c.codex.stall_timeout_ms == 0
    assert c.ext.token_cap == 500000
    assert c.ext.notify_states == ("Done",) and c.ext.notify_failure_attempts == 5
    assert c.ext.notify_enabled is False
    assert c.ext.rex_enabled is False and c.ext.rex_session == "sym2"


@pytest.mark.parametrize("fm", [
    {"hooks": {"timeout_ms": 0}},
    {"hooks": {"timeout_ms": -5}},
    {"hooks": {"timeout_ms": "soon"}},
    {"hooks": {"after_create": 5}},
    {"agent": {"max_turns": 0}},
    {"agent": {"max_turns": True}},
    {"agent": {"max_concurrent_agents": "many"}},
    {"polling": {"interval_ms": 1.5}},
    {"polling": "fast"},
    {"tracker": ["linear"]},
    {"tracker": {"provider": "x"}},
    {"tracker": {"required_labels": "agent"}},
    {"codex": {"transport": "carrier-pigeon"}},
    {"codex": {"command": ["codex"]}},
    {"budget": {"token_cap": 0}},
    {"notify": {"enabled": "yes"}},
    {"workspace": {"repos": ["a"]}},
    {"workspace": {"root": "$UNSET_VAR"}},
])
def test_invalid_values_raise_config_error(tmp_path, fm):
    with pytest.raises(ConfigError) as e:
        cfg_of(fm, tmp_path)
    assert e.value.category == "invalid_config"


def test_numeric_strings_coerced(tmp_path):
    assert cfg_of({"agent": {"max_turns": "7"}}, tmp_path).agent.max_turns == 7


# --- tracker ----------------------------------------------------------------------------------

def test_provider_preserves_unknown_keys(tmp_path):
    prov = {"team_key": "TAL", "keychain_service": "symphony-linear-mcp",
            "wake_states": ["In Review"],
            "signatures": {"agent": "🤖 agent"}, "something_new": {"nested": [1, 2]}}
    c = cfg_of({"tracker": {"kind": "linear", "provider": prov}}, tmp_path)
    assert c.tracker.provider == prov


def test_required_labels_normalized(tmp_path):
    c = cfg_of({"tracker": {"required_labels": [" Agent ", "agent", "  "]}}, tmp_path)
    assert c.tracker.required_labels == ("agent", "")


def test_kind_normalized(tmp_path):
    assert cfg_of({"tracker": {"kind": " Linear "}}, tmp_path).tracker.kind == "linear"


# --- paths, $VAR and ~ ------------------------------------------------------------------------

def test_workspace_root_relative_to_workflow_dir(tmp_path):
    c = cfg_of({"workspace": {"root": "./ws"}}, tmp_path)
    assert c.workspace_root == tmp_path / "ws"


def test_workspace_root_var(tmp_path):
    env = {"WS": str(tmp_path / "from_env")}
    assert cfg_of({"workspace": {"root": "$WS"}}, tmp_path, env).workspace_root == tmp_path / "from_env"
    assert cfg_of({"workspace": {"root": "${WS}/sub"}}, tmp_path, env).workspace_root == (
        tmp_path / "from_env/sub")


def test_tilde_expansion(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    c = cfg_of({"workspace": {"root": "~/ws"}, "codex": {"socket": "~/sock"}}, tmp_path)
    assert c.workspace_root == tmp_path / "home/ws"
    assert c.codex.socket == tmp_path / "home/sock"


def test_var_then_tilde(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    assert resolve_path("$R/x", {"R": "~/r"}, tmp_path) == tmp_path / "home/r/x"


def test_env_does_not_override_unreferenced_values(tmp_path):
    c = cfg_of({"workspace": {"root": "/abs/ws"}}, tmp_path, {"WORKSPACE_ROOT": "/other"})
    assert c.workspace_root == Path("/abs/ws")


def test_dollar_inside_value_is_not_expanded(tmp_path):
    assert resolve_path("/a/$B", {"B": "x"}, tmp_path) == Path("/a/$B")


def test_repos_map_resolved(tmp_path):
    env = {"CODE": str(tmp_path / "code")}
    fm = {"workspace": {"repos": {"Hydra": "$CODE/hydra", "dot": "../dotfiles"}}}
    c = cfg_of(fm, tmp_path, env)
    assert c.ext.repos == {"hydra": tmp_path / "code/hydra",
                           "dot": (tmp_path.parent / "dotfiles")}


def test_top_level_repos_accepted(tmp_path):
    c = cfg_of({"repos": {"x": "/r/x"}}, tmp_path)
    assert c.ext.repos == {"x": Path("/r/x")}


def test_codex_command_kept_verbatim(tmp_path):
    cmd = "~/bin/codex --flag $HOME app-server"
    c = cfg_of({"codex": {"command": cmd}}, tmp_path, {"HOME": "/nope"})
    assert c.codex.command == cmd


# --- per-state concurrency --------------------------------------------------------------------

def test_by_state_normalized_and_invalid_ignored(tmp_path):
    fm = {"agent": {"max_concurrent_agents_by_state": {
        " In Progress ": 2, "Todo": "3", "Waiting": 0, "Human Review": -1, "Done": "x",
        "Canceled": True, "Dup": 1.5}}}
    c = cfg_of(fm, tmp_path)
    assert c.agent.max_concurrent_agents_by_state == {"in progress": 2, "todo": 3}


def test_state_key():
    assert state_key("  Human Review ") == "human review"


# --- dispatch preflight -----------------------------------------------------------------------

def test_preflight_ok(tmp_path, fake_adapter):
    c = cfg_of(LINEAR, tmp_path)
    env = {"X": "1"}
    validate_dispatch_config(c, env)
    assert fake_adapter == [({"team_key": "TAL"}, env)]


def test_preflight_missing_kind(tmp_path, fake_adapter):
    with pytest.raises(ConfigError) as e:
        validate_dispatch_config(cfg_of({}, tmp_path), {})
    assert e.value.category == "unsupported_tracker_kind"


def test_preflight_unsupported_kind(tmp_path, fake_adapter):
    with pytest.raises(ConfigError) as e:
        validate_dispatch_config(cfg_of({"tracker": {"kind": "jira"}}, tmp_path), {})
    assert e.value.category == "unsupported_tracker_kind"


def test_preflight_adapter_rejects_provider(tmp_path, fake_adapter):
    c = cfg_of({"tracker": {"kind": "linear", "provider": {"team_key": " "}}}, tmp_path)
    with pytest.raises(ConfigError) as e:
        validate_dispatch_config(c, {})
    assert e.value.category == "invalid_tracker_config"


def test_preflight_stdio_needs_command(tmp_path, fake_adapter):
    c = cfg_of({**LINEAR, "codex": {"transport": "stdio", "command": "  "}}, tmp_path)
    with pytest.raises(ConfigError) as e:
        validate_dispatch_config(c, {})
    assert e.value.category == "missing_codex_command"


def test_preflight_daemon_ignores_command(tmp_path, fake_adapter):
    c = cfg_of({**LINEAR, "codex": {"command": ""}}, tmp_path)
    validate_dispatch_config(c, {})

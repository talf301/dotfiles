"""Typed service config: defaults, $VAR and ~ resolution, dispatch preflight (SPEC §5.3, §6)."""

from __future__ import annotations

import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .errors import ConfigError
from .workflow import WorkflowDefinition

SUPPORTED_TRACKERS = {"linear"}
DEFAULT_SOCKET = "~/.codex/app-server-control/app-server-control.sock"


@dataclass(frozen=True)
class TrackerConfig:
    kind: str | None
    provider: dict
    required_labels: tuple[str, ...] = ()
    active_states: tuple[str, ...] = ()
    terminal_states: tuple[str, ...] = ()


@dataclass(frozen=True)
class HooksConfig:
    after_create: str | None = None
    before_run: str | None = None
    after_run: str | None = None
    before_remove: str | None = None
    timeout_ms: int = 60000


@dataclass(frozen=True)
class AgentConfig:
    max_concurrent_agents: int = 3
    max_turns: int = 20
    max_retry_backoff_ms: int = 300000
    max_concurrent_agents_by_state: Mapping[str, int] = field(default_factory=dict)
    continuation_prompt: str | None = None


@dataclass(frozen=True)
class CodexConfig:
    command: str = "codex app-server"
    transport: str = "daemon"
    socket: Path = Path(DEFAULT_SOCKET).expanduser()
    approval_policy: Any = "on-request"
    approvals_reviewer: str | None = "auto_review"
    thread_sandbox: Any = "workspace-write"
    turn_sandbox_policy: Any = None
    turn_timeout_ms: int = 3600000
    read_timeout_ms: int = 5000
    stall_timeout_ms: int = 300000


@dataclass(frozen=True)
class ExtensionsConfig:
    repos: Mapping[str, Path] = field(default_factory=dict)
    token_cap: int = 3_000_000
    notify_states: tuple[str, ...] = ("In Review", "Done")
    notify_failure_attempts: int = 3
    notify_enabled: bool = True
    rex_enabled: bool = True
    rex_session: str = "symphony"


@dataclass(frozen=True)
class ServiceConfig:
    workflow_path: Path
    poll_interval_ms: int
    workspace_root: Path
    tracker: TrackerConfig
    hooks: HooksConfig
    agent: AgentConfig
    codex: CodexConfig
    ext: ExtensionsConfig


def state_key(name: str) -> str:
    """Normalized state/label comparison key: trim + lowercase."""
    return name.strip().lower()


_VAR_PREFIX = re.compile(r"^\$(?:\{([A-Za-z_][A-Za-z0-9_]*)\}|([A-Za-z_][A-Za-z0-9_]*))")


def resolve_path(value: str, env: Mapping[str, str], base_dir: Path) -> Path:
    """$VAR / ${VAR} expansion, then ~, then relative to base_dir; returns an absolute path.
    Raises ConfigError(invalid_config) if the value is not a string or resolves empty."""
    if not isinstance(value, str):
        raise ConfigError("invalid_config", f"path must be a string, got {type(value).__name__}")
    m = _VAR_PREFIX.match(value)
    if m:
        value = env.get(m.group(1) or m.group(2), "") + value[m.end():]
    if not value.strip():
        raise ConfigError("invalid_config", "path resolves to an empty value")
    p = Path(value).expanduser()
    if not p.is_absolute():
        p = Path(base_dir) / p
    return Path(os.path.abspath(p))


def _section(raw: Mapping, key: str) -> dict:
    v = raw.get(key)
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ConfigError("invalid_config", f"{key} must be a map")
    return v


def _int(sec: Mapping, key: str, default: int, where: str, *, positive: bool = True) -> int:
    v = sec.get(key)
    if v is None:
        return default
    if isinstance(v, str) and re.fullmatch(r"\s*-?\d+\s*", v):
        v = int(v)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ConfigError("invalid_config", f"{where}.{key} must be an integer")
    if positive and v <= 0:
        raise ConfigError("invalid_config", f"{where}.{key} must be > 0")
    return v


def _bool(sec: Mapping, key: str, default: bool, where: str) -> bool:
    v = sec.get(key)
    if v is None:
        return default
    if not isinstance(v, bool):
        raise ConfigError("invalid_config", f"{where}.{key} must be true or false")
    return v


def _opt_str(sec: Mapping, key: str, where: str, default: str | None = None) -> str | None:
    if key not in sec:
        return default
    v = sec[key]
    if v is not None and not isinstance(v, str):
        raise ConfigError("invalid_config", f"{where}.{key} must be a string")
    return v


def _str_list(sec: Mapping, key: str, default: tuple[str, ...], where: str) -> tuple[str, ...]:
    v = sec.get(key)
    if v is None:
        return tuple(default)
    if not isinstance(v, list) or not all(isinstance(x, str) for x in v):
        raise ConfigError("invalid_config", f"{where}.{key} must be a list of strings")
    return tuple(v)


def _by_state(v: Any) -> dict[str, int]:
    if v is None:
        return {}
    if not isinstance(v, dict):
        raise ConfigError("invalid_config", "agent.max_concurrent_agents_by_state must be a map")
    out: dict[str, int] = {}
    for k, n in v.items():
        if isinstance(n, str) and n.strip().isdigit():
            n = int(n)
        if isinstance(k, str) and isinstance(n, int) and not isinstance(n, bool) and n > 0:
            out[state_key(k)] = n
    return out


def _tracker(raw: Mapping) -> TrackerConfig:
    sec = _section(raw, "tracker")
    kind = sec.get("kind")
    if kind is not None and not isinstance(kind, str):
        raise ConfigError("invalid_config", "tracker.kind must be a string")
    kind = kind.strip().lower() if kind else None
    provider = sec.get("provider")
    if provider is None:
        provider = {}
    if not isinstance(provider, dict):
        raise ConfigError("invalid_config", "tracker.provider must be a map")
    from . import linear  # lazy: linear imports config

    active, terminal = ((linear.DEFAULT_ACTIVE_STATES, linear.DEFAULT_TERMINAL_STATES)
                        if kind == "linear" else ((), ()))
    labels = _str_list(sec, "required_labels", (), "tracker")
    return TrackerConfig(
        kind=kind,
        provider=dict(provider),
        required_labels=tuple(dict.fromkeys(state_key(x) for x in labels)),
        active_states=_str_list(sec, "active_states", active, "tracker"),
        terminal_states=_str_list(sec, "terminal_states", terminal, "tracker"),
    )


def _codex(raw: Mapping, base: Path, env: Mapping[str, str]) -> CodexConfig:
    sec = _section(raw, "codex")
    d = CodexConfig()
    command = sec.get("command", d.command)
    if command is None:
        command = ""
    if not isinstance(command, str):
        raise ConfigError("invalid_config", "codex.command must be a string")
    transport = sec.get("transport", d.transport)
    if transport not in ("daemon", "stdio"):
        raise ConfigError("invalid_config", "codex.transport must be 'daemon' or 'stdio'")
    socket = sec.get("socket")
    return CodexConfig(
        command=command,
        transport=transport,
        socket=resolve_path(socket, env, base) if socket is not None else d.socket,
        approval_policy=sec.get("approval_policy", d.approval_policy),
        approvals_reviewer=_opt_str(sec, "approvals_reviewer", "codex", d.approvals_reviewer),
        thread_sandbox=sec.get("thread_sandbox", d.thread_sandbox),
        turn_sandbox_policy=sec.get("turn_sandbox_policy", d.turn_sandbox_policy),
        turn_timeout_ms=_int(sec, "turn_timeout_ms", d.turn_timeout_ms, "codex"),
        read_timeout_ms=_int(sec, "read_timeout_ms", d.read_timeout_ms, "codex"),
        stall_timeout_ms=_int(sec, "stall_timeout_ms", d.stall_timeout_ms, "codex",
                              positive=False),
    )


def _ext(raw: Mapping, workspace: Mapping, base: Path, env: Mapping[str, str]) -> ExtensionsConfig:
    d = ExtensionsConfig()
    repos_raw = workspace.get("repos", raw.get("repos"))
    if repos_raw is None:
        repos_raw = {}
    if not isinstance(repos_raw, dict):
        raise ConfigError("invalid_config", "repos must be a map of name -> path")
    repos = {state_key(str(k)): resolve_path(v, env, base) for k, v in repos_raw.items()}
    budget = _section(raw, "budget")
    notify = _section(raw, "notify")
    rex = _section(raw, "rex")
    return ExtensionsConfig(
        repos=repos,
        token_cap=_int(budget, "token_cap", d.token_cap, "budget"),
        notify_states=_str_list(notify, "states", d.notify_states, "notify"),
        notify_failure_attempts=_int(notify, "failure_attempts", d.notify_failure_attempts,
                                     "notify"),
        notify_enabled=_bool(notify, "enabled", d.notify_enabled, "notify"),
        rex_enabled=_bool(rex, "enabled", d.rex_enabled, "rex"),
        rex_session=_opt_str(rex, "session", "rex", d.rex_session) or d.rex_session,
    )


def build_config(defn: WorkflowDefinition, env: Mapping[str, str]) -> ServiceConfig:
    """Defaults -> $VAR -> coerce/validate. Raises ConfigError(invalid_config).

    Front-matter extensions (all apply on reload): `workspace.repos` (or top-level `repos`)
    {name: path}; `budget.token_cap`; `notify.{states,failure_attempts,enabled}`;
    `rex.{enabled,session}`; `codex.{transport,socket,approvals_reviewer}`;
    `agent.continuation_prompt`. Tracker wake/signature settings live in the adapter-owned
    `tracker.provider` map and are preserved as-is.
    """
    raw = defn.config
    base = Path(defn.path).parent
    polling = _section(raw, "polling")
    workspace = _section(raw, "workspace")
    hooks = _section(raw, "hooks")
    agent = _section(raw, "agent")
    root = workspace.get("root")
    ad = AgentConfig()
    return ServiceConfig(
        workflow_path=Path(defn.path),
        poll_interval_ms=_int(polling, "interval_ms", 30000, "polling"),
        workspace_root=(resolve_path(root, env, base) if root is not None
                        else Path(tempfile.gettempdir()) / "symphony_workspaces"),
        tracker=_tracker(raw),
        hooks=HooksConfig(
            after_create=_opt_str(hooks, "after_create", "hooks"),
            before_run=_opt_str(hooks, "before_run", "hooks"),
            after_run=_opt_str(hooks, "after_run", "hooks"),
            before_remove=_opt_str(hooks, "before_remove", "hooks"),
            timeout_ms=_int(hooks, "timeout_ms", 60000, "hooks"),
        ),
        agent=AgentConfig(
            max_concurrent_agents=_int(agent, "max_concurrent_agents",
                                       ad.max_concurrent_agents, "agent"),
            max_turns=_int(agent, "max_turns", ad.max_turns, "agent"),
            max_retry_backoff_ms=_int(agent, "max_retry_backoff_ms", ad.max_retry_backoff_ms,
                                      "agent"),
            max_concurrent_agents_by_state=_by_state(agent.get("max_concurrent_agents_by_state")),
            continuation_prompt=_opt_str(agent, "continuation_prompt", "agent"),
        ),
        codex=_codex(raw, base, env),
        ext=_ext(raw, workspace, base, env),
    )


def validate_dispatch_config(cfg: ServiceConfig, env: Mapping[str, str]) -> None:
    """§6.3 preflight. Raises ConfigError."""
    kind = cfg.tracker.kind
    if not kind:
        raise ConfigError("unsupported_tracker_kind", "tracker.kind is required")
    if kind not in SUPPORTED_TRACKERS:
        raise ConfigError("unsupported_tracker_kind", f"tracker.kind {kind!r} is not supported")
    if kind == "linear":
        from . import linear  # lazy: linear imports config

        linear.validate_provider_config(cfg.tracker.provider, env)
    if cfg.codex.transport == "stdio" and not cfg.codex.command.strip():
        raise ConfigError("missing_codex_command", "codex.command is empty")
    if cfg.codex.transport == "daemon" and not str(cfg.codex.socket).strip():
        raise ConfigError("invalid_config", "codex.socket is required for the daemon transport")

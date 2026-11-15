"""Tracker contract (SPEC §11) and adapter selection. The orchestrator only uses this protocol."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from datetime import datetime
from typing import Protocol

from .config import TrackerConfig
from .errors import ConfigError
from .models import Issue


class Tracker(Protocol):
    def fetch_issues_by_states(self, state_names: Sequence[str]) -> list[Issue]:
        """§11.1 state-list read. Empty input -> [] without a request. Raises TrackerError."""
        ...

    def fetch_issues_by_ids(self, issue_ids: Sequence[str]) -> list[Issue]:
        """§11.1 ID refresh. Empty input -> [] without a request. Raises TrackerError."""
        ...

    def prepare_dispatch(self, issue: Issue) -> Issue:
        """Before each turn: un-park a woken ticket (remove `waiting`, In Review -> In Progress;
        deviation 3)."""
        ...

    def escalate(self, issue: Issue, text: str) -> None:
        """Signed orchestrator comment with a question for Tal, then move to In Review."""
        ...

    def post_note(self, issue: Issue, text: str) -> None:
        """Signed orchestrator comment."""
        ...

    def secret_environment_names(self) -> list[str]:
        """Env names to strip from hook and stdio child environments."""
        ...


def make_tracker(cfg: TrackerConfig, env: Mapping[str, str],
                 turn_started_at: Callable[[str], datetime | None]) -> Tracker:
    """Build the adapter for cfg.kind. Raises ConfigError(unsupported_tracker_kind | ...)."""
    if cfg.kind != "linear":
        raise ConfigError("unsupported_tracker_kind", f"tracker.kind {cfg.kind!r} is not supported")
    from . import linear

    from . import mcp_linear

    linear.validate_provider_config(cfg.provider, env)
    tools = mcp_linear.service_session(
        url=cfg.provider.get("endpoint") or mcp_linear.MCP_URL,
        keychain_service=cfg.provider.get("keychain_service") or mcp_linear.KEYCHAIN_SERVICE)
    return linear.LinearTracker(cfg.provider, active_states=cfg.active_states,
                                terminal_states=cfg.terminal_states, tools=tools,
                                turn_started_at=turn_started_at,
                                required_labels=cfg.required_labels)

"""Domain model (SPEC §4, DESIGN.md "Models"). Plain dataclasses, no behavior beyond to_template."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .orchestrator import WorkerHandle


@dataclass(frozen=True)
class BlockerRef:
    id: str | None
    identifier: str | None
    state: str | None


@dataclass(frozen=True)
class Issue:
    """Normalized issue (§4.1.1). Every field is always present."""

    id: str
    identifier: str
    title: str
    state: str
    dispatchable: bool
    description: str | None = None
    priority: int | None = None
    branch_name: str | None = None
    url: str | None = None
    assignee_id: str | None = None
    labels: tuple[str, ...] = ()
    blocked_by: tuple[BlockerRef, ...] = ()
    native_ref: dict | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None

    def to_template(self) -> dict[str, Any]:
        """String-keyed dict for prompt rendering; lists stay iterable, timestamps as ISO strings."""
        d = asdict(self)
        d["labels"] = list(self.labels)
        d["blocked_by"] = [asdict(b) for b in self.blocked_by]
        for k in ("created_at", "updated_at"):
            d[k] = d[k].isoformat() if d[k] else None
        return d


@dataclass(frozen=True)
class Workspace:
    path: Path
    workspace_key: str
    created_now: bool


@dataclass
class ThreadRecord:
    """Per-ticket Codex thread affinity record, persisted by workspace.ThreadStore."""

    thread_id: str
    created_at: datetime
    last_turn_id: str | None = None
    last_turn_started_at: datetime | None = None
    total_tokens: int = 0


@dataclass
class AgentEvent:
    """Runner event posted upstream to the orchestrator (§10.4)."""

    event: str
    timestamp: datetime
    thread_id: str | None = None
    turn_id: str | None = None
    codex_app_server_pid: str | None = None
    usage: dict | None = None
    rate_limits: dict | None = None
    message: str | None = None
    method: str | None = None
    scoped: bool = True  # False: account-wide message, not activity of this worker's turn


@dataclass
class WorkerOutcome:
    normal: bool
    reason: str
    error: str | None = None
    final_issue: Issue | None = None


@dataclass
class RetryEntry:
    issue_id: str
    identifier: str
    attempt: int
    due_at_ms: int
    error: str | None = None


@dataclass
class RunningEntry:
    issue: Issue
    identifier: str
    worker: "WorkerHandle"
    started_at: datetime
    started_ms: int
    retry_attempt: int | None
    session_id: str | None = None
    thread_id: str | None = None
    turn_id: str | None = None
    codex_app_server_pid: str | None = None
    last_codex_event: str | None = None
    last_codex_timestamp_ms: int | None = None
    last_codex_message: str | None = None
    codex_input_tokens: int = 0
    codex_output_tokens: int = 0
    codex_total_tokens: int = 0
    last_reported_input_tokens: int = 0
    last_reported_output_tokens: int = 0
    last_reported_total_tokens: int = 0
    turn_count: int = 0


def _zero_totals() -> dict:
    return {"input_tokens": 0, "output_tokens": 0, "total_tokens": 0, "seconds_running": 0.0}


@dataclass
class OrchestratorState:
    poll_interval_ms: int
    max_concurrent_agents: int
    running: dict[str, RunningEntry] = field(default_factory=dict)
    claimed: set[str] = field(default_factory=set)
    retry_attempts: dict[str, RetryEntry] = field(default_factory=dict)
    completed: set[str] = field(default_factory=set)
    codex_totals: dict = field(default_factory=_zero_totals)
    codex_rate_limits: dict | None = None
    failure_notified: set[str] = field(default_factory=set)

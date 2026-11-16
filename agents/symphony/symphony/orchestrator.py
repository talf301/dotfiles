"""Single-authority scheduler: poll, dispatch, reconcile, retry (SPEC §7, §8, §14, §16).

Workers run in threads and only post messages to `Orchestrator.inbox`:
("codex", issue_id, AgentEvent) and ("exit", issue_id, WorkerOutcome). Only the thread that
calls run() (or, in tests, the test itself) touches `state`.

A worker that reconciliation or stall detection cancels leaves `running` at once but stays in
`draining` until its exit message arrives. A draining issue is never dispatched, so one
workspace never has two workers, and terminal cleanup waits for the worker to finish.
"""

from __future__ import annotations

import json
import os
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

from .config import ServiceConfig, state_key, validate_dispatch_config
from .errors import SymphonyError
from .linear import DEFAULT_WAITING_LABEL, DEFAULT_WAKE_STATES, is_parked
from .log import log
from .models import (AgentEvent, Issue, OrchestratorState, RetryEntry, RunningEntry,
                     WorkerOutcome)
from .tracker import Tracker, make_tracker
from .workflow import WorkflowDefinition, WorkflowStore
from .workspace import ThreadStore, WorkspaceManager, cleanup_terminal_workspaces

if TYPE_CHECKING:
    from .notify import Notifier

CONTINUATION_DELAY_MS = 1000
FAILURE_BASE_MS = 10000
DRAINING_RECHECK_MS = 1000
SHUTDOWN_GRACE_S = 10.0
NO_SLOTS = "no available orchestrator slots"


def monotonic_ms() -> int:
    return int(time.monotonic() * 1000)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class WorkerHandle(Protocol):
    def cancel(self, reason: str) -> None: ...

    def stop_after_turn(self) -> None: ...

    def is_alive(self) -> bool: ...


@dataclass
class WorkerContext:
    config: ServiceConfig
    workflow: WorkflowDefinition
    tracker: Tracker
    workspaces: WorkspaceManager
    threads: ThreadStore
    post: Callable[[tuple], None]


WorkerFactory = Callable[[Issue, "int | None", WorkerContext], WorkerHandle]


class ThreadWorker:
    """A daemon thread running CodexRunner.run_attempt. Always posts exactly one exit message."""

    def __init__(self, issue: Issue, attempt: int | None, ctx: WorkerContext, *,
                 runner_factory=None):
        from .runner import CodexRunner

        self._cancel = threading.Event()
        self._stop = threading.Event()
        self.cancel_reason: str | None = None
        cfg = ctx.config
        rex = None
        if cfg.ext.rex_enabled:
            def rex(issue: Issue, cwd: Path, thread_id: str) -> None:
                from . import rex as rex_mod
                rex_mod.ensure_tab(issue.identifier, cwd, thread_id,
                                   session_label=cfg.ext.rex_session,
                                   store_dir=ctx.threads.dir_for(issue.identifier))
        self._runner = (runner_factory or CodexRunner)(
            config=cfg, workflow=ctx.workflow, tracker=ctx.tracker, workspaces=ctx.workspaces,
            threads=ctx.threads, on_event=lambda ev: ctx.post(("codex", issue.id, ev)), rex=rex)
        self._issue, self._attempt, self._post = issue, attempt, ctx.post
        self._thread = threading.Thread(target=self._main, daemon=True,
                                        name=f"symphony-{issue.identifier}")
        self._thread.start()

    def _main(self) -> None:
        outcome = WorkerOutcome(False, "worker_crashed", "worker ended without an outcome",
                                self._issue)
        try:
            outcome = self._runner.run_attempt(self._issue, self._attempt, cancel=self._cancel,
                                               stop_after_turn=self._stop)
        except BaseException as e:  # noqa: BLE001 - the exit message must always be posted
            outcome = WorkerOutcome(False, "worker_crashed", f"{type(e).__name__}: {e}",
                                    self._issue)
        finally:
            self._post(("exit", self._issue.id, outcome))

    def cancel(self, reason: str) -> None:
        self.cancel_reason = reason
        self._cancel.set()

    def stop_after_turn(self) -> None:
        self._stop.set()

    def is_alive(self) -> bool:
        return self._thread.is_alive()

    def join(self, timeout: float | None = None) -> None:
        self._thread.join(timeout)


def thread_worker_factory(issue: Issue, attempt: int | None, ctx: WorkerContext) -> WorkerHandle:
    """Real worker: a thread running CodexRunner.run_attempt, posting events/exit to ctx.post."""
    return ThreadWorker(issue, attempt, ctx)


def _sort_key(issue: Issue):
    p = issue.priority if isinstance(issue.priority, int) and 1 <= issue.priority <= 4 else 5
    created = issue.created_at.timestamp() if issue.created_at else float("inf")
    return (p, created, issue.identifier)


def sort_for_dispatch(issues: list[Issue]) -> list[Issue]:
    """Priority 1..4 first (ascending), then created_at oldest (None last), then identifier."""
    return sorted(issues, key=_sort_key)


def retry_delay_ms(attempt: int, *, continuation: bool, max_backoff_ms: int) -> int:
    """1000 for continuation; else min(10000 * 2**(attempt-1), max_backoff_ms)."""
    if continuation:
        return CONTINUATION_DELAY_MS
    return min(FAILURE_BASE_MS * 2 ** max(attempt - 1, 0), max_backoff_ms)


def _fields(issue_id: str, identifier: str | None, **extra) -> dict:
    return dict(issue_id=issue_id, issue_identifier=identifier, **extra)


class Orchestrator:
    def __init__(self, store: WorkflowStore, *, worker_factory: WorkerFactory,
                 notifier: "Notifier",
                 tracker_factory: Callable[..., Tracker] = make_tracker,
                 clock_ms: Callable[[], int] = monotonic_ms,
                 wall: Callable[[], datetime] = utcnow,
                 status_path: Path | None = None):
        self.store = store
        self.worker_factory = worker_factory
        self.notifier = notifier
        self.tracker_factory = tracker_factory
        self.clock_ms = clock_ms
        self.wall = wall
        self.status_path = status_path
        self.inbox: queue.Queue = queue.Queue()
        self.state: OrchestratorState | None = None
        self.tracker: Tracker | None = None
        self.workspaces: WorkspaceManager | None = None
        self.threads: ThreadStore | None = None
        self.validation_error: SymphonyError | None = None
        # Keep the identifier even when cancellation does not request terminal cleanup.
        # issue_id -> (worker, issue to clean up or None, identifier)
        self.draining: dict[str, tuple[WorkerHandle, Issue | None, str]] = {}
        self._tracker_cfg = None
        self._next_tick_ms = 0
        self._urls: dict[str, str | None] = {}
        self._token_base_pending: dict[str, int] = {}
        self._parking: set[str] = set()          # running issues told to stop after the turn
        self._state_notified: set[str] = set()   # running issues already notified for state
        self._workspace_kept_notified: set[str] = set()

    # -- config ------------------------------------------------------------------------------

    @property
    def env(self):
        return self.store.env

    @property
    def config(self) -> ServiceConfig:
        return self.store.current[1]

    @property
    def workflow(self) -> WorkflowDefinition:
        return self.store.current[0]

    def _apply_config(self, *, strict: bool = False) -> None:
        """Re-apply the store's current config: intervals, limits, workspace manager, tracker
        (rebuilt only when tracker config changed). strict=True raises tracker build errors."""
        cfg = self.config
        if self.state is not None:
            self.state.poll_interval_ms = cfg.poll_interval_ms
            self.state.max_concurrent_agents = cfg.agent.max_concurrent_agents
        self.threads = ThreadStore(cfg.workspace_root)
        if cfg.tracker != self._tracker_cfg or self.tracker is None:
            try:
                self.tracker = self.tracker_factory(
                    cfg.tracker, self.env, lambda ident: self.threads.turn_started_at(ident))
                self._tracker_cfg = cfg.tracker
            except SymphonyError as e:
                if strict:
                    raise
                self.tracker, self._tracker_cfg = None, None
                log("tracker_build_failed", "error", error=str(e), category=e.category)
        strip = self.tracker.secret_environment_names() if self.tracker else []
        self.workspaces = WorkspaceManager(cfg.workspace_root, cfg.hooks, repos=cfg.ext.repos,
                                           strip_env=strip, on_archive=self._workspace_archived)

    def reload(self) -> bool:
        """Apply a changed WORKFLOW.md. Invalid files keep the last good config."""
        if self.store.reload_if_changed():
            self._apply_config()
            log("config_applied", poll_interval_ms=self.config.poll_interval_ms,
                max_concurrent_agents=self.config.agent.max_concurrent_agents)
            return True
        return False

    # -- startup -----------------------------------------------------------------------------

    def startup(self) -> None:
        try:
            self.store.current
        except SymphonyError:
            self.store.load_initial()
        cfg = self.config
        try:
            validate_dispatch_config(cfg, self.env)
        except SymphonyError as e:
            log("startup_validation_failed", "error", error=str(e), category=e.category)
            raise
        self.state = OrchestratorState(poll_interval_ms=cfg.poll_interval_ms,
                                       max_concurrent_agents=cfg.agent.max_concurrent_agents)
        self._apply_config(strict=True)
        skipped = cleanup_terminal_workspaces(self.workspaces, self.tracker,
                                              cfg.tracker.terminal_states)
        for issue in skipped:
            self._workspace_kept_notified.add(issue.identifier)
            self._notify(issue.identifier, "workspace kept",
                         "before_remove failed, so the workspace was not deleted")
        self._next_tick_ms = self.clock_ms()
        log("startup_completed", workflow=str(cfg.workflow_path),
            poll_interval_ms=cfg.poll_interval_ms)

    # -- selection ---------------------------------------------------------------------------

    def _active(self) -> set[str]:
        return {state_key(s) for s in self.config.tracker.active_states}

    def _terminal(self) -> set[str]:
        return {state_key(s) for s in self.config.tracker.terminal_states}

    def issue_routable(self, issue: Issue) -> bool:
        """dispatchable and every required label present (blank label matches nothing)."""
        if not issue.dispatchable:
            return False
        labels = {state_key(label) for label in issue.labels}
        return all(r and r in labels for r in self.config.tracker.required_labels)

    def _parked_state(self, issue: Issue) -> bool:
        """Required labels present and parked (`waiting` label or In Review): the agent parked
        the ticket itself."""
        labels = {state_key(label) for label in issue.labels}
        if not all(r and r in labels for r in self.config.tracker.required_labels):
            return False
        provider = self.config.tracker.provider
        return is_parked(state=issue.state, labels=issue.labels,
                         wake_states=provider.get("wake_states") or DEFAULT_WAKE_STATES,
                         waiting_label=provider.get("waiting_label") or DEFAULT_WAITING_LABEL)

    def _eligible_state(self, issue: Issue) -> bool:
        s = state_key(issue.state or "")
        return s in self._active() and s not in self._terminal()

    def _global_slots(self) -> int:
        return max(self.state.max_concurrent_agents - len(self.state.running), 0)

    def _state_slot_free(self, issue: Issue) -> bool:
        limits = self.config.agent.max_concurrent_agents_by_state
        key = state_key(issue.state)
        if key not in limits:
            return True
        used = sum(1 for e in self.state.running.values() if state_key(e.issue.state) == key)
        return used < limits[key]

    def _slots_for(self, issue: Issue) -> bool:
        return self._global_slots() > 0 and self._state_slot_free(issue)

    def should_dispatch(self, issue: Issue) -> bool:
        """§8.2 candidate rules plus: no draining worker for the issue."""
        if not (issue.id and issue.identifier and issue.title and issue.state):
            return False
        if not self._eligible_state(issue) or not self.issue_routable(issue):
            return False
        if (issue.id in self.state.running or issue.id in self.state.claimed
                or issue.id in self.draining):
            return False
        return self._slots_for(issue)

    # -- tick --------------------------------------------------------------------------------

    def tick(self) -> None:
        """§16.2: reconcile, validate, fetch candidates, dispatch while slots remain."""
        self.reload()
        self.reconcile()
        try:
            if self.store.last_error is not None:  # §5.5: a broken WORKFLOW.md blocks dispatch
                raise self.store.last_error
            validate_dispatch_config(self.config, self.env)
            self.validation_error = None
        except SymphonyError as e:
            self.validation_error = e
            log("dispatch_validation_failed", "error", error=str(e), category=e.category)
            self.write_status()
            return
        if self.tracker is None:
            self._apply_config()
            if self.tracker is None:
                self.write_status()
                return
        self._cleanup_unowned_workspaces()
        try:
            issues = self.tracker.fetch_issues_by_states(list(self.config.tracker.active_states))
        except SymphonyError as e:
            log("candidate_fetch_failed", "warning", error=str(e), category=e.category)
            self.write_status()
            return
        for issue in sort_for_dispatch(issues):
            if self._global_slots() <= 0:
                break
            if self.should_dispatch(issue):
                self.dispatch(issue, None)
        self.write_status()

    # -- reconciliation ----------------------------------------------------------------------

    def _cleanup_unowned_workspaces(self) -> None:
        """Refresh only local ticket directories, never the team's terminal backlog.

        Running and draining workers own their workspace until their exit is processed.
        Hidden metadata/archive directories, files and symlinks are not workspaces.
        """
        owned = {entry.identifier for entry in self.state.running.values()}
        owned.update(identifier for _, _, identifier in self.draining.values())
        root = Path(self.workspaces.root).expanduser()
        team = re.escape(self.config.tracker.provider["team_key"])
        try:
            present = {path.name for path in root.iterdir()
                       if re.fullmatch(rf"{team}-[0-9]+", path.name, re.IGNORECASE)
                       and not path.is_symlink() and path.is_dir()}
        except FileNotFoundError:
            self._workspace_kept_notified.clear()
            return
        except OSError as e:
            log("workspace_scan_failed", "warning", error=str(e))
            return
        self._workspace_kept_notified.intersection_update(present)
        identifiers = sorted(present - owned)
        if not identifiers:
            return
        try:
            issues = self.tracker.fetch_issues_by_ids(identifiers)
        except SymphonyError as e:
            log("workspace_refresh_failed", "warning", error=str(e), category=e.category)
            return
        for issue in issues:
            if state_key(issue.state) not in self._terminal():
                self._workspace_kept_notified.discard(issue.identifier)
            if (issue.identifier in identifiers and issue.identifier not in owned
                    and issue.id not in self.state.running and issue.id not in self.draining
                    and state_key(issue.state) in self._terminal()):
                self._remove_workspace(issue)

    def reconcile(self) -> None:
        """§16.3 plus stall detection (§8.5 A) and graceful park (deviation 5): only a ticket
        the agent parked (`waiting` label or In Review) finishes its turn; any other
        unroutable ticket is interrupted."""
        self._reconcile_stalls()
        ids = list(self.state.running)
        if not ids or self.tracker is None:
            return
        try:
            refreshed = self.tracker.fetch_issues_by_ids(ids)
        except SymphonyError as e:
            log("reconcile_refresh_failed", "warning", error=str(e), category=e.category)
            return
        active, terminal = self._active(), self._terminal()
        seen = set()
        for issue in refreshed:
            entry = self.state.running.get(issue.id)
            if entry is None:
                continue
            seen.add(issue.id)
            key = state_key(issue.state)
            if key in terminal:
                self._maybe_notify_state(entry, issue)
                self._terminate(issue.id, "terminal_state", cleanup=issue)
            elif key not in active:
                self._terminate(issue.id, "non_active_state")
            elif self.issue_routable(issue):
                entry.issue = issue
                self._urls[issue.id] = issue.url
            elif not self._parked_state(issue):
                # Label removed, reassigned or newly blocked: the kill switch interrupts now.
                self._terminate(issue.id, "not_routable")
            else:
                entry.issue = issue
                if issue.id not in self._parking:
                    self._parking.add(issue.id)
                    entry.worker.stop_after_turn()
                    log("worker_parking", **_fields(issue.id, issue.identifier,
                                                    state=issue.state,
                                                    session_id=entry.session_id))
        for missing in set(ids) - seen:
            if missing in self.state.running:
                self._terminate(missing, "issue_missing")

    def _reconcile_stalls(self) -> None:
        stall = self.config.codex.stall_timeout_ms
        if stall <= 0:
            return
        now = self.clock_ms()
        for issue_id, entry in list(self.state.running.items()):
            last = entry.last_codex_timestamp_ms or entry.started_ms
            if now - last > stall:
                log("worker_stalled", "warning",
                    **_fields(issue_id, entry.identifier, session_id=entry.session_id,
                              elapsed_ms=now - last))
                self._terminate(issue_id, "stalled")
                self.schedule_retry(issue_id, (entry.retry_attempt or 0) + 1,
                                    identifier=entry.identifier, error="stalled",
                                    continuation=False)

    def _terminate(self, issue_id: str, reason: str, *, cleanup: Issue | None = None) -> None:
        """Cancel a running worker now. It drains until its exit message arrives; workspace
        cleanup (terminal) runs then. The claim is released unless a retry is scheduled."""
        entry = self._pop_running(issue_id)
        entry.worker.cancel(reason)
        self.draining[issue_id] = (entry.worker, cleanup, entry.identifier)
        self.state.claimed.discard(issue_id)
        log("worker_cancelled", **_fields(issue_id, entry.identifier, reason=reason,
                                          session_id=entry.session_id,
                                          cleanup=cleanup is not None))

    def _pop_running(self, issue_id: str) -> RunningEntry | None:
        entry = self.state.running.pop(issue_id, None)
        self._parking.discard(issue_id)
        self._token_base_pending.pop(issue_id, None)
        if entry is not None:
            self._add_runtime(entry)
        return entry

    def _add_runtime(self, entry: RunningEntry) -> None:
        self.state.codex_totals["seconds_running"] += max(
            0.0, (self.clock_ms() - entry.started_ms) / 1000)

    # -- dispatch ----------------------------------------------------------------------------

    def _context(self) -> WorkerContext:
        defn, cfg = self.store.current
        return WorkerContext(config=cfg, workflow=defn, tracker=self.tracker,
                             workspaces=self.workspaces, threads=self.threads,
                             post=self.inbox.put)

    def dispatch(self, issue: Issue, attempt: int | None) -> None:
        """§16.4. Spawn failure schedules a failure retry."""
        self.reload()
        try:
            worker = self.worker_factory(issue, attempt, self._context())
        except Exception as e:  # noqa: BLE001 - any spawn failure becomes a retry
            log("dispatch_failed", "error", **_fields(issue.id, issue.identifier, error=str(e)))
            self.schedule_retry(issue.id, (attempt or 0) + 1, identifier=issue.identifier,
                                error="failed to spawn agent", continuation=False)
            return
        rec = self.threads.read(issue.identifier)
        entry = RunningEntry(issue=issue, identifier=issue.identifier, worker=worker,
                             started_at=self.wall(), started_ms=self.clock_ms(),
                             retry_attempt=attempt, thread_id=rec.thread_id if rec else None)
        self.state.running[issue.id] = entry
        self.state.claimed.add(issue.id)
        self.state.retry_attempts.pop(issue.id, None)
        self._urls[issue.id] = issue.url
        self._state_notified.discard(issue.id)
        # Resumed thread: its usage events carry the thread's cumulative total; the stored
        # total is the baseline so earlier dispatches are not counted again.
        self._token_base_pending[issue.id] = rec.total_tokens if rec else 0
        log("dispatch", **_fields(issue.id, issue.identifier, state=issue.state,
                                  attempt=attempt, thread_id=entry.thread_id))

    # -- worker messages ---------------------------------------------------------------------

    def on_codex_update(self, issue_id: str, ev: AgentEvent) -> None:
        """§13.5: live session fields, absolute-total token deltas, latest rate limits."""
        entry = self.state.running.get(issue_id)
        if entry is None:
            return
        if ev.rate_limits is not None:
            self.state.codex_rate_limits = ev.rate_limits
        if not ev.scoped:
            return  # account-wide (e.g. rate limits): not activity of this worker
        entry.last_codex_event = ev.event
        entry.last_codex_timestamp_ms = self.clock_ms()
        if ev.message:
            entry.last_codex_message = ev.message
        if ev.thread_id:
            entry.thread_id = ev.thread_id
        if ev.turn_id:
            entry.turn_id = ev.turn_id
        if ev.codex_app_server_pid:
            entry.codex_app_server_pid = ev.codex_app_server_pid
        if ev.event == "session_started":
            entry.turn_count += 1
            entry.session_id = ev.message or (f"{ev.thread_id}-{ev.turn_id}")
            log("session_started", **_fields(issue_id, entry.identifier,
                                             session_id=entry.session_id,
                                             turn_count=entry.turn_count))
        elif ev.event not in ("notification", "malformed"):
            log("codex_event", **_fields(issue_id, entry.identifier, session_id=entry.session_id,
                                         codex_event=ev.event, message=ev.message))
        if ev.usage:
            self._account_usage(issue_id, entry, ev.usage)

    def _account_usage(self, issue_id: str, entry: RunningEntry, usage: dict) -> None:
        new = {k: int(usage.get(f"{k}_tokens") or 0) for k in ("input", "output", "total")}
        base = self._token_base_pending.pop(issue_id, None)
        if base is not None:
            if base and new["total"] >= base:
                # Resumed thread: total baseline from the record; input/output split unknown,
                # so scale the first event's split to the delta.
                frac = (new["total"] - base) / new["total"] if new["total"] else 0
                entry.last_reported_input_tokens = round(new["input"] * (1 - frac))
                entry.last_reported_output_tokens = round(new["output"] * (1 - frac))
                entry.last_reported_total_tokens = base
            # else: a new thread (or resume failed and a new one started): baseline 0
        elif new["total"] < entry.last_reported_total_tokens:
            entry.last_reported_input_tokens = entry.last_reported_output_tokens = 0
            entry.last_reported_total_tokens = 0
        totals = self.state.codex_totals
        for k in ("input", "output", "total"):
            last = getattr(entry, f"last_reported_{k}_tokens")
            totals[f"{k}_tokens"] += max(0, new[k] - last)
            setattr(entry, f"last_reported_{k}_tokens", max(last, new[k]))
            setattr(entry, f"codex_{k}_tokens", new[k])

    def on_worker_exit(self, issue_id: str, outcome: WorkerOutcome) -> None:
        """§16.6 plus notifications (In Review, Done, repeated failure, kept workspace)."""
        if issue_id in self.draining:
            _, cleanup, _ = self.draining.pop(issue_id)
            log("worker_drained", **_fields(issue_id, cleanup.identifier if cleanup else None,
                                            reason=outcome.reason, normal=outcome.normal))
            if cleanup is not None:
                self._remove_workspace(cleanup)
            return
        notified = issue_id in self._state_notified
        entry = self._pop_running(issue_id)
        if entry is None:
            log("worker_exit_unknown", "warning", issue_id=issue_id, reason=outcome.reason)
            return
        log("worker_exit", "info" if outcome.normal else "warning",
            **_fields(issue_id, entry.identifier, session_id=entry.session_id,
                      outcome="completed" if outcome.normal else "failed",
                      reason=outcome.reason, error=outcome.error,
                      turn_count=entry.turn_count))
        ext = self.config.ext
        if outcome.reason in ("escalated", "budget"):
            what = "token budget reached" if outcome.reason == "budget" else "question for Tal"
            self._notify(entry.identifier, "In Review", what)
        elif outcome.final_issue is not None and not notified:
            self._maybe_notify_state(entry, outcome.final_issue)
        self._state_notified.discard(issue_id)
        if outcome.normal:
            self.state.completed.add(issue_id)
            self.state.failure_notified.discard(issue_id)
            self.schedule_retry(issue_id, 1, identifier=entry.identifier, error=None,
                                continuation=True)
        else:
            attempt = (entry.retry_attempt or 0) + 1
            self.schedule_retry(issue_id, attempt, identifier=entry.identifier,
                                error=f"worker exited: {outcome.reason}", continuation=False)
            if (attempt >= ext.notify_failure_attempts
                    and issue_id not in self.state.failure_notified):
                self.state.failure_notified.add(issue_id)
                self._notify(entry.identifier, "failing",
                             f"{attempt} failed attempts; last: {outcome.reason}")

    # -- retries -----------------------------------------------------------------------------

    def schedule_retry(self, issue_id: str, attempt: int, *, identifier: str,
                       error: str | None, continuation: bool) -> None:
        delay = retry_delay_ms(attempt, continuation=continuation,
                               max_backoff_ms=self.config.agent.max_retry_backoff_ms)
        self.state.retry_attempts[issue_id] = RetryEntry(
            issue_id=issue_id, identifier=identifier, attempt=attempt,
            due_at_ms=self.clock_ms() + delay, error=error)
        self.state.claimed.add(issue_id)
        log("retry_scheduled", **_fields(issue_id, identifier, attempt=attempt, delay_ms=delay,
                                         error=error, continuation=continuation))

    def _release(self, issue_id: str, identifier: str, why: str) -> None:
        self.state.claimed.discard(issue_id)
        log("claim_released", **_fields(issue_id, identifier, reason=why))

    def on_retry_due(self, issue_id: str) -> None:
        """§16.6 on_retry_timer, plus terminal cleanup (§8.4) and draining workers."""
        entry = self.state.retry_attempts.pop(issue_id, None)
        if entry is None:
            return
        if issue_id in self.draining or self.tracker is None:
            self.state.retry_attempts[issue_id] = RetryEntry(
                issue_id, entry.identifier, entry.attempt,
                self.clock_ms() + DRAINING_RECHECK_MS, entry.error)
            return
        try:
            refreshed = self.tracker.fetch_issues_by_ids([issue_id])
        except SymphonyError as e:
            log("retry_refresh_failed", "warning",
                **_fields(issue_id, entry.identifier, error=str(e)))
            self.schedule_retry(issue_id, entry.attempt + 1, identifier=entry.identifier,
                                error="retry refresh failed", continuation=False)
            return
        issue = next((i for i in refreshed if i.id == issue_id), None)
        if issue is None:
            self._release(issue_id, entry.identifier, "missing")
            return
        if state_key(issue.state) in self._terminal():
            self._release(issue_id, issue.identifier, "terminal")
            self._remove_workspace(issue)
            return
        if not self._eligible_state(issue) or not self.issue_routable(issue):
            self._release(issue_id, issue.identifier, "not_routable")
            return
        if not self._slots_for(issue):
            self.schedule_retry(issue_id, entry.attempt + 1, identifier=issue.identifier,
                                error=NO_SLOTS, continuation=False)
            return
        self.dispatch(issue, entry.attempt)

    def fire_due_retries(self) -> None:
        now = self.clock_ms()
        due = sorted((e.due_at_ms, i) for i, e in self.state.retry_attempts.items()
                     if e.due_at_ms <= now)
        for _, issue_id in due:
            self.on_retry_due(issue_id)

    # -- helpers -----------------------------------------------------------------------------

    def _workspace_archived(self, issue: Issue, destination: str) -> None:
        try:
            if self.tracker is None:
                raise SymphonyError("tracker_unavailable", "cannot post archive destination")
            self.tracker.post_note(issue, f"Workspace archived to `{destination}`")
        except SymphonyError as e:
            # The move has already succeeded; do not claim the workspace was kept or
            # retry an uncertain comment submission. Preserve the destination in logs.
            log("workspace_archive_note_failed", "warning", issue_id=issue.id,
                issue_identifier=issue.identifier, archive=destination, error=str(e))

    def _remove_workspace(self, issue: Issue) -> None:
        try:
            removed = self.workspaces.remove(issue)
        except (SymphonyError, OSError) as e:
            log("workspace_remove_failed", "warning",
                **_fields(issue.id, issue.identifier, error=str(e)))
            removed = False
        if removed:
            self._workspace_kept_notified.discard(issue.identifier)
        elif issue.identifier not in self._workspace_kept_notified:
            self._workspace_kept_notified.add(issue.identifier)
            self._notify(issue.identifier, "workspace kept",
                         "before_remove failed, so the workspace was not deleted")

    def _maybe_notify_state(self, entry: RunningEntry, issue: Issue) -> None:
        """Notify when a worker that ran at least one turn leaves the ticket in a notify state.
        Marks the issue so the same worker's exit does not notify again."""
        if entry.turn_count <= 0 or issue.id in self._state_notified:
            return
        wanted = {state_key(s) for s in self.config.ext.notify_states}
        if state_key(issue.state) in wanted:
            self._state_notified.add(issue.id)
            self._notify(entry.identifier, issue.state, issue.title)

    def _notify(self, identifier: str, reason: str, detail: str) -> None:
        log("notify", issue_identifier=identifier, reason=reason)
        if not self.config.ext.notify_enabled:
            return
        try:
            self.notifier.notify(f"{identifier}: {reason}",
                                 f"{detail} (symphony open {identifier})")
        except Exception as e:  # noqa: BLE001 - notifications never break scheduling
            log("notify_failed", "warning", issue_identifier=identifier, error=str(e))

    # -- loop --------------------------------------------------------------------------------

    def drain_inbox(self, timeout_s: float) -> None:
        try:
            msg = self.inbox.get(timeout=timeout_s) if timeout_s > 0 else self.inbox.get_nowait()
        except queue.Empty:
            return
        while True:
            kind, issue_id, payload = msg
            try:
                if kind == "codex":
                    self.on_codex_update(issue_id, payload)
                elif kind == "exit":
                    self.on_worker_exit(issue_id, payload)
            except Exception as e:  # noqa: BLE001 - one bad message must not stop the loop
                log("inbox_message_failed", "error", issue_id=issue_id, kind=kind,
                    error=f"{type(e).__name__}: {e}")
            try:
                msg = self.inbox.get_nowait()
            except queue.Empty:
                return

    def snapshot(self) -> dict:
        """§13.3 runtime snapshot."""
        now_ms = self.clock_ms()
        st = self.state
        running = [{
            "issue_id": i, "issue_identifier": e.identifier, "state": e.issue.state,
            "url": e.issue.url, "session_id": e.session_id, "thread_id": e.thread_id,
            "turn_id": e.turn_id, "turn_count": e.turn_count,
            "started_at": e.started_at.isoformat(), "retry_attempt": e.retry_attempt,
            "last_event": e.last_codex_event, "last_message": e.last_codex_message,
            "idle_ms": now_ms - (e.last_codex_timestamp_ms or e.started_ms),
            "tokens": {"input_tokens": e.codex_input_tokens,
                       "output_tokens": e.codex_output_tokens,
                       "total_tokens": e.codex_total_tokens},
            "parking": i in self._parking,
        } for i, e in st.running.items()]
        retrying = [{
            "issue_id": r.issue_id, "issue_identifier": r.identifier, "attempt": r.attempt,
            "due_in_ms": max(0, r.due_at_ms - now_ms), "error": r.error,
            "url": self._urls.get(r.issue_id),
        } for r in st.retry_attempts.values()]
        totals = dict(st.codex_totals)
        totals["seconds_running"] = round(totals["seconds_running"] + sum(
            max(0, now_ms - e.started_ms) / 1000 for e in st.running.values()), 3)
        err = self.store.last_error or self.validation_error
        return {
            "generated_at": self.wall().isoformat(),
            "pid": os.getpid(),
            "workflow": str(self.config.workflow_path),
            "poll_interval_ms": st.poll_interval_ms,
            "max_concurrent_agents": st.max_concurrent_agents,
            "running": running,
            "retrying": retrying,
            "draining": sorted(self.draining),
            "codex_totals": totals,
            "rate_limits": st.codex_rate_limits,
            "error": str(err) if err else None,
        }

    def write_status(self) -> None:
        if self.status_path is None:
            return
        try:
            path = Path(self.status_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(path.name + ".tmp")
            tmp.write_text(json.dumps(self.snapshot(), indent=2, default=str) + "\n")
            os.replace(tmp, path)
        except Exception as e:  # noqa: BLE001 - status is observability only
            log("status_write_failed", "warning", error=str(e))

    def step(self, wait_s: float = 0.0) -> None:
        """One loop iteration: reload, drain worker messages, fire retries, tick when due."""
        self.reload()
        self.drain_inbox(wait_s)
        self.fire_due_retries()
        now = self.clock_ms()
        if now >= self._next_tick_ms:
            self.tick()
            self._next_tick_ms = self.clock_ms() + self.state.poll_interval_ms

    def run(self, stop: threading.Event) -> None:
        """startup(); loop until stop; then cancel workers and wait briefly for their exits."""
        self.startup()
        while not stop.is_set():
            self.step(wait_s=min(1.0, max(0.0, (self._next_tick_ms - self.clock_ms()) / 1000)))
        self.shutdown()

    def shutdown(self, grace_s: float = SHUTDOWN_GRACE_S) -> None:
        """Interrupt running turns (a restart resumes the same thread) and wait up to grace_s."""
        workers = [e.worker for e in self.state.running.values()]
        workers += [w for w, _, _ in self.draining.values()]
        for issue_id in list(self.state.running):
            self._terminate(issue_id, "shutdown")
        deadline = time.monotonic() + grace_s
        while any(w.is_alive() for w in workers) and time.monotonic() < deadline:
            self.drain_inbox(0.1)
        self.drain_inbox(0)
        self.write_status()
        log("shutdown_completed", still_running=sum(1 for w in workers if w.is_alive()))

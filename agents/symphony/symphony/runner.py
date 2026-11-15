"""One worker attempt: workspace, prompt, Codex thread and turn loop (SPEC §10.7, §16.5).

Adds thread affinity, wake-state handling, escalation and the token budget
(DESIGN.md "Runner").
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Mapping
from datetime import datetime, timezone
from pathlib import Path

from .appserver import AppServerClient, Escalation, Transport, open_transport
from .config import CodexConfig, ServiceConfig, state_key
from .errors import AgentError, SymphonyError
from .log import log
from .models import AgentEvent, Issue, ThreadRecord, WorkerOutcome
from .tracker import Tracker
from .workflow import DEFAULT_CONTINUATION, WorkflowDefinition, render_prompt
from .workspace import ThreadStore, WorkspaceManager

BUDGET_LABEL = re.compile(r"^budget:(\d+)k$")

# TurnResult.status -> WorkerOutcome.reason for abnormal turn ends
TURN_FAILURES = {
    "failed": "turn_failed",
    "timeout": "turn_timeout",
    "interrupted": "turn_cancelled",
    "cancelled": "canceled_by_reconciliation",
}

ESCALATION_WHAT = {
    "command_approval": "approval to run a command",
    "file_approval": "approval to change files",
    "permissions": "extra permissions",
    "user_input": "an answer to a question",
    "elicitation": "input requested by an MCP server",
}


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _safe_log(event: str, level: str = "info", **fields) -> None:
    try:
        log(event, level, **fields)
    except Exception:  # log() must never break the runner
        pass


def token_cap(issue: Issue, default: int) -> int:
    """`budget:<n>k` label -> n * 1000, else default."""
    for label in issue.labels:
        m = BUDGET_LABEL.match(label.strip().lower())
        if m:
            return int(m.group(1)) * 1000
    return default


def escalation_question(esc: Escalation) -> str:
    what = ESCALATION_WHAT.get(esc.kind, esc.kind)
    return (f"Question for Tal: Codex asked for {what}: {esc.summary}\n\n"
            "Symphony never approves requests, so the turn was stopped. Reply on this ticket "
            "with how to proceed and the agent will resume on the same thread.")


def budget_question(total: int, cap: int) -> str:
    return (f"Question for Tal: token budget reached ({total} of {cap} tokens). Raise the "
            "budget:<n>k label to let the agent continue.")


class CodexRunner:
    def __init__(self, *, config: ServiceConfig, workflow: WorkflowDefinition, tracker: Tracker,
                 workspaces: WorkspaceManager, threads: ThreadStore,
                 on_event: Callable[[AgentEvent], None],
                 transport_factory: Callable[[CodexConfig, Path, Mapping[str, str]], Transport]
                 = open_transport,
                 rex: Callable[[Issue, Path, str], None] | None = None,
                 wall: Callable[[], datetime] = utcnow):
        self.config = config
        self.workflow = workflow
        self.tracker = tracker
        self.workspaces = workspaces
        self.threads = threads
        self.on_event = on_event
        self.transport_factory = transport_factory
        self.rex = rex
        self.wall = wall
        self._usage_total: int | None = None

    def run_attempt(self, issue: Issue, attempt: int | None, *, cancel: threading.Event,
                    stop_after_turn: threading.Event) -> WorkerOutcome:
        try:
            ws = self.workspaces.ensure(issue)
            self.workspaces.run_hook("before_run", issue, ws.path)
        except SymphonyError as e:
            return WorkerOutcome(False, e.category, str(e), issue)

        state = {"issue": issue, "client": None}
        try:
            outcome = self._session(state, attempt, ws.path, cancel, stop_after_turn)
        except SymphonyError as e:
            outcome = WorkerOutcome(False, e.category, str(e))
        except Exception as e:  # a bug must still end the attempt cleanly
            outcome = WorkerOutcome(False, "worker_crashed", f"{type(e).__name__}: {e}")
        finally:
            if state["client"] is not None:
                state["client"].close()
            self.workspaces.run_hook_best_effort("after_run", state["issue"], ws.path)
        outcome.final_issue = state["issue"]
        _safe_log("worker_attempt_end", issue_id=issue.id, issue_identifier=issue.identifier,
                  normal=outcome.normal, reason=outcome.reason, error=outcome.error)
        return outcome

    def _on_client_event(self, ev: AgentEvent) -> None:
        if ev.usage and ev.usage.get("total_tokens") is not None:
            self._usage_total = ev.usage["total_tokens"]
        self.on_event(ev)

    def _over_budget(self, issue: Issue, rec: ThreadRecord | None) -> bool:
        if rec is None:
            return False
        cap = token_cap(issue, self.config.ext.token_cap)
        if rec.total_tokens < cap:
            return False
        self.tracker.escalate(issue, budget_question(rec.total_tokens, cap))
        return True

    def _session(self, state: dict, attempt: int | None, cwd: Path,
                 cancel: threading.Event, stop_after_turn: threading.Event) -> WorkerOutcome:
        cfg = self.config
        issue: Issue = state["issue"]
        ident = issue.identifier
        rec = self.threads.read(ident)
        if rec is not None and rec.total_tokens >= token_cap(issue, self.config.ext.token_cap):
            # Mark the wake that started this dispatch as handled; otherwise the ticket stays
            # dispatchable and escalates again on every continuation retry.
            rec.last_turn_started_at = self.wall()
            self.threads.write(ident, rec)
        if self._over_budget(issue, rec):
            return WorkerOutcome(True, "budget")

        self.workspaces.assert_agent_cwd(cwd, cwd)
        strip = set(self.tracker.secret_environment_names())
        env = {k: v for k, v in os.environ.items() if k not in strip}
        client = AppServerClient(self.transport_factory(cfg.codex, cwd, env),
                                 read_timeout_s=cfg.codex.read_timeout_ms / 1000,
                                 on_event=self._on_client_event)
        state["client"] = client
        self._usage_total = None
        client.initialize()

        # Thread affinity: resume the ticket's thread, else start one.
        new_thread = False
        if rec is not None:
            try:
                thread_id = client.resume_thread(rec.thread_id, cwd=cwd, policy=cfg.codex)
            except AgentError as e:
                if e.category != "thread_resume_failed":
                    raise
                _safe_log("thread_resume_failed", "warning", issue_id=issue.id,
                          issue_identifier=ident, thread_id=rec.thread_id, error=str(e))
                old, rec = rec.thread_id, None
                try:
                    self.tracker.post_note(issue, f"Could not resume Codex thread {old} "
                                           f"({e.message}); starting a new thread.")
                except SymphonyError as note_err:
                    _safe_log("post_note_failed", "warning", issue_id=issue.id,
                              issue_identifier=ident, error=str(note_err))
        if rec is None:
            thread_id = client.start_thread(cwd=cwd, policy=cfg.codex)
            new_thread = True
            client.set_title(thread_id, f"{ident}: {issue.title}")
            rec = ThreadRecord(thread_id=thread_id, created_at=self.wall())
            self.threads.write(ident, rec)
        if self.rex is not None:
            try:
                self.rex(issue, cwd, thread_id)
            except Exception as e:
                _safe_log("rex_tab_failed", "warning", issue_id=issue.id, issue_identifier=ident,
                          error=str(e))

        active = {state_key(s) for s in cfg.tracker.active_states}
        required = [state_key(label) for label in cfg.tracker.required_labels]
        max_turns = cfg.agent.max_turns
        continuation = cfg.agent.continuation_prompt or DEFAULT_CONTINUATION
        for turn_number in range(1, max_turns + 1):
            if cancel.is_set():
                return WorkerOutcome(False, "canceled_by_reconciliation", "cancelled before turn")
            issue = self.tracker.prepare_dispatch(issue)
            state["issue"] = issue
            first = new_thread and turn_number == 1
            prompt = render_prompt(self.workflow.prompt_template if first else continuation,
                                   issue, attempt, turn_number=turn_number, max_turns=max_turns)

            rec.last_turn_started_at = self.wall()
            self.threads.write(ident, rec)
            result = client.run_turn(thread_id, prompt, cwd=cwd, policy=cfg.codex,
                                     turn_timeout_s=cfg.codex.turn_timeout_ms / 1000,
                                     cancel=cancel)
            rec.last_turn_id = result.turn_id
            if self._usage_total is not None:
                rec.total_tokens = self._usage_total
            self.threads.write(ident, rec)

            if result.status == "escalated":
                self.tracker.escalate(issue, escalation_question(result.escalation))
                return WorkerOutcome(True, "escalated")
            if result.status != "completed":
                return WorkerOutcome(False, TURN_FAILURES.get(result.status, "turn_failed"),
                                     result.error)

            refreshed = self.tracker.fetch_issues_by_ids([issue.id])
            if not refreshed:
                return WorkerOutcome(True, "not_routable")
            issue = refreshed[0]
            state["issue"] = issue
            labels = set(issue.labels)
            if (state_key(issue.state) not in active or not issue.dispatchable
                    or any(not r or r not in labels for r in required)):
                return WorkerOutcome(True, "not_routable")
            if self._over_budget(issue, rec):
                return WorkerOutcome(True, "budget")
            if stop_after_turn.is_set():
                return WorkerOutcome(True, "stopped")
        return WorkerOutcome(True, "max_turns")

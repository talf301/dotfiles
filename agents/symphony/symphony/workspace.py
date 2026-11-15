"""Per-issue workspaces, lifecycle hooks and the thread record store (SPEC §9, DESIGN.md)."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path

from .config import HooksConfig
from .errors import HookError, SymphonyError, WorkspaceError
from .log import log
from .models import Issue, ThreadRecord, Workspace

STATE_DIRNAME = ".symphony"
HOOK_OUTPUT_LOG_LIMIT = 2048
HOOK_NAMES = ("after_create", "before_run", "after_run", "before_remove")

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]")


def workspace_key(identifier: str) -> str:
    """Sanitize to [A-Za-z0-9._-]; if changed, append '-' + 16 hex chars of sha256(identifier)."""
    key = _UNSAFE.sub("_", identifier)
    if key != identifier or not key or key.startswith("."):
        key = f"{key}-{hashlib.sha256(identifier.encode('utf-8')).hexdigest()[:16]}"
    return key


def _truncate(text: str, limit: int = HOOK_OUTPUT_LOG_LIMIT) -> str:
    """Keep the tail, where failures usually show."""
    return text if len(text) <= limit else "..." + text[-limit:]


def _bare_issue(identifier: str) -> Issue:
    return Issue(id="", identifier=identifier, title="", state="", dispatchable=False)


class WorkspaceManager:
    def __init__(self, root: Path, hooks: HooksConfig, *, repos: Mapping[str, Path],
                 strip_env: Sequence[str], run=subprocess.run):
        self.root = root
        self.hooks = hooks
        self.repos = repos
        self.strip_env = tuple(strip_env)
        self._run = run
        self.threads = ThreadStore(root)

    def _root(self) -> Path:
        return Path(self.root).expanduser().resolve()

    def _check_inside_root(self, path: Path) -> Path:
        root = self._root()
        resolved = path.resolve()
        if resolved == root or not resolved.is_relative_to(root):
            raise WorkspaceError("workspace_path_outside_root",
                                 f"{resolved} is not inside {root}")
        return resolved

    def path_for(self, identifier: str) -> Path:
        """root / workspace_key(identifier), containment checked."""
        path = self._root() / workspace_key(identifier)
        self._check_inside_root(path)
        return path

    def ensure(self, issue: Issue) -> Workspace:
        """Create or reuse; after_create on new dirs (failure removes the new dir, raises)."""
        key = workspace_key(issue.identifier)
        path = self.path_for(issue.identifier)
        if path.exists() and not path.is_dir():
            raise WorkspaceError("workspace_not_directory", f"{path} exists and is not a directory")
        try:
            path.mkdir(parents=True)
            created_now = True
        except FileExistsError:
            created_now = False
        except OSError as e:
            raise WorkspaceError("workspace_create_failed", f"{path}: {e}") from e
        if not path.is_dir():
            raise WorkspaceError("workspace_not_directory", f"{path} exists and is not a directory")
        if created_now:
            log("workspace_created", issue_id=issue.id, issue_identifier=issue.identifier,
                workspace=str(path))
            try:
                self.run_hook("after_create", issue, path)
            except HookError:
                shutil.rmtree(path, ignore_errors=True)
                raise
        return Workspace(path=path, workspace_key=key, created_now=created_now)

    def run_hook(self, name: str, issue: Issue, workspace: Path) -> None:
        """`bash -lc <script>` in the workspace with hook_env(); raises HookError."""
        script = getattr(self.hooks, name)
        if not script:
            return
        timeout_s = self.hooks.timeout_ms / 1000
        fields = dict(hook=name, issue_id=issue.id, issue_identifier=issue.identifier)
        log("hook_started", **fields)
        with tempfile.TemporaryFile() as out:
            try:
                proc = self._run(["bash", "-lc", script], cwd=workspace,
                                 env=self.hook_env(issue, workspace), stdin=subprocess.DEVNULL,
                                 stdout=out, stderr=subprocess.STDOUT, timeout=timeout_s)
                code = proc.returncode
            except subprocess.TimeoutExpired:
                code = None
            except OSError as e:
                log("hook_failed", level="error", error=str(e), **fields)
                raise HookError("hook_failed", f"{name}: {e}", hook=name) from e
            out.seek(0)
            output = _truncate(out.read().decode("utf-8", errors="replace"))
        if code is None:
            log("hook_timeout", level="error", timeout_ms=self.hooks.timeout_ms,
                output=output, **fields)
            raise HookError("hook_timeout", f"{name} timed out after {self.hooks.timeout_ms} ms",
                            hook=name)
        if code != 0:
            log("hook_failed", level="error", exit_code=code, output=output, **fields)
            raise HookError("hook_failed", f"{name} exited {code}", hook=name, exit_code=code)
        log("hook_completed", output=output, **fields)

    def run_hook_best_effort(self, name: str, issue: Issue, workspace: Path) -> bool:
        """Run a hook; log and swallow failure. Returns True on success or no hook."""
        try:
            self.run_hook(name, issue, workspace)
            return True
        except HookError as e:
            log("hook_ignored", level="warning", hook=name, issue_id=issue.id,
                issue_identifier=issue.identifier, error=str(e))
            return False

    def remove(self, issue_or_identifier: Issue | str) -> bool:
        """before_remove, then delete. Hook failure: keep the workspace, return False
        (deviation 2)."""
        issue = (issue_or_identifier if isinstance(issue_or_identifier, Issue)
                 else _bare_issue(issue_or_identifier))
        path = self.path_for(issue.identifier)
        if path.is_dir():
            try:
                self.run_hook("before_remove", issue, path)
            except HookError as e:
                log("workspace_remove_skipped", level="warning", issue_id=issue.id,
                    issue_identifier=issue.identifier, workspace=str(path), error=str(e))
                return False
            if path.is_dir():  # before_remove (git worktree remove) may have deleted it
                shutil.rmtree(path)
            log("workspace_removed", issue_id=issue.id, issue_identifier=issue.identifier,
                workspace=str(path))
        elif path.exists() or path.is_symlink():
            path.unlink()
        self.threads.delete(issue.identifier)
        return True

    def repo_for(self, issue: Issue) -> str:
        """Path from the first repo:<name> label found in repos, else ''."""
        by_lower = {k.lower(): v for k, v in self.repos.items()}
        for label in issue.labels:
            if label.lower().startswith("repo:"):
                name = label[len("repo:"):].strip()
                path = self.repos.get(name) or by_lower.get(name.lower())
                if path is not None:
                    return str(path)
        return ""

    def hook_env(self, issue: Issue, workspace: Path) -> dict[str, str]:
        env = {k: v for k, v in os.environ.items() if k not in self.strip_env}
        env.update(
            SYMPHONY_WORKSPACE=str(workspace),
            SYMPHONY_ISSUE_IDENTIFIER=issue.identifier,
            SYMPHONY_ISSUE_ID=issue.id,
            SYMPHONY_ISSUE_LABELS=",".join(issue.labels),
            SYMPHONY_REPO=self.repo_for(issue),
        )
        return env

    def assert_agent_cwd(self, workspace: Path, cwd: Path) -> None:
        """§9.5 invariants 1-2. Raises WorkspaceError."""
        ws = self._check_inside_root(Path(workspace))
        if Path(cwd).resolve() != ws:
            raise WorkspaceError("invalid_workspace_cwd", f"cwd {cwd} is not workspace {ws}")


def cleanup_terminal_workspaces(manager: WorkspaceManager, tracker,
                                terminal_states: Sequence[str]) -> list[Issue]:
    """§8.6 startup cleanup. Returns issues whose removal was skipped (deviation 2).
    A tracker failure is logged and cleanup is skipped."""
    try:
        issues = tracker.fetch_issues_by_states(list(terminal_states))
    except SymphonyError as e:
        log("startup_cleanup_failed", level="warning", error=str(e))
        return []
    skipped = []
    for issue in issues:
        try:
            if not manager.remove(issue):
                skipped.append(issue)
        except (SymphonyError, OSError) as e:
            log("workspace_remove_failed", level="warning", issue_id=issue.id,
                issue_identifier=issue.identifier, error=str(e))
            skipped.append(issue)
    return skipped


def _dt(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


class ThreadStore:
    """ThreadRecord at <root>/.symphony/<workspace_key>/thread.json (mode 0600, atomic writes)."""

    def __init__(self, root: Path):
        self.root = root

    def dir_for(self, identifier: str) -> Path:
        return Path(self.root).expanduser() / STATE_DIRNAME / workspace_key(identifier)

    def _file(self, identifier: str) -> Path:
        return self.dir_for(identifier) / "thread.json"

    def read(self, identifier: str) -> ThreadRecord | None:
        path = self._file(identifier)
        try:
            data = json.loads(path.read_text())
            return ThreadRecord(
                thread_id=data["thread_id"],
                created_at=datetime.fromisoformat(data["created_at"]),
                last_turn_id=data.get("last_turn_id"),
                last_turn_started_at=_dt(data.get("last_turn_started_at")),
                total_tokens=int(data.get("total_tokens") or 0),
            )
        except FileNotFoundError:
            return None
        except (OSError, ValueError, KeyError, TypeError) as e:
            log("thread_record_corrupt", level="warning", issue_identifier=identifier,
                path=str(path), error=str(e))
            return None

    def write(self, identifier: str, rec: ThreadRecord) -> None:
        directory = self.dir_for(identifier)
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        data = {
            "thread_id": rec.thread_id,
            "created_at": rec.created_at.isoformat(),
            "last_turn_id": rec.last_turn_id,
            "last_turn_started_at": (rec.last_turn_started_at.isoformat()
                                     if rec.last_turn_started_at else None),
            "total_tokens": rec.total_tokens,
        }
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".thread.", suffix=".tmp")
        try:
            os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(data, f, indent=2)
                f.write("\n")
            os.replace(tmp, directory / "thread.json")
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

    def delete(self, identifier: str) -> None:
        shutil.rmtree(self.dir_for(identifier), ignore_errors=True)

    def turn_started_at(self, identifier: str) -> datetime | None:
        rec = self.read(identifier)
        return rec.last_turn_started_at if rec else None

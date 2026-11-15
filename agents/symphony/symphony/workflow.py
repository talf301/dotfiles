"""WORKFLOW.md discovery, loading, live reload and strict prompt rendering (SPEC §5, §6.2, §12)."""

from __future__ import annotations

import hashlib
import os
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import yaml
from liquid import Environment, StrictUndefined
from liquid.exceptions import Error as LiquidError
from liquid.exceptions import LiquidSyntaxError, NoSuchFilterFunc

from .errors import SymphonyError, WorkflowError
from .models import Issue

if TYPE_CHECKING:
    from .config import ServiceConfig

DEFAULT_PROMPT = "You are working on an issue from the configured tracker."

DEFAULT_CONTINUATION = """\
Continue work on Linear issue {{ issue.identifier }} in this same thread.
Re-read the issue and any comments newer than your last turn with the Linear MCP,
then continue following the workflow rules you were given at the start of this thread.
Park the ticket (add the `waiting` label, or move it to In Review) as those rules describe when
you have nothing to do.
"""

_LIQUID = Environment(undefined=StrictUndefined, strict_filters=True)


@dataclass(frozen=True)
class WorkflowDefinition:
    config: dict
    prompt_template: str
    path: Path


def resolve_workflow_path(explicit: str | Path | None, cwd: Path) -> Path:
    """Explicit path wins; else cwd / "WORKFLOW.md". Does not check existence."""
    if explicit is not None and str(explicit) != "":
        p = Path(explicit).expanduser()
        return p if p.is_absolute() else Path(cwd) / p
    return Path(cwd) / "WORKFLOW.md"


def _read(path: Path) -> bytes:
    try:
        return path.read_bytes()
    except OSError as e:
        raise WorkflowError("missing_workflow_file",
                            f"cannot read {path}: {e.strerror or e}") from None


def _parse(path: Path, raw: bytes) -> WorkflowDefinition:
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        raise WorkflowError("workflow_parse_error", f"{path} is not UTF-8") from None
    lines = text.splitlines()
    config: object = {}
    body_lines = lines
    if lines and lines[0].rstrip() == "---":
        end = next((i for i in range(1, len(lines)) if lines[i].rstrip() == "---"), None)
        if end is None:
            raise WorkflowError("workflow_parse_error", f"{path}: front matter has no closing ---")
        try:
            config = yaml.safe_load("\n".join(lines[1:end]))
        except yaml.YAMLError as e:
            raise WorkflowError("workflow_parse_error", f"{path}: invalid YAML: {e}") from None
        if config is None:
            config = {}
        body_lines = lines[end + 1:]
    if not isinstance(config, dict):
        raise WorkflowError("workflow_front_matter_not_a_map",
                            f"{path}: front matter is a {type(config).__name__}, not a map")
    return WorkflowDefinition(config=config, prompt_template="\n".join(body_lines).strip(),
                              path=Path(os.path.abspath(path)))


def load_workflow(path: Path) -> WorkflowDefinition:
    """Split YAML front matter and body. Raises WorkflowError(missing_workflow_file |
    workflow_parse_error | workflow_front_matter_not_a_map)."""
    path = Path(path)
    return _parse(path, _read(path))


def render_prompt(template: str, issue: Issue, attempt: int | None, *,
                  retry_kind: str | None = None, **extra) -> str:
    """Strict Liquid render with `issue`, `attempt`, `retry_kind` and extra variables.
    `retry_kind` is always defined (None on a first run) so templates may test it.
    Raises WorkflowError(template_parse_error | template_render_error)."""
    if not template or not template.strip():
        return DEFAULT_PROMPT
    try:
        tmpl = _LIQUID.from_string(template)
    except NoSuchFilterFunc as e:
        raise WorkflowError("template_render_error", str(e)) from None
    except LiquidSyntaxError as e:
        raise WorkflowError("template_parse_error", str(e)) from None
    except LiquidError as e:
        raise WorkflowError("template_parse_error", str(e)) from None
    variables = {"issue": issue.to_template(), "attempt": attempt, "retry_kind": retry_kind,
                 **extra}
    try:
        return tmpl.render(**variables)
    except LiquidError as e:
        raise WorkflowError("template_render_error", str(e)) from None


def _log(event: str, level: str, **fields) -> None:
    try:
        from .log import log
        log(event, level, **fields)
    except Exception:  # logging must never break reload
        pass


class WorkflowStore:
    """Holds the last known good (definition, config); reloads on file change.

    Change detection: (mtime_ns, size) from stat; on a change the file is re-read and
    hashed, and only a different hash is parsed. A failed file is not re-parsed or re-logged
    until its stat changes again.
    """

    def __init__(self, path: Path, env: Mapping[str, str] = os.environ):
        self.path = Path(path)
        self.env = env
        self.last_error: SymphonyError | None = None
        self._current: tuple[WorkflowDefinition, ServiceConfig] | None = None
        self._good_hash: str | None = None
        self._stamp: tuple[int, int] | None = None

    def _stat(self) -> tuple[int, int] | None:
        try:
            st = self.path.stat()
        except OSError:
            return None
        return (st.st_mtime_ns, st.st_size)

    def _build(self, raw: bytes) -> tuple[WorkflowDefinition, ServiceConfig]:
        from .config import build_config
        defn = _parse(self.path, raw)
        return defn, build_config(defn, self.env)

    def load_initial(self) -> tuple[WorkflowDefinition, "ServiceConfig"]:
        """Load and build config; raises on error (startup)."""
        stamp = self._stat()
        raw = _read(self.path)
        self._current = self._build(raw)
        self._good_hash = hashlib.sha256(raw).hexdigest()
        self._stamp = stamp
        self.last_error = None
        return self._current

    def reload_if_changed(self) -> bool:
        """Apply a changed valid file and return True. Invalid: keep last good, set last_error,
        log, return False. Never raises."""
        try:
            stamp = self._stat()
            if stamp is not None and stamp == self._stamp:
                return False
            if stamp is None and self._stamp == (-1, -1):
                return False  # still missing; already reported
            self._stamp = stamp if stamp is not None else (-1, -1)
            raw = _read(self.path)
            digest = hashlib.sha256(raw).hexdigest()
            if digest == self._good_hash:
                self.last_error = None
                return False
            current = self._build(raw)
        except SymphonyError as e:
            self.last_error = e
            _log("workflow_reload", "error", outcome="failed", path=str(self.path),
                 error=str(e), category=e.category)
            return False
        except Exception as e:  # never crash the run loop
            self.last_error = WorkflowError("workflow_parse_error", f"{type(e).__name__}: {e}")
            _log("workflow_reload", "error", outcome="failed", path=str(self.path),
                 error=str(self.last_error), category=self.last_error.category)
            return False
        self._current = current
        self._good_hash = digest
        self.last_error = None
        _log("workflow_reload", "info", outcome="applied", path=str(self.path))
        return True

    @property
    def current(self) -> tuple[WorkflowDefinition, "ServiceConfig"]:
        if self._current is None:
            raise WorkflowError("missing_workflow_file", f"{self.path} not loaded yet")
        return self._current

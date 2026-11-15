"""macOS notifications for In Review, Done, repeated failure and skipped removal."""

from __future__ import annotations

import subprocess
from typing import Protocol

from .log import log

_SCRIPT = ("on run argv\ndisplay notification (item 2 of argv) with title (item 1 of argv)\n"
           "end run")


class Notifier(Protocol):
    def notify(self, title: str, message: str) -> None:
        """Never raises."""
        ...


class MacNotifier:
    """osascript `display notification` (ported from run-ticket), 10 s timeout, errors logged."""

    def __init__(self, run=subprocess.run):
        self._run = run

    def notify(self, title: str, message: str) -> None:
        try:
            r = self._run(["osascript", "-e", _SCRIPT, title, message],
                          capture_output=True, timeout=10)
            if getattr(r, "returncode", 0) != 0:
                log("notify_failed", "warning", title=title, exit_code=r.returncode)
        except Exception as e:  # never raise
            log("notify_failed", "warning", title=title, error=str(e))


class NullNotifier:
    def notify(self, title: str, message: str) -> None:
        pass

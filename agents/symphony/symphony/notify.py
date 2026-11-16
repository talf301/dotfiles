"""macOS notifications for In Review, Done, repeated failure and skipped removal."""

from __future__ import annotations

import json
import subprocess
import urllib.request
from pathlib import Path
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


NTFY_CONFIG = Path.home() / ".config" / "symphony" / "ntfy.json"


class NtfyNotifier:
    """Phone push through ntfy. Config (outside the repo, mode 0600) at NTFY_CONFIG:
    {"topic": "<random>", "server": "https://ntfy.sh",
     "issue_url": "https://linear.app/<workspace>/issue/", "include_detail": false}
    By default only "<ID>: <reason>" and a link leave the machine, not titles or comment text."""

    def __init__(self, cfg: dict, post=None):
        self.topic = cfg["topic"]
        self.server = cfg.get("server", "https://ntfy.sh").rstrip("/")
        self.issue_url = cfg.get("issue_url", "")
        self.include_detail = bool(cfg.get("include_detail", False))
        self._post = post or _http_post

    @classmethod
    def from_file(cls, path: Path = NTFY_CONFIG) -> "NtfyNotifier | None":
        try:
            cfg = json.loads(path.read_text())
            return cls(cfg) if cfg.get("topic") else None
        except FileNotFoundError:
            return None
        except Exception as e:  # bad config: log, run without phone pushes
            log("notify_config_invalid", "warning", path=str(path), error=type(e).__name__)
            return None

    def notify(self, title: str, message: str) -> None:
        identifier = title.split(":", 1)[0].strip()
        headers = {"Title": title, "Tags": "robot"}
        if self.issue_url and identifier:
            headers["Click"] = self.issue_url + identifier
        body = message if self.include_detail else title
        try:
            self._post(f"{self.server}/{self.topic}", body.encode(), headers)
        except Exception as e:  # never raise
            log("notify_failed", "warning", title=title, channel="ntfy", error=type(e).__name__)


class MultiNotifier:
    def __init__(self, *notifiers: Notifier):
        self.notifiers = [n for n in notifiers if n is not None]

    def notify(self, title: str, message: str) -> None:
        for n in self.notifiers:
            n.notify(title, message)


def default_notifier() -> Notifier:
    return MultiNotifier(MacNotifier(), NtfyNotifier.from_file())


def _http_post(url: str, data: bytes, headers: dict) -> None:
    req = urllib.request.Request(url, data=data, headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=10) as r:
        r.read()

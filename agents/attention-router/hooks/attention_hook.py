#!/usr/bin/env python3
"""Claude Code hook: record attention events and gate Stop on declared artifacts.

Events are appended to ~/.local/state/attention-router/events.jsonl. The hook
never answers permission prompts and never records prompt text.

Usage:
  attention_hook.py            # hook mode: reads one hook JSON object on stdin
  attention_hook.py report     # summarize recorded events
"""

import json
import os
import sys
import time
from pathlib import Path

STATE = Path(os.environ.get("ATTENTION_STATE_DIR", Path.home() / ".local/state/attention-router"))
EVENTS = STATE / "events.jsonl"
EXPECT = STATE / "expect"


def _state_dirs():
    for d in (STATE, EXPECT):
        d.mkdir(mode=0o700, parents=True, exist_ok=True)


def _log(record):
    _state_dirs()
    fd = os.open(EVENTS, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def _expect_file(session_id):
    safe = "".join(c for c in session_id if c.isalnum() or c in "-_")
    return EXPECT / f"{safe}.json"


def _missing_artifacts(session_id, cwd):
    path = _expect_file(session_id)
    if not session_id or not path.is_file():
        return None, []
    try:
        declared = json.loads(path.read_text())
        paths = declared.get("paths", []) if isinstance(declared, dict) else []
    except (OSError, ValueError):
        return path, ["<unreadable expectation file>"]
    missing = []
    for p in paths:
        full = Path(p) if os.path.isabs(p) else Path(cwd or ".") / p
        if not full.exists() or (full.is_file() and full.stat().st_size == 0):
            missing.append(str(full))
    return path, missing


def handle(payload):
    event = payload.get("hook_event_name", "")
    session_id = payload.get("session_id", "")
    cwd = payload.get("cwd", "")
    base = {"ts": time.time(), "event": event, "session_id": session_id, "cwd": cwd}

    if event == "Notification":
        _log({**base, "notification_type": payload.get("notification_type", ""),
              "message": payload.get("message", "")[:500]})
        return None

    if event == "PermissionRequest":
        _log({**base, "tool_name": payload.get("tool_name", "")})
        return None  # no decision: the normal permission prompt proceeds

    if event == "UserPromptSubmit":
        _log(base)  # timestamp only; prompt text is not stored
        return None

    if event == "SessionStart":
        _log({**base, "source": payload.get("source", "")})
        if not session_id:
            return None
        ctx = (
            "Attention hooks are active. If this session must produce files before it "
            f"is done, declare them as JSON {{\"paths\": [...]}} in {_expect_file(session_id)}; "
            "Claude will not be allowed to stop while a declared path is missing or empty. "
            "Route routine decisions through attention-router per the work skill before asking Tal."
        )
        return {"hookSpecificOutput": {"hookEventName": "SessionStart", "additionalContext": ctx}}

    if event == "Stop":
        path, missing = _missing_artifacts(session_id, cwd)
        active = bool(payload.get("stop_hook_active"))
        _log({**base, "declared": path is not None, "missing": missing, "stop_hook_active": active})
        if missing and not active:
            return {"decision": "block",
                    "reason": "Declared artifacts are missing or empty: " + ", ".join(missing)
                    + f". Produce them, or edit {path} if the declaration is wrong."}
        return None

    return None


def report():
    if not EVENTS.is_file():
        print("No events recorded.")
        return
    counts, sessions, waits = {}, set(), []
    pending = {}
    for line in EVENTS.read_text().splitlines():
        try:
            r = json.loads(line)
        except ValueError:
            continue
        key = r["event"] + (":" + r["notification_type"] if r.get("notification_type") else "")
        counts[key] = counts.get(key, 0) + 1
        sid = r.get("session_id")
        sessions.add(sid)
        if r["event"] in ("Notification", "PermissionRequest"):
            pending.setdefault(sid, r["ts"])
        elif r["event"] == "UserPromptSubmit" and sid in pending:
            waits.append(r["ts"] - pending.pop(sid))
    print(f"Sessions: {len(sessions)}")
    for k in sorted(counts):
        print(f"  {k}: {counts[k]}")
    if waits:
        waits.sort()
        print(f"Waits answered by a prompt: {len(waits)}, total {sum(waits)/60:.1f} min, "
              f"median {waits[len(waits)//2]/60:.1f} min")


def main():
    if sys.argv[1:] == ["report"]:
        report()
        return 0
    try:
        payload = json.load(sys.stdin)
        out = handle(payload)
    except Exception as e:  # never break the session because logging failed
        print(f"attention_hook: {e}", file=sys.stderr)
        return 0
    if out:
        print(json.dumps(out))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

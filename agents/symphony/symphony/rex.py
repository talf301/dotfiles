"""Hidden Rex tab per ticket running `codex resume <thread>` (ported from run-ticket).

Tab ids (session, window, thread) are kept in <store_dir>/rex.json, next to thread.json.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path


def run_rex(*args: str) -> dict | str:
    """`rex <args>` with a 20 s timeout; parsed JSON when the output is an object."""
    out = subprocess.run(["rex", *args], capture_output=True, text=True, timeout=20,
                         check=True).stdout
    return json.loads(out) if out.strip().startswith("{") else out


def _find_key(obj, key):
    if isinstance(obj, dict):
        if obj.get(key):
            return obj[key]
        obj = list(obj.values())
    if isinstance(obj, list):
        for v in obj:
            found = _find_key(v, key)
            if found:
                return found
    return None


def _read(store_dir: Path) -> dict:
    try:
        data = json.loads((Path(store_dir) / "rex.json").read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def _write(store_dir: Path, data: dict) -> None:
    store_dir = Path(store_dir)
    store_dir.mkdir(parents=True, exist_ok=True)
    tmp = store_dir / "rex.json.tmp"
    tmp.write_text(json.dumps(data) + "\n")
    os.replace(tmp, store_dir / "rex.json")


def _session_id(label: str, rex) -> str | None:
    out = rex("ls", "--json")
    sessions = out.get("sessions", []) if isinstance(out, dict) else []
    return next((s.get("session_id") for s in sessions if s.get("label") == label), None)


def ensure_tab(identifier: str, cwd: Path, thread_id: str, *, session_label: str,
               store_dir: Path, rex=run_rex) -> None:
    """Create the ticket's tab in the background session if it is missing or shows another
    thread. The tab is not focused."""
    state = _read(store_dir)
    cmd = ["--keep-open", "-c", str(cwd), "--json", "--", "codex", "resume", thread_id]
    session = _session_id(session_label, rex)
    window = state.get("window_id")
    if (session and window and state.get("session_id") == session
            and state.get("thread_id") == thread_id
            and window in json.dumps(rex("session", "inspect", session, "--json"))):
        return
    if session:
        out = rex("window", "new", "-s", session, identifier, "--focus=false", *cmd)
    else:
        out = rex("new", session_label, "--window", identifier, *cmd)
        session = _find_key(out, "session_id")
    _write(store_dir, {"session_id": session, "window_id": _find_key(out, "window_id"),
                       "thread_id": thread_id})


def show_tab(identifier: str, *, session_label: str, store_dir: Path, rex=run_rex) -> None:
    """Bring the ticket's tab to the front in a connected Rex app. Raises RuntimeError when
    no tab is recorded or no Rex app is connected."""
    state = _read(store_dir)
    if not state.get("session_id"):
        raise RuntimeError(f"{identifier} has no Rex tab")
    out = rex("client", "ls", "--json")
    clients = out.get("clients", []) if isinstance(out, dict) else []
    apps = [c["client_id"] for c in clients
            if (c.get("info") or {}).get("kind") == "app"
            and c.get("connection_state") == "connected"]
    if not apps:
        raise RuntimeError(f"No Rex app is connected. The tab is in Rex session "
                           f"{session_label}.")
    rex("-C", apps[0], "do", "session.select", f"session_id={state['session_id']}",
        *([f"window_id={state['window_id']}"] if state.get("window_id") else []))

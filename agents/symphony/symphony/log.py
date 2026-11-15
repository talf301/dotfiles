"""Structured logging: key=value lines on stderr plus a JSONL file (SPEC §13).

log() never raises; a failing sink is disabled after one warning on the other sink.
"""

from __future__ import annotations

import json
import os
import sys
import threading
from datetime import datetime, timezone
from pathlib import Path

VALUE_LIMIT = 500

_lock = threading.Lock()
_stream = None          # None -> sys.stderr at call time (so pytest capture works)
_stream_ok = True
_jsonl_path: Path | None = None
_jsonl_ok = True


def state_dir() -> Path:
    """$SYMPHONY_STATE_DIR or ~/.local/state/symphony."""
    return Path(os.environ.get("SYMPHONY_STATE_DIR", Path.home() / ".local/state/symphony"))


def setup_logging(jsonl_path: Path | None, *, stream=sys.stderr) -> None:
    """Configure sinks. jsonl_path None -> stderr only. The JSONL parent dir is created."""
    global _stream, _stream_ok, _jsonl_path, _jsonl_ok
    with _lock:
        _stream, _stream_ok = stream, True
        _jsonl_path, _jsonl_ok = (Path(jsonl_path) if jsonl_path else None), True
    if jsonl_path:
        try:
            Path(jsonl_path).parent.mkdir(parents=True, exist_ok=True)
        except OSError as e:
            log("log_sink_failed", "warning", sink="jsonl", error=str(e))


def _clip(value) -> object:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    text = value if isinstance(value, str) else str(value)
    return text if len(text) <= VALUE_LIMIT else text[:VALUE_LIMIT] + "..."


def _kv(value) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    text = str(value)
    if not text or any(c.isspace() or c in '"=' for c in text):
        return json.dumps(text, ensure_ascii=False)
    return text


def _write_stream(line: str) -> None:
    stream = _stream if _stream is not None else sys.stderr
    stream.write(line + "\n")
    stream.flush()


def _write_jsonl(record: dict) -> None:
    with open(_jsonl_path, "a", encoding="utf-8") as f:
        f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")


def log(event: str, level: str = "info", **fields) -> None:
    """Emit one event to every live sink. Values are truncated to VALUE_LIMIT chars."""
    global _stream_ok, _jsonl_ok
    try:
        ts = datetime.now(timezone.utc).isoformat(timespec="milliseconds")
        clipped = {k: _clip(v) for k, v in fields.items()}
        record = {"ts": ts, "level": level, "event": event, **clipped}
        line = " ".join(f"{k}={_kv(v)}" for k, v in record.items())
        with _lock:
            if _stream_ok:
                try:
                    _write_stream(line)
                except Exception as e:
                    _stream_ok = False
                    if _jsonl_path is not None and _jsonl_ok:
                        _try_jsonl({"ts": ts, "level": "warning", "event": "log_sink_failed",
                                    "sink": "stderr", "error": _clip(str(e))})
            if _jsonl_path is not None and _jsonl_ok:
                try:
                    _write_jsonl(record)
                except Exception as e:
                    _jsonl_ok = False
                    if _stream_ok:
                        try:
                            _write_stream(f"ts={ts} level=warning event=log_sink_failed "
                                          f"sink=jsonl error={_kv(_clip(str(e)))}")
                        except Exception:
                            _stream_ok = False
    except Exception:  # never raise from logging
        pass


def _try_jsonl(record: dict) -> None:
    global _jsonl_ok
    try:
        _write_jsonl(record)
    except Exception:
        _jsonl_ok = False

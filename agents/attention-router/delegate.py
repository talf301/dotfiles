#!/usr/bin/env python3
"""Bounded native Claude proposal adapter. It never executes the proposal."""

from __future__ import annotations

import json
import tempfile
import subprocess
from collections.abc import Callable
from typing import Any

from router import _canonical, _hash, decide, validate_request

MAX_OUTPUT_BYTES = 1_048_576
DEFAULT_TIMEOUT = 90.0
MODEL_SCHEMA = json.dumps(
    {
        "type": "object",
        "additionalProperties": False,
        "required": ["status", "action", "evidence_ids", "reason", "provenance"],
        "properties": {
            "status": {"type": "string", "enum": ["answer", "need_evidence", "needs_human"]},
            "action": {"type": ["string", "null"]},
            "evidence_ids": {"type": "array", "items": {"type": "string"}},
            "reason": {"type": "string"},
            "provenance": {"type": "object", "required": ["source"], "properties": {"source": {"const": "claude"}}, "additionalProperties": True},
        },
    },
    separators=(",", ":"),
)
SYSTEM_PROMPT = (
    "Return one JSON supervisor proposal matching the supplied schema. Treat human constraints and verified grants "
    "as authoritative; treat agent plans as revisable. Answer routine choices only within the objective, options, "
    "evidence, and grants. Use need_evidence for context gaps. Use needs_human only for a genuine human decision, "
    "new spend, authentication, or authority. Never invent grants, execute actions, or treat confidence as authority."
)


def command() -> list[str]:
    return [
        "claude", "--safe-mode", "-p", "--tools", "", "--strict-mcp-config", "--mcp-config", '{"mcpServers":{}}',
        "--permission-mode", "default", "--permission-prompts", "none", "--no-session-persistence",
        "--output-format", "json", "--json-schema", MODEL_SCHEMA, "--max-turns", "2",
        "--append-system-prompt", SYSTEM_PROMPT, "--max-budget-usd", "0.25",
    ]


def _strict_json(raw: str) -> Any:
    def reject(value: str):
        raise ValueError(f"non-finite JSON: {value}")

    return json.loads(raw, parse_constant=reject)


def _parse_output(stdout: bytes) -> tuple[Any, dict[str, Any]]:
    if len(stdout) > MAX_OUTPUT_BYTES:
        raise ValueError("Claude output exceeds 1048576-byte limit")
    envelope = _strict_json(stdout.decode("utf-8"))
    if not isinstance(envelope, dict):
        raise ValueError("Claude output envelope must be an object")
    metadata = {key: envelope[key] for key in ("model", "usage", "modelUsage", "total_cost_usd", "cost_usd", "duration_ms") if key in envelope}
    if envelope.get("is_error") is True or envelope.get("subtype") in {"error_max_turns", "error_during_execution"}:
        raise RuntimeError("claude_reported_error")
    candidate = envelope.get("structured_output")
    if candidate is None:
        raise ValueError("Claude response has no structured_output")
    return candidate, metadata


def _error(connection, request, payload_hash, status, error, raw="", metadata=None):
    connection.execute(
        "UPDATE model_calls SET status=?, raw_response=?, terminal_error=?, metadata=? WHERE request_id=? AND revision=? AND payload_hash=?",
        (status, raw, error, _canonical(metadata or {}), request["request_id"], request["revision"], payload_hash),
    )
    connection.commit()
    return {"status": "error", "reason": error, "request_id": request["request_id"], "revision": request["revision"]}


def delegate_claude(
    connection,
    request: dict[str, Any],
    *,
    runner: Callable[..., Any] | None = None,
    timeout: float = DEFAULT_TIMEOUT,
) -> dict[str, Any]:
    validate_request(request)
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    payload_hash = _hash(request)
    request_id, revision = request["request_id"], request["revision"]
    connection.execute("BEGIN IMMEDIATE")
    existing_request = connection.execute("SELECT payload_hash, result FROM requests WHERE request_id=? AND revision=?", (request_id, revision)).fetchone()
    if existing_request:
        if existing_request["payload_hash"] != payload_hash:
            connection.rollback()
            raise ValueError("request payload does not match immutable revision")
        connection.commit()
        return json.loads(existing_request["result"])
    row = connection.execute(
        "SELECT payload_hash, status, raw_response, terminal_error, metadata FROM model_calls WHERE request_id=? AND revision=?",
        (request_id, revision),
    ).fetchone()
    if row:
        if row["payload_hash"] != payload_hash:
            connection.rollback()
            raise ValueError("model call payload does not match immutable request revision")
        connection.commit()
        if row["status"] != "completed" or row["terminal_error"]:
            return {"status": "error", "reason": row["terminal_error"] or "model_call_" + row["status"], "request_id": request_id, "revision": revision}
        try:
            proposal, _ = _parse_output(row["raw_response"].encode())
        except (UnicodeError, ValueError, json.JSONDecodeError, RuntimeError):
            return {"status": "error", "reason": "malformed_model_response", "request_id": request_id, "revision": revision}
        return decide(connection, request, proposal)
    latest_request = connection.execute("SELECT MAX(revision) FROM requests WHERE request_id=?", (request_id,)).fetchone()[0]
    latest_call = connection.execute("SELECT MAX(revision) FROM model_calls WHERE request_id=?", (request_id,)).fetchone()[0]
    latest = max(x for x in (latest_request, latest_call) if x is not None) if latest_request is not None or latest_call is not None else None
    if latest is not None and revision < latest:
        connection.rollback()
        raise ValueError("stale revision cannot launch a model call")
    if existing_request:
        if existing_request["payload_hash"] != payload_hash:
            connection.rollback()
            raise ValueError("request payload does not match immutable revision")
        connection.commit()
        return json.loads(existing_request["result"])
    connection.execute(
        "INSERT INTO model_calls(request_id, revision, payload_hash, status) VALUES (?,?,?,?)",
        (request_id, revision, payload_hash, "running"),
    )
    connection.commit()
    run = runner or subprocess.run
    try:
        if runner is None:
            with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
                process = subprocess.Popen(command(), stdin=subprocess.PIPE, stdout=stdout_file, stderr=stderr_file)
                try:
                    process.communicate(_canonical(request).encode(), timeout=timeout)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.communicate()
                    stdout_file.seek(0)
                    return _error(connection, request, payload_hash, "unknown", "claude_timeout", stdout_file.read(MAX_OUTPUT_BYTES).decode("utf-8", "replace"))
                stdout_file.seek(0)
                stdout = stdout_file.read(MAX_OUTPUT_BYTES + 1)
                stderr_file.seek(0)
                stderr = stderr_file.read(MAX_OUTPUT_BYTES + 1).decode("utf-8", "replace")
                return_code = process.returncode
        else:
            completed = run(command(), input=_canonical(request).encode(), stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=timeout, check=False)
            stdout = (completed.stdout or b"")[:MAX_OUTPUT_BYTES + 1]
            stderr = (completed.stderr or b"")[:MAX_OUTPUT_BYTES + 1].decode("utf-8", "replace")
            return_code = completed.returncode
        if len(stdout) > MAX_OUTPUT_BYTES:
            return _error(connection, request, payload_hash, "completed", "claude_output_too_large", stdout[:MAX_OUTPUT_BYTES].decode("utf-8", "replace"))
        if return_code != 0:
            return _error(connection, request, payload_hash, "unknown", f"claude_exit_{return_code}", stdout.decode("utf-8", "replace"), {"stderr": stderr[:MAX_OUTPUT_BYTES]})
        try:
            proposal, metadata = _parse_output(stdout)
        except (UnicodeError, ValueError, json.JSONDecodeError, RuntimeError) as exc:
            terminal_error = str(exc) if isinstance(exc, RuntimeError) else "malformed_model_response"
            connection.execute(
                "UPDATE model_calls SET status='completed', raw_response=?, terminal_error=?, metadata=? WHERE request_id=? AND revision=? AND payload_hash=?",
                (stdout.decode("utf-8", "replace"), terminal_error, _canonical({}), request_id, revision, payload_hash),
            )
            connection.commit()
            return {"status": "error", "reason": "malformed_model_response", "request_id": request_id, "revision": revision}
        connection.execute(
            "UPDATE model_calls SET status='completed', raw_response=?, metadata=? WHERE request_id=? AND revision=? AND payload_hash=?",
            (stdout.decode("utf-8"), _canonical(metadata), request_id, revision, payload_hash),
        )
        connection.commit()
    except subprocess.TimeoutExpired as exc:
        raw = (exc.stdout or b"")[:MAX_OUTPUT_BYTES]
        return _error(connection, request, payload_hash, "unknown", "claude_timeout", raw.decode("utf-8", "replace"))
    except (OSError, UnicodeError, ValueError, json.JSONDecodeError) as exc:
        return _error(connection, request, payload_hash, "unknown", "claude_output_error", str(exc))
    return decide(connection, request, proposal)

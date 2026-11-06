#!/usr/bin/env python3
"""Offline request/result ledger for bounded supervisor proposals."""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from pathlib import Path
from typing import Any

STATUSES = {"answer", "need_evidence", "needs_human"}
REQUEST_FIELDS = {"request_id", "revision", "session_id", "task_id", "objective", "question", "options", "evidence", "grants"}
PROPOSAL_FIELDS = {"status", "action", "evidence_ids", "reason", "provenance", "supervisor_authored"}


def _canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _hash(value: Any) -> str:
    return hashlib.sha256(_canonical(value).encode()).hexdigest()


def _fail(message: str) -> ValueError:
    return ValueError(message)


def validate_request(request: dict[str, Any]) -> None:
    if not isinstance(request, dict) or set(request) != REQUEST_FIELDS:
        raise _fail("request fields must be exactly request_id, revision, session_id, task_id, objective, question, options, evidence, grants")
    if not isinstance(request["request_id"], str) or not request["request_id"]:
        raise _fail("request_id must be a nonempty string")
    if type(request["revision"]) is not int or request["revision"] < 0:
        raise _fail("revision must be a nonnegative integer")
    for field in ("session_id", "task_id", "objective", "question"):
        if not isinstance(request[field], str) or not request[field].strip():
            raise _fail(f"{field} must be a nonempty string")
    options = request["options"]
    if not isinstance(options, list) or not options or any(not isinstance(x, str) or not x for x in options):
        raise _fail("options must be a nonempty list of strings")
    if len(set(options)) != len(options):
        raise _fail("options must be unique")
    for field in ("evidence", "grants"):
        if not isinstance(request[field], list) or any(not isinstance(x, dict) for x in request[field]):
            raise _fail(f"{field} must be a list of objects")
    for item in request["evidence"]:
        if not isinstance(item.get("id"), str) or not item["id"]:
            raise _fail("each evidence item needs an id")
        if not isinstance(item.get("provenance"), dict) or not item["provenance"]:
            raise _fail("each evidence item needs provenance")
    evidence_ids = [item["id"] for item in request["evidence"]]
    if len(set(evidence_ids)) != len(evidence_ids):
        raise _fail("evidence ids must be unique")
    for grant in request["grants"]:
        if not isinstance(grant.get("action"), str) or grant["action"] not in options:
            raise _fail("grant action must be one of options")
        if grant.get("actor") != "human" or grant.get("verified") is not True:
            raise _fail("grants must be verified human grants")


def _proposal_or_fail_closed(request: dict[str, Any], proposal: Any) -> dict[str, Any]:
    if proposal is None:
        return {"status": "need_evidence", "reason": "supervisor_proposal_required", "supervisor_authored": True}
    if not isinstance(proposal, dict) or set(proposal) - PROPOSAL_FIELDS:
        return {"status": "error", "reason": "malformed_proposal", "supervisor_authored": True}
    status = proposal.get("status")
    provenance = proposal.get("provenance")
    if not isinstance(status, str) or status not in STATUSES or not isinstance(provenance, dict) or not isinstance(provenance.get("source"), str) or not provenance["source"]:
        return {"status": "error", "reason": "malformed_proposal", "supervisor_authored": True}
    evidence_ids = proposal.get("evidence_ids", [])
    valid_ids = {item["id"] for item in request["evidence"]}
    if not isinstance(evidence_ids, list) or any(not isinstance(x, str) or x not in valid_ids for x in evidence_ids):
        return {"status": "error", "reason": "unknown_evidence", "supervisor_authored": True}
    action = proposal.get("action")
    if status == "answer" and (not isinstance(action, str) or action not in request["options"]):
        return {"status": "error", "reason": "unauthorized_action", "supervisor_authored": True}
    if status != "answer" and action is not None:
        return {"status": "error", "reason": "malformed_proposal", "supervisor_authored": True}
    if status == "answer" and not evidence_ids:
        return {"status": "need_evidence", "reason": "answer_without_evidence", "supervisor_authored": True}
    result = {"status": status, "supervisor_authored": True, "evidence_ids": evidence_ids}
    if action is not None:
        result["action"] = action
    if isinstance(proposal.get("reason"), str) and proposal["reason"]:
        result["reason"] = proposal["reason"]
    result["provenance"] = provenance
    return result


def open_database(path: str | Path) -> sqlite3.Connection:
    ledger = os.path.abspath(os.fspath(path))
    parent = os.path.dirname(ledger)
    parent_info = os.stat(parent, follow_symlinks=False)
    if not stat.S_ISDIR(parent_info.st_mode) or parent_info.st_uid != os.getuid() or parent_info.st_mode & 0o077:
        raise ValueError("ledger parent must be an existing owner-only directory")
    try:
        ledger_info = os.lstat(ledger)
    except FileNotFoundError:
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(ledger, flags, 0o600)
        os.close(descriptor)
    else:
        if not stat.S_ISREG(ledger_info.st_mode) or ledger_info.st_uid != os.getuid():
            raise ValueError("ledger must be an owner-owned regular file")
        os.chmod(ledger, 0o600)
    connection = sqlite3.connect(ledger)
    connection.row_factory = sqlite3.Row
    model_columns = {row[1] for row in connection.execute("PRAGMA table_info(model_calls)")}
    if model_columns and "payload" not in model_columns:
        connection.close()
        raise ValueError("legacy model_calls ledger lacks immutable request payload; refusing migration")
    connection.execute("PRAGMA foreign_keys=ON")
    connection.execute("PRAGMA journal_mode=WAL")
    with connection:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS requests (request_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "payload_hash TEXT NOT NULL, payload TEXT NOT NULL, proposal TEXT NOT NULL, result TEXT NOT NULL, "
            "PRIMARY KEY (request_id, revision))"
        )
        connection.execute(
            "CREATE TABLE IF NOT EXISTS model_calls (request_id TEXT NOT NULL, revision INTEGER NOT NULL, "
            "payload_hash TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL, raw_response TEXT, terminal_error TEXT, "
            "metadata TEXT, PRIMARY KEY (request_id, revision))"
        )
    return connection


def decide(connection: sqlite3.Connection, request: dict[str, Any], proposal: Any = None) -> dict[str, Any]:
    validate_request(request)
    payload_hash = _hash(request)
    request_id, revision = request["request_id"], request["revision"]
    connection.execute("BEGIN IMMEDIATE")
    try:
        latest_request = connection.execute("SELECT MAX(revision) FROM requests WHERE request_id=?", (request_id,)).fetchone()[0]
        latest_call = connection.execute("SELECT MAX(revision) FROM model_calls WHERE request_id=?", (request_id,)).fetchone()[0]
        latest = max(x for x in (latest_request, latest_call) if x is not None) if latest_request is not None or latest_call is not None else None
        call_identity = connection.execute(
            "SELECT payload_hash FROM model_calls WHERE request_id=? AND revision=?", (request_id, revision)
        ).fetchone()
        if call_identity and call_identity["payload_hash"] != payload_hash:
            raise _fail("model call payload does not match immutable request revision")
        if latest is not None and revision < latest:
            raise _fail("stale revision cannot replace a newer request")
        existing = connection.execute(
            "SELECT payload_hash, result FROM requests WHERE request_id=? AND revision=?", (request_id, revision)
        ).fetchone()
        if existing:
            if existing["payload_hash"] != payload_hash:
                raise _fail("request revision is immutable and payload does not match")
            connection.commit()
            return json.loads(existing["result"])
        if latest_request is not None and revision <= latest_request:
            raise _fail("revision must increase for an existing request_id")
        answer = _proposal_or_fail_closed(request, proposal)
        result = {
            "request_id": request_id,
            "revision": revision,
            "session_id": request["session_id"],
            "task_id": request["task_id"],
            **answer,
            "effect_id": _hash([request_id, revision, answer]) if answer["status"] == "answer" else None,
        }
        proposal_json = _canonical(proposal)
        connection.execute(
            "INSERT INTO requests(request_id, revision, payload_hash, payload, proposal, result) VALUES (?,?,?,?,?,?)",
            (request_id, revision, payload_hash, _canonical(request), proposal_json, _canonical(result)),
        )
        connection.commit()
        return result
    except Exception:
        connection.rollback()
        raise

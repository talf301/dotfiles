#!/usr/bin/env python3
from __future__ import annotations
"""Read one request and optional supervisor proposal; print one durable result."""

import argparse
import json
import sys
import sqlite3
from pathlib import Path

try:
    from router import decide, open_database
    from delegate import delegate_claude
except ModuleNotFoundError:  # pragma: no cover - supports direct script invocation
    from .router import decide, open_database
    from .delegate import delegate_claude

MAX_JSON_BYTES = 262144


def reject_constant(value: str):
    raise ValueError(f"non-finite JSON: {value}")


def _load(path: str | None):
    if path is None:
        return None
    with open(path, "rb") as stream:
        raw = stream.read(MAX_JSON_BYTES + 1)
    if len(raw) > MAX_JSON_BYTES:
        raise ValueError("JSON input exceeds 262144-byte limit")
    return json.loads(raw, parse_constant=reject_constant)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", required=True)
    parser.add_argument("--request", help="JSON request file; defaults to stdin")
    parser.add_argument("--proposal", help="JSON supervisor proposal file")
    parser.add_argument("--delegate", choices=["claude"])
    args = parser.parse_args()
    try:
        if args.request:
            request = _load(args.request)
        else:
            raw = sys.stdin.buffer.read(MAX_JSON_BYTES + 1)
            if len(raw) > MAX_JSON_BYTES:
                raise ValueError("request exceeds 262144-byte limit")
            request = json.loads(raw, parse_constant=reject_constant)
        if args.delegate and args.proposal:
            raise ValueError("--delegate and --proposal are mutually exclusive")
        database = open_database(Path(args.db))
        result = delegate_claude(database, request) if args.delegate else decide(database, request, _load(args.proposal))
    except (OSError, json.JSONDecodeError, ValueError, sqlite3.Error) as exc:
        print(json.dumps({"status": "error", "reason": "invalid_input" if isinstance(exc, (json.JSONDecodeError, ValueError)) else "storage_error", "detail": str(exc)}))
        return 2
    print(json.dumps(result, sort_keys=True, ensure_ascii=False))
    return 2 if result.get("status") == "error" else 0


if __name__ == "__main__":
    raise SystemExit(main())

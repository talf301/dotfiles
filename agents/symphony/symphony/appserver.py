"""Codex app-server client (SPEC §10) for Codex 0.169's v2 protocol.

Two transports: the shared daemon (WebSocket over a Unix socket, adapted from
run-ticket/codex_rpc.py) and a stdio subprocess (`bash -lc <codex.command>`, JSON lines).
See DESIGN.md "Event mapping" for how protocol messages become AgentEvents.
"""

from __future__ import annotations

import base64
import json
import os
import queue
import socket
import struct
import subprocess
import threading
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol

from . import __version__
from .config import CodexConfig
from .errors import AgentError
from .log import log
from .models import AgentEvent

MAX_LINE_BYTES = 10 * 1024 * 1024
CLIENT_NAME = "symphony"
SUMMARY_LIMIT = 1024
NOTIFICATION_INTERVAL_S = 1.0
MALFORMED = "__malformed__"  # key of the dict a transport returns for an undecodable message

# Server-request methods (ServerRequest.json, Codex 0.169)
APPROVAL_METHODS = {
    "item/commandExecution/requestApproval": "command_approval",
    "execCommandApproval": "command_approval",
    "item/fileChange/requestApproval": "file_approval",
    "applyPatchApproval": "file_approval",
    "item/permissions/requestApproval": "permissions",
}
INPUT_METHODS = {
    "item/tool/requestUserInput": "user_input",
    "mcpServer/elicitation/request": "elicitation",
}
DYNAMIC_TOOL_METHOD = "item/tool/call"

# Replies that decline without approving anything.
DECLINE_REPLIES = {
    "item/commandExecution/requestApproval": {"decision": "cancel"},
    "item/fileChange/requestApproval": {"decision": "cancel"},
    "execCommandApproval": {"decision": "abort"},
    "applyPatchApproval": {"decision": "abort"},
    "item/permissions/requestApproval": {"permissions": {}, "scope": "turn"},
    "item/tool/requestUserInput": {"answers": {}},
    "mcpServer/elicitation/request": {"action": "cancel"},
}


def _safe_log(event: str, level: str = "info", **fields) -> None:
    try:
        log(event, level, **fields)
    except Exception:  # log() must never break the runner
        pass


class Transport(Protocol):
    pid: str | None

    def send(self, msg: dict) -> None: ...

    def recv(self, timeout_s: float) -> dict | None:
        """Next message, or None on timeout. Raises AgentError(port_exit) when closed."""
        ...

    def close(self) -> None: ...


def _decode(data: bytes) -> dict:
    try:
        msg = json.loads(data)
    except ValueError:
        return {MALFORMED: data[:200].decode(errors="replace")}
    return msg if isinstance(msg, dict) else {MALFORMED: repr(msg)[:200]}


class DaemonTransport:
    """WebSocket over the shared daemon's Unix socket (adapted from run-ticket/codex_rpc.py)."""

    pid = None

    def __init__(self, socket_path: Path, connect_timeout_s: float):
        self.sock = socket.socket(socket.AF_UNIX)
        self.sock.settimeout(connect_timeout_s)
        try:
            self.sock.connect(str(socket_path))
            key = base64.b64encode(os.urandom(16)).decode()
            self.sock.sendall((
                "GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
            head = b""
            while b"\r\n\r\n" not in head:
                chunk = self.sock.recv(4096)
                if not chunk:
                    raise ConnectionError("closed during handshake")
                head += chunk
        except (OSError, ConnectionError) as e:
            self.sock.close()
            raise AgentError("startup_failed", f"cannot connect to Codex daemon at {socket_path}: {e}")
        head, self._buf = head.split(b"\r\n\r\n", 1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            self.sock.close()
            raise AgentError("startup_failed", "Codex daemon refused the WebSocket upgrade: "
                             + head.split(b"\r\n", 1)[0].decode(errors="replace"))
        self._parts: list[bytes] = []
        self._lock = threading.Lock()

    def _send_frame(self, data: bytes, opcode: int) -> None:
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        masked = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
        with self._lock:
            try:
                self.sock.sendall(hdr + mask + masked)
            except OSError as e:
                raise AgentError("port_exit", f"Codex daemon socket closed: {e}")

    def send(self, msg: dict) -> None:
        self._send_frame(json.dumps(msg).encode(), 1)

    def _parse(self) -> dict | None:
        """Consume complete frames from the buffer; return the next full message, if any."""
        while True:
            buf = self._buf
            if len(buf) < 2:
                return None
            b0, b1 = buf[0], buf[1]
            op, n, pos = b0 & 0x0F, b1 & 0x7F, 2
            if n == 126:
                if len(buf) < 4:
                    return None
                n, pos = struct.unpack(">H", buf[2:4])[0], 4
            elif n == 127:
                if len(buf) < 10:
                    return None
                n, pos = struct.unpack(">Q", buf[2:10])[0], 10
            mask = None
            if b1 & 0x80:
                if len(buf) < pos + 4:
                    return None
                mask, pos = buf[pos:pos + 4], pos + 4
            if len(buf) < pos + n:
                return None
            data, self._buf = buf[pos:pos + n], buf[pos + n:]
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 9:  # ping
                self._send_frame(data, 10)
            elif op == 8:
                raise AgentError("port_exit", "Codex daemon closed the connection")
            elif op in (0, 1, 2):
                self._parts.append(data)
                if sum(len(p) for p in self._parts) > MAX_LINE_BYTES:
                    self._parts = []
                    return {MALFORMED: "message over 10 MB"}
                if b0 & 0x80:
                    whole, self._parts = b"".join(self._parts), []
                    return _decode(whole)

    def recv(self, timeout_s: float) -> dict | None:
        deadline = time.monotonic() + timeout_s
        while True:
            msg = self._parse()
            if msg is not None:
                return msg
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return None
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(65536)
            except socket.timeout:
                return None
            except OSError as e:
                raise AgentError("port_exit", f"Codex daemon socket error: {e}")
            if not chunk:
                raise AgentError("port_exit", "Codex daemon closed the socket")
            self._buf += chunk

    def close(self) -> None:
        try:
            self._send_frame(b"", 8)
        except AgentError:
            pass
        finally:
            self.sock.close()


class StdioTransport:
    """`bash -lc <command>` in cwd; JSON lines on stdout; stderr logged separately."""

    _EOF = object()

    def __init__(self, command: str, cwd: Path, env: Mapping[str, str]):
        try:
            self.proc = subprocess.Popen(
                ["bash", "-lc", command], cwd=str(cwd), env=dict(env),
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        except FileNotFoundError as e:
            raise AgentError("codex_not_found", str(e))
        except OSError as e:
            raise AgentError("startup_failed", f"cannot start {command!r}: {e}")
        self.pid = str(self.proc.pid)
        self._q: queue.Queue = queue.Queue()
        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()

    def _read_stdout(self) -> None:
        out = self.proc.stdout
        while True:
            line = out.readline(MAX_LINE_BYTES + 1)
            if not line:
                break
            if len(line) > MAX_LINE_BYTES and not line.endswith(b"\n"):
                while line and not line.endswith(b"\n"):  # discard the rest of the line
                    line = out.readline(MAX_LINE_BYTES)
                self._q.put({MALFORMED: "line over 10 MB"})
                continue
            if line.strip():
                self._q.put(_decode(line))
        self._q.put(self._EOF)

    def _read_stderr(self) -> None:
        for line in self.proc.stderr:
            _safe_log("codex_stderr", "debug", pid=self.pid,
                      line=line.decode(errors="replace").rstrip()[:500])

    def send(self, msg: dict) -> None:
        try:
            self.proc.stdin.write(json.dumps(msg).encode() + b"\n")
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as e:
            raise AgentError("port_exit", f"app-server stdin closed: {e}")

    def recv(self, timeout_s: float) -> dict | None:
        try:
            item = self._q.get(timeout=max(timeout_s, 0))
        except queue.Empty:
            return None
        if item is self._EOF:
            self._q.put(self._EOF)  # stay closed for later calls
            try:
                code = self.proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                code = None
            if code == 127:
                raise AgentError("codex_not_found", "app-server command not found (exit 127)")
            raise AgentError("port_exit", f"app-server exited (code {code})")
        return item

    def close(self) -> None:
        for f in (self.proc.stdin,):
            try:
                f.close()
            except OSError:
                pass
        try:
            self.proc.terminate()
            self.proc.wait(timeout=5)
        except (OSError, subprocess.TimeoutExpired):
            self.proc.kill()


def open_transport(codex: CodexConfig, cwd: Path, env: Mapping[str, str]) -> Transport:
    if codex.transport == "stdio":
        return StdioTransport(codex.command, cwd, env)
    if codex.transport == "daemon":
        return DaemonTransport(Path(codex.socket), codex.read_timeout_ms / 1000)
    raise AgentError("startup_failed", f"unknown codex.transport {codex.transport!r}")


@dataclass
class Escalation:
    kind: str          # command_approval | file_approval | permissions | user_input | elicitation
    summary: str       # <= 1 KB, for the Linear question


@dataclass
class TurnResult:
    status: str  # completed | failed | interrupted | timeout | cancelled | escalated
    turn_id: str | None
    error: str | None = None
    escalation: Escalation | None = None


def _clip(text: str, limit: int = SUMMARY_LIMIT) -> str:
    return text if len(text) <= limit else text[:limit - 3] + "..."


def summarize_request(kind: str, params: dict) -> str:
    """Short human-readable description of an approval or input request."""
    parts: list[str] = []
    if kind == "command_approval":
        cmd = params.get("command")
        if isinstance(cmd, list):
            cmd = " ".join(str(c) for c in cmd)
        if cmd:
            parts.append(f"run `{cmd}`")
        if params.get("cwd"):
            parts.append(f"in {params['cwd']}")
    elif kind == "file_approval":
        files = list((params.get("fileChanges") or {}).keys())
        parts.append("change files" + (": " + ", ".join(files) if files else ""))
        if params.get("grantRoot"):
            parts.append(f"with write access to {params['grantRoot']}")
    elif kind == "permissions":
        parts.append("grant permissions " + json.dumps(params.get("permissions"), sort_keys=True))
    elif kind == "user_input":
        qs = [q.get("question", "") for q in params.get("questions") or [] if isinstance(q, dict)]
        parts.append("answer: " + " | ".join(q for q in qs if q))
    elif kind == "elicitation":
        parts.append(f"MCP server {params.get('serverName', '?')} asked for input")
        if params.get("message"):
            parts.append(str(params["message"]))
    if params.get("reason"):
        parts.append(f"(reason: {params['reason']})")
    return _clip(" ".join(parts) or kind)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class AppServerClient:
    """JSON-RPC client over a Transport. One thread at a time (`thread_id`) is ours; messages
    for other threads on the shared daemon are ignored."""

    def __init__(self, transport: Transport, *, read_timeout_s: float,
                 on_event: Callable[[AgentEvent], None],
                 clock: Callable[[], float] = time.monotonic):
        self.transport = transport
        self.read_timeout_s = read_timeout_s
        self.on_event = on_event
        self.clock = clock
        self.thread_id: str | None = None
        self.turn_id: str | None = None
        self.last_activity = clock()
        self._next_id = 0
        self._completed: dict[str, dict] = {}
        self._escalation: Escalation | None = None
        self._turn_error: str | None = None
        self._last_note = float("-inf")

    # -- plumbing -----------------------------------------------------------------------------

    def _emit(self, event: str, **fields) -> None:
        self.on_event(AgentEvent(event=event, timestamp=_utcnow(), thread_id=self.thread_id,
                                 turn_id=self.turn_id, codex_app_server_pid=self.transport.pid,
                                 **fields))

    def _send_request(self, method: str, params: dict) -> int:
        self._next_id += 1
        self.transport.send({"jsonrpc": "2.0", "id": self._next_id, "method": method,
                             "params": params})
        return self._next_id

    def notify(self, method: str, params: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self.transport.send(msg)

    def request(self, method: str, params: dict) -> dict:
        rid = self._send_request(method, params)
        deadline = self.clock() + self.read_timeout_s
        while True:
            remaining = deadline - self.clock()
            if remaining <= 0:
                raise AgentError("response_timeout", f"no reply to {method} in "
                                 f"{self.read_timeout_s:g}s")
            msg = self.transport.recv(min(remaining, 1.0))
            if msg is None:
                continue
            if msg.get("id") == rid and "method" not in msg:
                if "error" in msg:
                    err = msg["error"] or {}
                    text = err.get("message") if isinstance(err, dict) else str(err)
                    raise AgentError("response_error", f"{method}: {text}")
                return msg.get("result") or {}
            self._handle(msg)

    # -- inbound message routing --------------------------------------------------------------

    def _handle(self, msg: dict) -> None:
        if MALFORMED in msg:
            self._emit("malformed", message=_clip(str(msg[MALFORMED]), 200))
            return
        method = msg.get("method")
        if method is None:
            return  # reply to a fire-and-forget request (e.g. turn/interrupt)
        params = msg.get("params") if isinstance(msg.get("params"), dict) else {}
        thread = params.get("thread") if isinstance(params.get("thread"), dict) else {}
        tid = params.get("threadId") or params.get("conversationId") or thread.get("id")
        if tid:
            if tid != self.thread_id:
                return  # another thread on the shared daemon (or no thread of ours yet)
            ours = True
        else:
            ours = bool(self.turn_id) and params.get("turnId") == self.turn_id
        if ours:
            self.last_activity = self.clock()
        if "id" in msg:
            self._handle_server_request(msg["id"], method, params)
        elif ours:
            self._handle_notification(method, params)
        elif method == "account/rateLimits/updated":
            # Account-wide: record the limits, but it is not activity of our turn.
            self._emit("notification", method=method, rate_limits=params.get("rateLimits"),
                       scoped=False)

    def _reply(self, rid, result: dict | None = None, error: dict | None = None) -> None:
        msg = {"jsonrpc": "2.0", "id": rid}
        if error is not None:
            msg["error"] = error
        else:
            msg["result"] = result
        self.transport.send(msg)

    def _handle_server_request(self, rid, method: str, params: dict) -> None:
        kind = APPROVAL_METHODS.get(method) or INPUT_METHODS.get(method)
        if kind:
            self._reply(rid, DECLINE_REPLIES[method])
            summary = summarize_request(kind, params)
            if self._escalation is None:
                self._escalation = Escalation(kind, summary)
            if self.turn_id and self.thread_id:
                self._send_request("turn/interrupt", {"threadId": self.thread_id,
                                                      "turnId": self.turn_id})
            event = "approval_escalated" if method in APPROVAL_METHODS else "turn_input_required"
            self._emit(event, method=method, message=summary)
        elif method == DYNAMIC_TOOL_METHOD:
            tool = params.get("tool", "?")
            self._reply(rid, {"success": False, "contentItems": [
                {"type": "inputText", "text": f"unsupported tool: {tool}"}]})
            self._emit("unsupported_tool_call", method=method, message=_clip(str(tool), 200))
        else:
            self._reply(rid, error={"code": -32601, "message": f"unsupported method {method}"})
            self._emit("other_message", method=method)

    def _handle_notification(self, method: str, params: dict) -> None:
        if method == "turn/completed":
            turn = params.get("turn") or {}
            if turn.get("id"):
                self._completed[turn["id"]] = turn
            return  # run_turn emits the outcome event
        if method == "error":
            if not params.get("willRetry"):
                err = params.get("error") or {}
                self._turn_error = _clip(str(err.get("message") if isinstance(err, dict) else err),
                                         500)
                self._emit("turn_ended_with_error", method=method, message=self._turn_error)
            return
        if method == "thread/tokenUsage/updated":
            total = (params.get("tokenUsage") or {}).get("total") or {}
            usage = {"input_tokens": int(total.get("inputTokens") or 0),
                     "output_tokens": int(total.get("outputTokens") or 0),
                     "total_tokens": int(total.get("totalTokens") or 0)}
            self._emit("notification", method=method, usage=usage)
            return
        if method == "account/rateLimits/updated":
            self._emit("notification", method=method, rate_limits=params.get("rateLimits"))
            return
        now = self.clock()
        if now - self._last_note >= NOTIFICATION_INTERVAL_S:
            self._last_note = now
            self._emit("notification", method=method)

    # -- protocol operations ------------------------------------------------------------------

    def initialize(self, client_name: str = CLIENT_NAME) -> None:
        self.request("initialize", {"clientInfo": {"name": client_name, "version": __version__}})
        self.notify("initialized")

    @staticmethod
    def _thread_params(cwd: Path, policy: CodexConfig) -> dict:
        p = {"cwd": str(cwd), "approvalPolicy": policy.approval_policy,
             "sandbox": policy.thread_sandbox}
        if policy.approvals_reviewer is not None:
            p["approvalsReviewer"] = policy.approvals_reviewer
        return p

    def start_thread(self, *, cwd: Path, policy: CodexConfig) -> str:
        result = self.request("thread/start", self._thread_params(cwd, policy))
        self.thread_id = result["thread"]["id"]
        return self.thread_id

    def resume_thread(self, thread_id: str, *, cwd: Path, policy: CodexConfig) -> str:
        """Resume the thread. If it still has a turn running (a crash, or a cancel whose
        turn/completed never came), interrupt that turn and wait for it to end first."""
        params = {"threadId": thread_id, **self._thread_params(cwd, policy), "excludeTurns": True}
        self.thread_id = thread_id  # messages for this thread are ours while resuming
        try:
            result = self.request("thread/resume", params)
        except AgentError as e:
            self.thread_id = None
            if e.category != "response_error":
                raise
            raise AgentError("thread_resume_failed", e.message)
        thread = result.get("thread") or {}
        self.thread_id = thread.get("id") or thread_id
        if (thread.get("status") or {}).get("type") == "active":
            self._end_running_turn(self.thread_id)
        return self.thread_id

    def _end_running_turn(self, thread_id: str) -> None:
        """Interrupt the thread's in-progress turn and wait up to read_timeout_s for it to end.
        Raises AgentError(turn_in_progress) rather than let a second turn start beside it."""
        turns = self.request("thread/turns/list", {"threadId": thread_id, "limit": 1}).get("data")
        turn = turns[0] if isinstance(turns, list) and turns and isinstance(turns[0], dict) else {}
        turn_id = turn.get("id")
        if not turn_id or turn.get("status") != "inProgress":
            return
        _safe_log("codex_turn_still_running", "warning", thread_id=thread_id, turn_id=turn_id)
        self.interrupt(thread_id, turn_id)
        deadline = self.clock() + self.read_timeout_s
        while self._completed.pop(turn_id, None) is None:
            if self.clock() >= deadline:
                raise AgentError("turn_in_progress", f"earlier turn {turn_id} on thread "
                                 f"{thread_id} did not stop after turn/interrupt")
            msg = self.transport.recv(0.5)
            if msg is not None:
                self._handle(msg)

    def set_title(self, thread_id: str, title: str) -> None:
        try:
            self.request("thread/name/set", {"threadId": thread_id, "name": title})
        except AgentError as e:
            _safe_log("codex_set_title_failed", "warning", thread_id=thread_id, error=str(e))

    def interrupt(self, thread_id: str, turn_id: str) -> None:
        """turn/interrupt without waiting for the reply (best effort)."""
        try:
            self._send_request("turn/interrupt", {"threadId": thread_id, "turnId": turn_id})
        except AgentError:
            pass

    def run_turn(self, thread_id: str, prompt: str, *, cwd: Path, policy: CodexConfig,
                 turn_timeout_s: float, cancel: threading.Event) -> TurnResult:
        self.thread_id = thread_id
        self.turn_id = None
        self._escalation = None
        self._turn_error = None
        params = {"threadId": thread_id, "input": [{"type": "text", "text": prompt}],
                  "cwd": str(cwd)}
        if policy.turn_sandbox_policy is not None:
            params["sandboxPolicy"] = policy.turn_sandbox_policy
        result = self.request("turn/start", params)
        turn_id = (result.get("turn") or {}).get("id")
        if not turn_id:
            raise AgentError("response_error", "turn/start returned no turn id")
        self.turn_id = turn_id
        self.last_activity = self.clock()
        self._emit("session_started", message=f"{thread_id}-{turn_id}")

        give_up_at: float | None = None  # after cancel/escalation: wait briefly for completion
        while True:
            turn = self._completed.pop(turn_id, None)
            if turn is not None:
                return self._finish(turn_id, turn, cancel)
            now = self.clock()
            if give_up_at is None and (cancel.is_set() or self._escalation):
                if cancel.is_set():
                    self.interrupt(thread_id, turn_id)
                give_up_at = now + self.read_timeout_s
            if give_up_at is not None and now >= give_up_at:
                if self._escalation:
                    return TurnResult("escalated", turn_id, escalation=self._escalation)
                self._emit("turn_cancelled", message="cancelled")
                return TurnResult("cancelled", turn_id, error="cancelled")
            if give_up_at is None and now - self.last_activity > turn_timeout_s:
                self.interrupt(thread_id, turn_id)
                self._emit("turn_failed", message="turn_timeout")
                return TurnResult("timeout", turn_id, error=f"no turn activity for {turn_timeout_s:g}s")
            msg = self.transport.recv(0.5)
            if msg is not None:
                self._handle(msg)

    def _finish(self, turn_id: str, turn: dict, cancel: threading.Event) -> TurnResult:
        status = turn.get("status")
        err = turn.get("error") or {}
        error = (err.get("message") if isinstance(err, dict) else None) or self._turn_error
        if self._escalation:
            return TurnResult("escalated", turn_id, error=error, escalation=self._escalation)
        if status == "completed":
            self._emit("turn_completed")
            return TurnResult("completed", turn_id)
        if status == "failed":
            self._emit("turn_failed", message=error)
            return TurnResult("failed", turn_id, error=error)
        self._emit("turn_cancelled", message=error)
        if cancel.is_set():
            return TurnResult("cancelled", turn_id, error=error or "cancelled")
        return TurnResult("interrupted", turn_id, error=error or f"turn ended with status {status}")

    def close(self) -> None:
        try:
            self.transport.close()
        except Exception as e:  # closing must not mask the attempt's outcome
            _safe_log("codex_close_failed", "warning", error=str(e))

"""§17.5 app-server client tests against FakeAppServer and local fake transports."""

from __future__ import annotations

import json
import os
import shutil
import socket
import struct
import sys
import tempfile
import textwrap
import threading
from pathlib import Path

import pytest

from fakes import FakeAppServer
from symphony.appserver import (
    AppServerClient,
    DaemonTransport,
    StdioTransport,
    open_transport,
)
from symphony.config import CodexConfig
from symphony.errors import AgentError

POLICY = CodexConfig()
CWD = Path("/tmp/ws/TAL-1")


class TickClock:
    """Monotonic clock that advances `step` seconds per call, so timeouts fire quickly."""

    def __init__(self, step: float = 1.0):
        self.t = 0.0
        self.step = step

    def __call__(self) -> float:
        self.t += self.step
        return self.t


def make_client(server, *, read_timeout_s=5.0, clock=None):
    events = []
    c = AppServerClient(server, read_timeout_s=read_timeout_s, on_event=events.append,
                        clock=clock or TickClock(0.001))
    return c, events


def names(events):
    return [e.event for e in events]


def ready_server(thread_id="th-1"):
    s = FakeAppServer(pid="4242")
    s.on("initialize", {"userAgent": "codex"})
    s.on("thread/start", {"thread": {"id": thread_id}})
    return s


# -- startup ------------------------------------------------------------------------------------

def test_startup_sequence_identity_and_policy_payloads():
    s = ready_server()
    c, _ = make_client(s)
    c.initialize()
    tid = c.start_thread(cwd=CWD, policy=POLICY)
    assert tid == "th-1"
    methods = [m.get("method") for m in s.sent]
    assert methods == ["initialize", "initialized", "thread/start"]
    init = s.requests("initialize")[0]
    assert init["clientInfo"]["name"] == "symphony" and init["clientInfo"]["version"]
    assert "id" not in s.sent[1]  # initialized is a notification
    assert s.requests("thread/start")[0] == {
        "cwd": str(CWD), "approvalPolicy": "on-request", "sandbox": "workspace-write",
        "approvalsReviewer": "auto_review"}


def test_policy_overrides_pass_through_and_reviewer_omitted_when_none():
    s = ready_server()
    c, _ = make_client(s)
    pol = CodexConfig(approval_policy={"granular": {"sandbox_approval": True}},
                      approvals_reviewer=None, thread_sandbox="read-only")
    c.start_thread(cwd=CWD, policy=pol)
    p = s.requests("thread/start")[0]
    assert p["approvalPolicy"] == {"granular": {"sandbox_approval": True}}
    assert p["sandbox"] == "read-only" and "approvalsReviewer" not in p


def test_resume_thread_payload_and_failure_category():
    s = FakeAppServer().on("thread/resume", {"thread": {"id": "th-old"}})
    c, _ = make_client(s)
    assert c.resume_thread("th-old", cwd=CWD, policy=POLICY) == "th-old"
    p = s.requests("thread/resume")[0]
    assert p["threadId"] == "th-old" and p["excludeTurns"] is True and p["cwd"] == str(CWD)

    s2 = FakeAppServer().on("thread/resume", RuntimeError("no rollout found"))
    c2, _ = make_client(s2)
    with pytest.raises(AgentError) as ei:
        c2.resume_thread("th-gone", cwd=CWD, policy=POLICY)
    assert ei.value.category == "thread_resume_failed"


def test_set_title_sends_name_and_is_best_effort():
    s = FakeAppServer().on("thread/name/set", {})
    c, _ = make_client(s)
    c.set_title("th-1", "TAL-1: Fix it")
    assert s.requests("thread/name/set") == [{"threadId": "th-1", "name": "TAL-1: Fix it"}]
    s2 = FakeAppServer().on("thread/name/set", RuntimeError("nope"))
    make_client(s2)[0].set_title("th-1", "x")  # no raise


def test_read_timeout_enforced():
    s = FakeAppServer()  # initialize unanswered
    c, _ = make_client(s, read_timeout_s=3, clock=TickClock(1.0))
    with pytest.raises(AgentError) as ei:
        c.initialize()
    assert ei.value.category == "response_timeout"


def test_jsonrpc_error_is_response_error():
    s = FakeAppServer().on("thread/start", RuntimeError("bad params"))
    c, _ = make_client(s)
    with pytest.raises(AgentError) as ei:
        c.start_thread(cwd=CWD, policy=POLICY)
    assert ei.value.category == "response_error" and "bad params" in str(ei.value)


def test_port_exit_surfaces():
    s = FakeAppServer()
    s.close()
    c, _ = make_client(s)
    with pytest.raises(AgentError) as ei:
        c.initialize()
    assert ei.value.category == "port_exit"


# -- turns --------------------------------------------------------------------------------------

def note(method, **params):
    return {"jsonrpc": "2.0", "method": method, "params": params}


def run(c, prompt="do it", *, cancel=None, turn_timeout_s=60):
    return c.run_turn("th-1", prompt, cwd=CWD, policy=POLICY, turn_timeout_s=turn_timeout_s,
                      cancel=cancel or threading.Event())


def test_turn_completed_emits_session_started_and_usage():
    s = FakeAppServer(pid="77")
    s.script_turn("th-1", "tu-1", events=[
        note("thread/tokenUsage/updated", threadId="th-1", turnId="tu-1", tokenUsage={
            "last": {"inputTokens": 1, "outputTokens": 1, "totalTokens": 2},
            "total": {"inputTokens": 100, "outputTokens": 20, "totalTokens": 120,
                      "cachedInputTokens": 0, "reasoningOutputTokens": 0}}),
        note("account/rateLimits/updated", rateLimits={"primary": {"usedPercent": 12}}),
    ])
    c, events = make_client(s)
    r = run(c, "hello")
    assert (r.status, r.turn_id) == ("completed", "tu-1")
    p = s.requests("turn/start")[0]
    assert p == {"threadId": "th-1", "input": [{"type": "text", "text": "hello"}],
                 "cwd": str(CWD)}
    started = events[0]
    assert started.event == "session_started" and started.message == "th-1-tu-1"
    assert started.thread_id == "th-1" and started.turn_id == "tu-1"
    assert started.codex_app_server_pid == "77"
    usage = [e.usage for e in events if e.usage]
    assert usage == [{"input_tokens": 100, "output_tokens": 20, "total_tokens": 120}]
    assert [e.rate_limits for e in events if e.rate_limits] == [{"primary": {"usedPercent": 12}}]
    assert names(events)[-1] == "turn_completed"
    assert all(e.timestamp.tzinfo is not None for e in events)


def test_turn_sandbox_policy_sent_when_set():
    s = FakeAppServer().script_turn("th-1", "tu-1")
    c, _ = make_client(s)
    pol = CodexConfig(turn_sandbox_policy={"type": "workspaceWrite"})
    c.run_turn("th-1", "x", cwd=CWD, policy=pol, turn_timeout_s=60, cancel=threading.Event())
    assert s.requests("turn/start")[0]["sandboxPolicy"] == {"type": "workspaceWrite"}


def test_turn_failed_carries_error_message():
    s = FakeAppServer().script_turn("th-1", "tu-1", status="failed", error="model overloaded",
                                    events=[note("error", threadId="th-1", turnId="tu-1",
                                                 willRetry=False, error={"message": "boom"})])
    c, events = make_client(s)
    r = run(c)
    assert r.status == "failed" and r.error == "model overloaded"
    assert "turn_ended_with_error" in names(events) and names(events)[-1] == "turn_failed"


def test_retrying_error_is_not_terminal():
    s = FakeAppServer().script_turn("th-1", "tu-1", events=[
        note("error", threadId="th-1", turnId="tu-1", willRetry=True, error={"message": "x"})])
    c, events = make_client(s)
    assert run(c).status == "completed"
    assert "turn_ended_with_error" not in names(events)


def test_turn_interrupted_without_escalation_is_failure():
    s = FakeAppServer().script_turn("th-1", "tu-1", status="interrupted")
    c, events = make_client(s)
    r = run(c)
    assert r.status == "interrupted" and names(events)[-1] == "turn_cancelled"


def test_turn_timeout_enforced_and_interrupts():
    s = FakeAppServer().on("turn/start", {"turn": {"id": "tu-1"}})
    c, events = make_client(s, clock=TickClock(1.0))
    r = run(c, turn_timeout_s=5)
    assert r.status == "timeout"
    assert s.requests("turn/interrupt") == [{"threadId": "th-1", "turnId": "tu-1"}]
    assert names(events)[-1] == "turn_failed"


def test_activity_resets_turn_silence_timer():
    # 8 notifications 1 s apart with a 5 s silence timeout: still completes.
    events_in = [note("item/agentMessage/delta", threadId="th-1", turnId="tu-1", delta="x")
                 for _ in range(8)]
    s = FakeAppServer().script_turn("th-1", "tu-1", events=events_in)
    c, _ = make_client(s, clock=TickClock(1.0))
    assert run(c, turn_timeout_s=5).status == "completed"


def test_cancel_interrupts_turn():
    s = FakeAppServer().on("turn/start", {"turn": {"id": "tu-1"}})
    s.after("turn/interrupt", [note("turn/completed", threadId="th-1",
                                    turn={"id": "tu-1", "status": "interrupted"})])
    s.on("turn/interrupt", {})
    c, _ = make_client(s)
    cancel = threading.Event()
    cancel.set()
    r = run(c, cancel=cancel)
    assert r.status == "cancelled"
    assert s.requests("turn/interrupt") == [{"threadId": "th-1", "turnId": "tu-1"}]


def test_other_threads_and_malformed_frames_ignored():
    s = FakeAppServer().script_turn("th-1", "tu-1", events=[
        note("turn/completed", threadId="th-other", turn={"id": "tu-1", "status": "failed"}),
        {"__malformed__": "not json"},
    ])
    rid = s.server_request("item/commandExecution/requestApproval", threadId="th-other",
                           turnId="tu-x", itemId="i", startedAtMs=0, command="rm x")
    c, events = make_client(s)
    r = run(c)
    assert r.status == "completed"
    assert rid not in s.replies  # another client's request: not answered by us
    assert "malformed" in names(events)


def test_notifications_coalesced():
    deltas = [note("item/agentMessage/delta", threadId="th-1", turnId="tu-1", delta="x")
              for _ in range(50)]
    s = FakeAppServer().script_turn("th-1", "tu-1", events=deltas)
    c, events = make_client(s, clock=TickClock(0.001))
    run(c)
    assert names(events).count("notification") < 10


# -- approvals, user input, tools ---------------------------------------------------------------

def scripted_request(method, params, *, status="interrupted"):
    s = FakeAppServer().on("turn/interrupt", {})
    s.script_turn("th-1", "tu-1", status=status, events=[
        {"jsonrpc": "2.0", "id": 900, "method": method, "params": params}])
    return s


@pytest.mark.parametrize("method,params,reply,kind,event", [
    ("item/commandExecution/requestApproval",
     {"threadId": "th-1", "turnId": "tu-1", "itemId": "i", "startedAtMs": 0,
      "command": "aws ec2 run-instances", "reason": "needs network"},
     {"decision": "cancel"}, "command_approval", "approval_escalated"),
    ("execCommandApproval",
     {"conversationId": "th-1", "callId": "c", "command": ["git", "push"], "cwd": "/w",
      "parsedCmd": []},
     {"decision": "abort"}, "command_approval", "approval_escalated"),
    ("item/fileChange/requestApproval",
     {"threadId": "th-1", "turnId": "tu-1", "itemId": "i", "startedAtMs": 0,
      "grantRoot": "/etc"},
     {"decision": "cancel"}, "file_approval", "approval_escalated"),
    ("applyPatchApproval",
     {"conversationId": "th-1", "callId": "c", "fileChanges": {"/etc/hosts": {}}},
     {"decision": "abort"}, "file_approval", "approval_escalated"),
    ("item/permissions/requestApproval",
     {"threadId": "th-1", "turnId": "tu-1", "itemId": "i", "startedAtMs": 0, "cwd": "/w",
      "permissions": {"network": {"enabled": True}}},
     {"permissions": {}, "scope": "turn"}, "permissions", "approval_escalated"),
    ("item/tool/requestUserInput",
     {"threadId": "th-1", "turnId": "tu-1", "itemId": "i", "isBlocking": True,
      "questions": [{"id": "q", "header": "h", "question": "Which dataset?"}]},
     {"answers": {}}, "user_input", "turn_input_required"),
    ("mcpServer/elicitation/request",
     {"threadId": "th-1", "turnId": "tu-1", "serverName": "linear", "message": "Pick a team"},
     {"action": "cancel"}, "elicitation", "turn_input_required"),
])
def test_requests_are_declined_and_escalated(method, params, reply, kind, event):
    s = scripted_request(method, params)
    c, events = make_client(s)
    r = run(c)
    assert s.replies[900] == reply  # never approves
    assert r.status == "escalated" and r.escalation.kind == kind
    assert r.escalation.summary and len(r.escalation.summary) <= 1024
    assert event in names(events)
    assert s.requests("turn/interrupt") == [{"threadId": "th-1", "turnId": "tu-1"}]


def test_escalation_summary_content():
    s = scripted_request("item/commandExecution/requestApproval", {
        "threadId": "th-1", "turnId": "tu-1", "itemId": "i", "startedAtMs": 0,
        "command": "aws ec2 run-instances", "reason": "needs network"})
    r = run(make_client(s)[0])
    assert "aws ec2 run-instances" in r.escalation.summary
    assert "needs network" in r.escalation.summary


def test_escalation_does_not_stall_without_turn_completed():
    s = FakeAppServer().on("turn/start", {"turn": {"id": "tu-1"}})
    s.after("turn/start", [{"jsonrpc": "2.0", "id": 5, "method": "item/tool/requestUserInput",
                            "params": {"threadId": "th-1", "turnId": "tu-1", "itemId": "i",
                                       "isBlocking": True, "questions": []}}])
    c, _ = make_client(s, read_timeout_s=3, clock=TickClock(1.0))
    r = run(c, turn_timeout_s=1000)
    assert r.status == "escalated" and r.escalation.kind == "user_input"


def test_unsupported_dynamic_tool_fails_and_turn_continues():
    s = scripted_request("item/tool/call", {"threadId": "th-1", "turnId": "tu-1", "callId": "c",
                                            "tool": "linear_graphql", "arguments": {}},
                         status="completed")
    c, events = make_client(s)
    r = run(c)
    assert r.status == "completed"
    reply = s.replies[900]
    assert reply["success"] is False
    assert reply["contentItems"][0]["type"] == "inputText"
    assert "unsupported_tool_call" in names(events)
    assert s.requests("turn/interrupt") == []


def test_unknown_server_request_gets_method_not_found():
    s = scripted_request("account/chatgptAuthTokens/refresh", {}, status="completed")
    c, events = make_client(s)
    assert run(c).status == "completed"
    assert s.replies[900]["code"] == -32601
    assert "other_message" in names(events)


# -- transports ---------------------------------------------------------------------------------

def test_open_transport_defaults_to_daemon_with_default_command():
    cfg = CodexConfig()
    assert cfg.command == "codex app-server" and cfg.transport == "daemon"
    with pytest.raises(AgentError) as ei:
        open_transport(CodexConfig(socket=Path("/nonexistent/sock")), CWD, {})
    assert ei.value.category == "startup_failed"


class FakeWsDaemon:
    """Minimal WebSocket server on a Unix socket; `script(conn)` drives one connection."""

    def __init__(self, script):
        self.dir = tempfile.mkdtemp(prefix="sy", dir="/tmp")  # short path for AF_UNIX
        self.path = Path(self.dir) / "s.sock"
        self.srv = socket.socket(socket.AF_UNIX)
        self.srv.bind(str(self.path))
        self.srv.listen(1)
        self.received: list = []
        self.error = None
        self.thread = threading.Thread(target=self._serve, args=(script,), daemon=True)
        self.thread.start()

    def _serve(self, script):
        try:
            conn, _ = self.srv.accept()
            conn.settimeout(5)
            head = b""
            while b"\r\n\r\n" not in head:
                head += conn.recv(1)
            assert head.startswith(b"GET / HTTP/1.1") and b"Upgrade: websocket" in head
            conn.sendall(b"HTTP/1.1 101 Switching Protocols\r\nUpgrade: websocket\r\n"
                         b"Connection: Upgrade\r\n\r\n")
            script(self, conn)
        except Exception as e:  # surfaced by the test
            self.error = e

    @staticmethod
    def frame(data: bytes, opcode=1, fin=True) -> bytes:
        n = len(data)
        b0 = (0x80 if fin else 0) | opcode
        if n < 126:
            return bytes([b0, n]) + data
        if n < 65536:
            return bytes([b0, 126]) + struct.pack(">H", n) + data
        return bytes([b0, 127]) + struct.pack(">Q", n) + data

    def read_frame(self, conn):
        def exact(n):
            buf = b""
            while len(buf) < n:
                buf += conn.recv(n - len(buf))
            return buf
        b0, b1 = exact(2)
        assert b1 & 0x80, "client frames must be masked"
        n = b1 & 0x7F
        if n == 126:
            n = struct.unpack(">H", exact(2))[0]
        elif n == 127:
            n = struct.unpack(">Q", exact(8))[0]
        mask = exact(4)
        data = bytes(b ^ mask[i % 4] for i, b in enumerate(exact(n)))
        return b0 & 0x0F, data

    def close(self):
        self.srv.close()
        shutil.rmtree(self.dir, ignore_errors=True)


def test_daemon_transport_framing_ping_fragments_and_close():
    def script(d, conn):
        op, data = d.read_frame(conn)
        d.received.append((op, json.loads(data)))
        # ping, then a fragmented text message, then a large message, then close
        body = json.dumps({"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}).encode()
        conn.sendall(d.frame(b"hi", opcode=9))
        conn.sendall(d.frame(body[:10], opcode=1, fin=False) + d.frame(body[10:], opcode=0))
        big = json.dumps({"jsonrpc": "2.0", "method": "x", "params": {"s": "a" * 70000}})
        conn.sendall(d.frame(big.encode()))
        conn.sendall(d.frame(b"{not json"))
        d.received.append(d.read_frame(conn))  # pong
        conn.sendall(d.frame(b"", opcode=8))

    d = FakeWsDaemon(script)
    try:
        t = DaemonTransport(d.path, connect_timeout_s=5)
        assert t.pid is None
        t.send({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}})
        assert t.recv(5) == {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}}
        assert len(t.recv(5)["params"]["s"]) == 70000
        assert "__malformed__" in t.recv(5)
        with pytest.raises(AgentError) as ei:
            t.recv(5)
        assert ei.value.category == "port_exit"
        t.close()
        d.thread.join(5)
        assert d.error is None
        assert d.received[0] == (1, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                     "params": {}})
        assert d.received[1] == (10, b"hi")
    finally:
        d.close()


def test_daemon_transport_recv_timeout_returns_none():
    done = threading.Event()

    def script(d, conn):
        done.wait(5)

    d = FakeWsDaemon(script)
    try:
        t = DaemonTransport(d.path, connect_timeout_s=5)
        assert t.recv(0.05) is None
        done.set()
        t.close()
    finally:
        d.close()


FAKE_STDIO_SERVER = textwrap.dedent("""
    import json, os, sys
    sys.stderr.write("diagnostic noise\\n"); sys.stderr.flush()
    for line in sys.stdin:
        msg = json.loads(line)
        if msg.get("method") == "initialize":
            out = {"jsonrpc": "2.0", "id": msg["id"], "result": {"cwd": os.getcwd()}}
            sys.stdout.write(json.dumps(out) + "\\n"); sys.stdout.flush()
        if msg.get("method") == "quit":
            break
""")


def test_stdio_transport_launch_framing_and_stderr(tmp_path):
    script = tmp_path / "server.py"
    script.write_text(FAKE_STDIO_SERVER)
    ws = tmp_path / "ws"
    ws.mkdir()
    env = {"PATH": os.environ.get("PATH", ""), "HOME": str(tmp_path)}
    cfg = CodexConfig(transport="stdio", command=f"{sys.executable} {script}")
    t = open_transport(cfg, ws, env)
    assert isinstance(t, StdioTransport) and t.pid
    try:
        c = AppServerClient(t, read_timeout_s=20, on_event=lambda e: None)
        result = c.request("initialize", {"clientInfo": {"name": "symphony", "version": "0"}})
        assert Path(result["cwd"]).resolve() == ws.resolve()  # launched in workspace cwd
        t.send({"jsonrpc": "2.0", "method": "quit"})
        with pytest.raises(AgentError) as ei:
            while True:  # stderr noise never shows up as protocol messages
                assert t.recv(20) is None or False
        assert ei.value.category == "port_exit"
    finally:
        t.close()


def test_stdio_command_not_found(tmp_path):
    env = {"PATH": "/usr/bin:/bin", "HOME": str(tmp_path)}
    t = StdioTransport("definitely-not-a-codex-binary-xyz app-server", tmp_path, env)
    try:
        with pytest.raises(AgentError) as ei:
            t.recv(20)
        assert ei.value.category == "codex_not_found"
    finally:
        t.close()


def test_thread_scoped_messages_before_our_thread_are_ignored():
    s = ready_server()
    foreign = s.server_request("item/commandExecution/requestApproval", threadId="th-tal",
                               turnId="tu-x", itemId="i", startedAtMs=0, command="ls")
    s.push(note("thread/tokenUsage/updated", threadId="th-tal", turnId="tu-x",
                tokenUsage={"total": {"inputTokens": 1, "outputTokens": 1,
                                      "totalTokens": 9_000_000}}))
    c, events = make_client(s)
    c.initialize()
    c.start_thread(cwd=CWD, policy=POLICY)
    assert foreign not in s.replies
    assert events == []


def test_unscoped_notifications_do_not_count_as_turn_activity():
    s = FakeAppServer().on("turn/start", {"turn": {"id": "tu-1"}}).on("turn/interrupt", {})
    s.after("turn/start", [
        note("account/rateLimits/updated", rateLimits={"primary": {"usedPercent": 5}}),
        note("thread/started", thread={"id": "th-other", "turns": []}),
    ] * 20)
    c, events = make_client(s, clock=TickClock(1.0))
    r = run(c, turn_timeout_s=10)
    assert r.status == "timeout" and len(s.inbox) > 0  # timed out despite the chatter
    rl = [e for e in events if e.rate_limits is not None]
    assert rl and all(not e.scoped for e in rl)
    assert all(e.method != "thread/started" for e in events)


def test_resume_interrupts_a_turn_still_running():
    s = FakeAppServer().on("thread/resume", {"thread": {"id": "th-1",
                                                        "status": {"type": "active",
                                                                   "activeFlags": []}}})
    s.on("thread/turns/list", {"data": [{"id": "tu-old", "status": "inProgress"}]})
    s.on("turn/interrupt", {})
    s.after("turn/interrupt", [note("turn/completed", threadId="th-1",
                                    turn={"id": "tu-old", "status": "interrupted"})])
    c, _ = make_client(s)
    assert c.resume_thread("th-1", cwd=CWD, policy=POLICY) == "th-1"
    assert s.requests("turn/interrupt") == [{"threadId": "th-1", "turnId": "tu-old"}]


def test_resume_fails_when_running_turn_does_not_stop():
    s = FakeAppServer().on("thread/resume", {"thread": {"id": "th-1",
                                                        "status": {"type": "active",
                                                                   "activeFlags": []}}})
    s.on("thread/turns/list", {"data": [{"id": "tu-old", "status": "inProgress"}]})
    s.on("turn/interrupt", {})
    c, _ = make_client(s, read_timeout_s=3, clock=TickClock(1.0))
    with pytest.raises(AgentError) as ei:
        c.resume_thread("th-1", cwd=CWD, policy=POLICY)
    assert ei.value.category == "turn_in_progress"


def test_resume_of_idle_thread_does_not_list_turns():
    s = FakeAppServer().on("thread/resume", {"thread": {"id": "th-1",
                                                        "status": {"type": "idle"}}})
    c, _ = make_client(s)
    c.resume_thread("th-1", cwd=CWD, policy=POLICY)
    assert s.requests("thread/turns/list") == []

"""Linear MCP transport: Keychain record, OAuth login and refresh against fake endpoints, error
mapping, and the synchronous session against a local in-process MCP server.

Nothing here reaches Linear, the real Keychain or a browser."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import socket
import threading
import time
import urllib.parse
import urllib.request
from types import SimpleNamespace

import httpx2
import pytest

from fakes import FakeKeychain
from symphony import mcp_linear
from symphony.errors import TrackerError
from symphony.mcp_linear import (
    Keychain,
    KeychainTokenStorage,
    LoginRequired,
    McpSession,
    map_error,
    service_auth,
    tool_payload,
)

BASE = "https://mcp.test"
MCP = f"{BASE}/mcp"


# -- Keychain ---------------------------------------------------------------------------------

class FakeSecurity:
    """Stands in for /usr/bin/security; records argv and stdin."""

    def __init__(self):
        self.items: dict[tuple[str, str], str] = {}
        self.calls: list[tuple[list[str], str | None]] = []

    def __call__(self, argv, input=None, **kw):
        self.calls.append((argv, input))
        if argv[:2] == ["security", "-i"]:
            parts = input.split()
            svc, acct, data = parts[parts.index("-s") + 1], parts[parts.index("-a") + 1], \
                parts[parts.index("-X") + 1]
            self.items[(svc, acct)] = bytes.fromhex(data).decode()
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        svc, acct = argv[argv.index("-s") + 1], argv[argv.index("-a") + 1]
        if argv[1] == "find-generic-password":
            if (svc, acct) not in self.items:
                return SimpleNamespace(returncode=44, stdout="", stderr="not found")
            return SimpleNamespace(returncode=0, stdout=self.items[(svc, acct)] + "\n", stderr="")
        if argv[1] == "delete-generic-password":
            self.items.pop((svc, acct), None)
            return SimpleNamespace(returncode=0, stdout="", stderr="")
        raise AssertionError(argv)


def test_keychain_writes_secret_on_stdin_never_in_argv():
    sec = FakeSecurity()
    kc = Keychain(run=sec)
    kc.write({"refresh_token": "rt-secret"})
    assert kc.read() == {"refresh_token": "rt-secret"}
    for argv, stdin in sec.calls:
        assert "rt-secret" not in " ".join(argv)
        assert stdin is None or "rt-secret" not in stdin  # hex-encoded
    assert sec.calls[0][0] == ["security", "-i"]
    assert ("symphony-linear-mcp", "linear-mcp") in sec.items
    assert "rt-secret" not in repr(kc)
    kc.delete()
    assert kc.read() is None


def test_keychain_reads_hex_output_and_rejects_odd_names():
    sec = FakeSecurity()
    sec.items[("symphony-linear-mcp", "linear-mcp")] = json.dumps({"a": 1}).encode().hex()
    assert Keychain(run=sec).read() == {"a": 1}
    with pytest.raises(ValueError):
        Keychain("svc; rm -rf ~")


def test_keychain_write_failure_is_tracker_auth():
    def broken(argv, **kw):
        return SimpleNamespace(returncode=0 if argv[1] == "-i" else 44, stdout="", stderr="")
    with pytest.raises(TrackerError) as e:
        Keychain(run=broken).write({"refresh_token": "x"})
    assert e.value.category == "tracker_auth" and "x" not in e.value.message.split()


# -- fake OAuth authorization server + MCP endpoint ------------------------------------------------

class FakeOAuth:
    def __init__(self):
        self.registered: list[dict] = []
        self.token_requests: list[dict] = []
        self.valid = set()
        self.rotation = 0
        self.authorize: dict | None = None

    def metadata(self):
        return {"issuer": BASE, "authorization_endpoint": f"{BASE}/authorize",
                "token_endpoint": f"{BASE}/token", "registration_endpoint": f"{BASE}/register",
                "response_types_supported": ["code"], "code_challenge_methods_supported": ["S256"],
                "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none"]}

    def _tokens(self):
        self.rotation += 1
        at = f"at-{self.rotation}"
        self.valid = {at}
        return {"access_token": at, "refresh_token": f"rt-{self.rotation}",
                "token_type": "Bearer", "expires_in": 3600}

    def handler(self, request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/mcp":
            auth = request.headers.get("authorization", "")
            if auth.removeprefix("Bearer ") in self.valid:
                return httpx2.Response(200, json={"ok": True})
            return httpx2.Response(401, headers={"WWW-Authenticate": (
                f'Bearer resource_metadata="{BASE}/.well-known/oauth-protected-resource/mcp"')})
        if path.startswith("/.well-known/oauth-protected-resource"):
            return httpx2.Response(200, json={"resource": MCP, "authorization_servers": [BASE]})
        if path.startswith("/.well-known/oauth-authorization-server"):
            return httpx2.Response(200, json=self.metadata())
        if path == "/register":
            body = json.loads(request.content)
            self.registered.append(body)
            return httpx2.Response(201, json={**body, "client_id": "cid-1"})
        if path == "/token":
            form = dict(urllib.parse.parse_qsl(request.content.decode()))
            self.token_requests.append(form)
            if form["grant_type"] == "authorization_code":
                challenge = base64.urlsafe_b64encode(hashlib.sha256(
                    form["code_verifier"].encode()).digest()).decode().rstrip("=")
                assert form["code"] == "the-code" and form["client_id"] == "cid-1"
                assert challenge == self.authorize["code_challenge"]
                return httpx2.Response(200, json=self._tokens())
            if form["grant_type"] == "refresh_token":
                if form["refresh_token"] != f"rt-{self.rotation}":
                    return httpx2.Response(400, json={"error": "invalid_grant"})
                return httpx2.Response(200, json=self._tokens())
        return httpx2.Response(404)


class MockSession:
    """session_factory for login(): drives the provider through httpx2 with FakeOAuth."""

    def __init__(self, oauth: FakeOAuth):
        self.oauth = oauth

    def __call__(self, url, auth, timeout_s=60):
        self.url, self.auth = url, auth
        return self

    def list_tools(self):
        async def go():
            async with httpx2.AsyncClient(auth=self.auth,
                                          transport=httpx2.MockTransport(self.oauth.handler)) as c:
                r = await c.post(self.url, json={"jsonrpc": "2.0", "method": "tools/list"})
                assert r.status_code == 200
        asyncio.run(go())
        return ["get_issue", "list_issues", "save_comment"]

    def close(self):
        pass


def fake_browser(oauth: FakeOAuth):
    def open_browser(auth_url):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlparse(auth_url).query))
        oauth.authorize = q
        assert q["code_challenge_method"] == "S256" and q["client_id"] == "cid-1"
        target = f"{q['redirect_uri']}?code=the-code&state={q['state']}"
        threading.Thread(target=lambda: urllib.request.urlopen(target, timeout=5).read(),
                         daemon=True).start()
    return open_browser


def test_login_registers_public_client_with_pkce_and_stores_tokens():
    oauth, kc = FakeOAuth(), FakeKeychain({"stale": True})
    out = []
    tools = mcp_linear.login(url=MCP, keychain=kc, port=0, open_browser=fake_browser(oauth),
                             session_factory=MockSession(oauth), timeout_s=10,
                             out=out.append)
    assert tools == ["get_issue", "list_issues", "save_comment"]
    reg = oauth.registered[0]
    assert reg["token_endpoint_auth_method"] == "none" and reg["client_name"] == "symphony"
    assert reg["redirect_uris"][0].startswith("http://127.0.0.1:")
    assert set(reg["grant_types"]) == {"authorization_code", "refresh_token"}
    rec = kc.record
    assert "stale" not in rec  # login starts from an empty record
    assert rec["refresh_token"] == "rt-1" and rec["access_token"] == "at-1"
    assert rec["client_info"]["client_id"] == "cid-1"
    assert rec["metadata"]["token_endpoint"] == f"{BASE}/token"
    assert rec["expires_at"] > time.time() + 3000
    assert not any("rt-1" in line or "at-1" in line for line in out)


def stored_record(oauth: FakeOAuth, *, expired: bool) -> dict:
    oauth.rotation = 1
    return {"client_info": {"client_id": "cid-1", "redirect_uris": ["http://127.0.0.1:1/callback"],
                            "token_endpoint_auth_method": "none",
                            "grant_types": ["authorization_code", "refresh_token"],
                            "response_types": ["code"]},
            "metadata": oauth.metadata(), "refresh_token": "rt-1", "access_token": "at-1",
            "expires_at": time.time() + (-10 if expired else 3600)}


async def request_with(provider, oauth):
    async with httpx2.AsyncClient(auth=provider,
                                  transport=httpx2.MockTransport(oauth.handler)) as c:
        return await c.post(MCP, json={})


def test_service_refreshes_expired_token_without_browser_and_stores_rotation():
    oauth = FakeOAuth()
    kc = FakeKeychain(stored_record(oauth, expired=True))
    r = asyncio.run(request_with(service_auth(KeychainTokenStorage(kc), MCP), oauth))
    assert r.status_code == 200
    assert [t["grant_type"] for t in oauth.token_requests] == ["refresh_token"]
    assert kc.record["refresh_token"] == "rt-2" and kc.record["access_token"] == "at-2"
    assert oauth.registered == []


def test_service_uses_stored_access_token_until_expiry():
    oauth = FakeOAuth()
    kc = FakeKeychain(stored_record(oauth, expired=False))
    oauth.valid = {"at-1"}
    r = asyncio.run(request_with(service_auth(KeychainTokenStorage(kc), MCP), oauth))
    assert r.status_code == 200 and oauth.token_requests == []


def test_service_without_login_never_opens_a_browser():
    oauth = FakeOAuth()
    with pytest.raises(BaseException) as e:
        asyncio.run(request_with(service_auth(KeychainTokenStorage(FakeKeychain()), MCP), oauth))
    err = map_error(e.value)
    assert err.category == "tracker_auth" and "symphony login" in err.message


def test_service_session_fails_fast_without_login():
    s = mcp_linear.service_session(keychain=FakeKeychain())
    with pytest.raises(TrackerError) as e:
        s.call("get_user", {"query": "me"})
    assert e.value.category == "tracker_auth"
    s.close()


# -- error and result mapping ---------------------------------------------------------------------

def test_map_error_categories():
    resp = SimpleNamespace(status_code=429)
    assert map_error(type("E", (Exception,), {"response": resp})()).category == \
        "tracker_rate_limited"
    group = ExceptionGroup("g", [ValueError("x"), LoginRequired("no")])
    assert map_error(group).category == "tracker_auth"
    assert map_error(OSError("down")).category == "tracker_request"


def text_result(text, *, error=False, structured=None):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)], is_error=error,
                           structured_content=structured)


def test_tool_payload():
    assert tool_payload("t", text_result('{"a": 1}')) == {"a": 1}
    assert tool_payload("t", text_result("plain")) == "plain"
    assert tool_payload("t", text_result("x", structured={"b": 2})) == {"b": 2}
    with pytest.raises(TrackerError) as e:
        tool_payload("get_issue", text_result("Issue not found", error=True))
    assert e.value.category == "tracker_tool_error" and "not found" in e.value.message
    with pytest.raises(TrackerError) as e:
        tool_payload("list_issues", text_result("Rate limit exceeded", error=True))
    assert e.value.category == "tracker_rate_limited"


# -- McpSession against a local in-process streamable-HTTP server -----------------------------------

@pytest.fixture
def local_server():
    uvicorn = pytest.importorskip("uvicorn")
    from mcp.server.mcpserver import MCPServer

    srv = MCPServer("fake-linear")

    @srv.tool()
    def list_issues(team: str, state: str = "", limit: int = 50) -> dict:
        return {"issues": [{"id": "TAL-1", "status": state}], "hasNextPage": False}

    @srv.tool()
    def boom() -> str:
        raise ValueError("nope")

    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    server = uvicorn.Server(uvicorn.Config(srv.streamable_http_app(), host="127.0.0.1",
                                           port=port, log_level="error"))
    t = threading.Thread(target=server.run, daemon=True)
    t.start()
    deadline = time.time() + 10
    while not server.started and time.time() < deadline:
        time.sleep(0.02)
    yield f"http://127.0.0.1:{port}/mcp"
    server.should_exit = True
    t.join(5)


def test_session_calls_tools_from_many_threads_and_recovers(local_server):
    from concurrent.futures import ThreadPoolExecutor

    s = McpSession(local_server, timeout_s=10)
    try:
        assert sorted(s.list_tools()) == ["boom", "list_issues"]
        with ThreadPoolExecutor(4) as ex:
            states = list(ex.map(lambda i: s.call("list_issues", {"team": "TAL", "state": str(i)})
                                 ["issues"][0]["status"], range(6)))
        assert states == [str(i) for i in range(6)]
        with pytest.raises(TrackerError) as e:
            s.call("boom", {})
        assert e.value.category == "tracker_tool_error"
        assert s.call("list_issues", {"team": "TAL"})["issues"][0]["id"] == "TAL-1"
    finally:
        s.close()

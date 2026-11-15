"""Linear MCP transport: OAuth login, Keychain token storage and a synchronous tool caller.

Symphony is its own OAuth client of Linear's MCP server (dynamic client registration, public
client, PKCE, localhost redirect). `symphony login` opens the browser once; afterwards the
service refreshes tokens without a browser. The token record lives in the macOS Keychain
(service `symphony-linear-mcp`) and is never logged or printed.

The `mcp` SDK is async; `McpSession` runs it on one background event loop so the threaded
orchestrator, workers and CLI can call tools synchronously.
"""

from __future__ import annotations

import asyncio
import json
import re
import subprocess
import threading
import time
import urllib.parse
import webbrowser
from collections.abc import Callable
from contextlib import AsyncExitStack
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import httpx2
from mcp.client import Client
from mcp.client.auth import OAuthClientProvider
from mcp.client.streamable_http import streamable_http_client
from mcp.shared.auth import (
    AuthorizationCodeResult,
    OAuthClientInformationFull,
    OAuthClientMetadata,
    OAuthMetadata,
    OAuthToken,
)

from .errors import TrackerError

MCP_URL = "https://mcp.linear.app/mcp"
KEYCHAIN_SERVICE = "symphony-linear-mcp"
KEYCHAIN_ACCOUNT = "linear-mcp"
REDIRECT_PORT = 47615
CALLBACK_PATH = "/callback"
LOGIN_TIMEOUT_S = 300
_ERROR_TEXT_LIMIT = 300
_NAME_RE = re.compile(r"^[\w.@-]+$")
LOGIN_HINT = "Linear MCP login required: run `symphony login`"


def _clip(text: str) -> str:
    return text if len(text) <= _ERROR_TEXT_LIMIT else text[:_ERROR_TEXT_LIMIT] + "..."


# -- Keychain --------------------------------------------------------------------------------

class Keychain:
    """One generic-password item holding a JSON record. Writes go through `security -i` on
    stdin (hex data), so the secret never appears in a process argument list."""

    def __init__(self, service: str = KEYCHAIN_SERVICE, account: str = KEYCHAIN_ACCOUNT,
                 run: Callable[..., Any] = subprocess.run):
        if not _NAME_RE.match(service) or not _NAME_RE.match(account):
            raise ValueError("keychain service and account must be simple names")
        self.service, self.account, self._run = service, account, run

    def __repr__(self) -> str:
        return f"Keychain(service={self.service!r})"

    def read(self) -> dict | None:
        try:
            r = self._run(["security", "find-generic-password", "-s", self.service,
                           "-a", self.account, "-w"], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        if r.returncode != 0 or not (r.stdout or "").strip():
            return None
        raw = r.stdout.strip()
        for decode in (lambda s: s, lambda s: bytes.fromhex(s).decode()):
            try:
                record = json.loads(decode(raw))
            except (ValueError, UnicodeDecodeError):
                continue
            return record if isinstance(record, dict) else None
        return None

    def write(self, record: dict) -> None:
        data = json.dumps(record, separators=(",", ":"), sort_keys=True)
        cmd = (f"add-generic-password -U -s {self.service} -a {self.account} "
               f"-X {data.encode().hex()}\n")
        try:
            self._run(["security", "-i"], input=cmd, capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError) as e:
            raise TrackerError("tracker_auth", f"could not write Keychain item: {type(e).__name__}")
        if self.read() != json.loads(data):
            raise TrackerError("tracker_auth",
                               f"Keychain item {self.service} did not store the token record")

    def delete(self) -> None:
        try:
            self._run(["security", "delete-generic-password", "-s", self.service,
                       "-a", self.account], capture_output=True, text=True, timeout=10)
        except (OSError, subprocess.SubprocessError):
            pass


class KeychainTokenStorage:
    """mcp TokenStorage over one Keychain record:
    {client_info, metadata, access_token, expires_at, refresh_token, scope}.
    Each read goes to the Keychain, so a token another process refreshed is picked up."""

    def __init__(self, keychain: Keychain, clock: Callable[[], float] = time.time):
        self.keychain = keychain
        self.clock = clock
        self._lock = threading.Lock()

    def record(self) -> dict:
        return self.keychain.read() or {}

    def has_login(self) -> bool:
        r = self.record()
        return bool(r.get("client_info") and r.get("refresh_token"))

    def _update(self, **changes) -> None:
        with self._lock:
            r = self.record()
            r.update(changes)
            self.keychain.write({k: v for k, v in r.items() if v is not None})

    async def get_tokens(self) -> OAuthToken | None:
        r = self.record()
        if not r.get("refresh_token") and not r.get("access_token"):
            return None
        return OAuthToken(access_token=r.get("access_token") or "",
                          refresh_token=r.get("refresh_token"), scope=r.get("scope"))

    async def set_tokens(self, tokens: OAuthToken) -> None:
        # Keep an access token only with a known expiry: an unknown one would look valid
        # forever and a 401 would then need a browser instead of a refresh.
        keep = tokens.expires_in is not None
        r = self.record()
        self._update(access_token=tokens.access_token if keep else None,
                     expires_at=self.clock() + tokens.expires_in if keep else None,
                     refresh_token=tokens.refresh_token or r.get("refresh_token"),
                     scope=tokens.scope or r.get("scope"))

    async def get_client_info(self) -> OAuthClientInformationFull | None:
        info = self.record().get("client_info")
        return OAuthClientInformationFull.model_validate(info) if info else None

    async def set_client_info(self, client_info: OAuthClientInformationFull) -> None:
        self._update(client_info=client_info.model_dump(mode="json", exclude_none=True))

    def expires_at(self) -> float | None:
        v = self.record().get("expires_at")
        return float(v) if isinstance(v, (int, float)) else None

    def metadata(self) -> OAuthMetadata | None:
        m = self.record().get("metadata")
        try:
            return OAuthMetadata.model_validate(m) if m else None
        except ValueError:
            return None

    def set_metadata(self, metadata: OAuthMetadata | None) -> None:
        if metadata is not None:
            self._update(metadata=metadata.model_dump(mode="json", exclude_none=True))


# -- OAuth provider --------------------------------------------------------------------------

class LoginRequired(Exception):
    """A browser authorization would be needed; the service never opens one."""


def client_metadata(port: int = REDIRECT_PORT) -> OAuthClientMetadata:
    return OAuthClientMetadata(
        client_name="symphony",
        redirect_uris=[f"http://127.0.0.1:{port}{CALLBACK_PATH}"],
        grant_types=["authorization_code", "refresh_token"],
        response_types=["code"],
        token_endpoint_auth_method="none",
    )


class StoredOAuthProvider(OAuthClientProvider):
    """Also restores the access-token expiry and the authorization server metadata from
    storage, so an expired access token is refreshed at the stored token endpoint instead of
    starting a browser flow."""

    async def _initialize(self) -> None:
        await super()._initialize()
        storage = self.context.storage
        if isinstance(storage, KeychainTokenStorage):
            self.context.token_expiry_time = storage.expires_at()
            if self.context.oauth_metadata is None:
                self.context.oauth_metadata = storage.metadata()


async def _refuse_browser(url: str) -> None:
    raise LoginRequired(LOGIN_HINT)


async def _refuse_callback() -> AuthorizationCodeResult:
    raise LoginRequired(LOGIN_HINT)


def service_auth(storage: KeychainTokenStorage, url: str = MCP_URL,
                 port: int = REDIRECT_PORT) -> StoredOAuthProvider:
    """Non-interactive auth for the service and `bridge-event`: refresh only, never a browser."""
    return StoredOAuthProvider(url, client_metadata(port), storage,
                               redirect_handler=_refuse_browser,
                               callback_handler=_refuse_callback)


# -- synchronous session ---------------------------------------------------------------------

def _leaves(exc: BaseException):
    seen: set[int] = set()
    stack = [exc]
    while stack:
        e = stack.pop()
        if e is None or id(e) in seen:
            continue
        seen.add(id(e))
        yield e
        stack.extend(getattr(e, "exceptions", ()) or ())
        stack.extend([e.__cause__, e.__context__])


def map_error(exc: BaseException) -> TrackerError:
    """Exception from the SDK -> TrackerError with a secret-free message."""
    if isinstance(exc, TrackerError):
        return exc
    leaves = list(_leaves(exc))
    for e in leaves:
        if isinstance(e, TrackerError):
            return e
    for e in leaves:
        if isinstance(e, LoginRequired) or type(e).__name__.startswith("OAuth"):
            return TrackerError("tracker_auth", LOGIN_HINT)
    for e in leaves:
        response = getattr(e, "response", None)
        status = getattr(response, "status_code", None)
        if status == 429:
            return TrackerError("tracker_rate_limited", "Linear MCP rate limit (HTTP 429)")
        if status == 401:
            return TrackerError("tracker_auth", LOGIN_HINT)
        if isinstance(status, int) and status >= 400:
            return TrackerError("tracker_status", f"Linear MCP HTTP {status}")
    for e in leaves:
        if type(e).__name__ in ("McpError", "MCPError"):
            return TrackerError("tracker_response", _clip(f"Linear MCP error: {e}"))
    return TrackerError("tracker_request",
                        _clip(f"Linear MCP request failed: {type(exc).__name__}: {exc}"))


def tool_payload(name: str, result: Any) -> Any:
    """CallToolResult -> structured content, else the JSON-decoded text, else the text."""
    texts = [getattr(c, "text", "") for c in getattr(result, "content", None) or []
             if getattr(c, "type", None) == "text"]
    text = "".join(texts)
    if getattr(result, "is_error", False):
        low = text.lower()
        if "rate limit" in low or "ratelimit" in low or "too many requests" in low:
            raise TrackerError("tracker_rate_limited", _clip(f"{name}: {text}"))
        raise TrackerError("tracker_tool_error", _clip(f"{name}: {text or 'tool error'}"))
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return structured
    try:
        return json.loads(text)
    except ValueError:
        return text


class McpSession:
    """Thread-safe synchronous facade over one `mcp` Client on a background event loop.

    One owner task holds the connection open (anyio scopes must exit in the task that entered
    them). A failed call drops the connection; the next call reconnects."""

    def __init__(self, url: str = MCP_URL, auth: httpx2.Auth | None = None, *,
                 timeout_s: float = 60.0, before_connect: Callable[[], None] | None = None):
        self.url = url
        self._auth = auth
        self.timeout_s = timeout_s
        self._before_connect = before_connect
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._start_lock = threading.Lock()
        self._client: Client | None = None
        self._owner: asyncio.Task | None = None
        self._closing: asyncio.Event | None = None
        self._connect_lock: asyncio.Lock | None = None

    def __repr__(self) -> str:
        return f"McpSession(url={self.url!r})"

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        with self._start_lock:
            if self._loop is None:
                loop = asyncio.new_event_loop()
                t = threading.Thread(target=loop.run_forever, name="symphony-mcp", daemon=True)
                t.start()
                self._loop, self._thread = loop, t
            return self._loop

    def _run(self, coro) -> Any:
        fut = asyncio.run_coroutine_threadsafe(coro, self._ensure_loop())
        try:
            return fut.result(self.timeout_s * 2 + 30)
        except TimeoutError:
            fut.cancel()
            raise TrackerError("tracker_request", "Linear MCP call timed out")

    async def _own(self, ready: asyncio.Future) -> None:
        try:
            async with AsyncExitStack() as stack:
                http = await stack.enter_async_context(httpx2.AsyncClient(
                    auth=self._auth, timeout=httpx2.Timeout(30.0, read=self.timeout_s)))
                client = Client(streamable_http_client(self.url, http_client=http),
                                cache=None, read_timeout_seconds=self.timeout_s,
                                client_info=None)
                ready.set_result(await stack.enter_async_context(client))
                await self._closing.wait()
        except BaseException as e:  # noqa: BLE001 - reported through `ready` or on next call
            if not ready.done():
                ready.set_exception(e)
        finally:
            self._client = None

    async def _connected(self) -> Client:
        if self._connect_lock is None:
            self._connect_lock = asyncio.Lock()
        async with self._connect_lock:
            if self._client is not None and self._owner is not None and not self._owner.done():
                return self._client
            if self._before_connect is not None:
                self._before_connect()
            self._closing = asyncio.Event()
            ready = asyncio.get_running_loop().create_future()
            self._owner = asyncio.ensure_future(self._own(ready))
            self._client = await asyncio.wait_for(ready, self.timeout_s + 30)
            return self._client

    async def _drop(self) -> None:
        if self._closing is not None:
            self._closing.set()
        owner, self._client = self._owner, None
        if owner is not None:
            try:
                await asyncio.wait_for(asyncio.shield(owner), 5)
            except BaseException:  # noqa: BLE001 - best effort
                pass

    async def _call(self, name: str, args: dict) -> Any:
        try:
            client = await self._connected()
            result = await client.call_tool(name, args)
        except BaseException as e:  # noqa: BLE001
            await self._drop()
            raise map_error(e) from None
        return tool_payload(name, result)

    async def _list(self) -> list[str]:
        try:
            client = await self._connected()
            result = await client.list_tools()
        except BaseException as e:  # noqa: BLE001
            await self._drop()
            raise map_error(e) from None
        return [t.name for t in result.tools]

    def call(self, name: str, args: dict) -> Any:
        """Call one tool; returns its decoded payload. Raises TrackerError."""
        return self._run(self._call(name, args))

    def list_tools(self) -> list[str]:
        return self._run(self._list())

    def close(self) -> None:
        if self._loop is None:
            return
        try:
            self._run(self._drop())
        except TrackerError:
            pass
        self._loop.call_soon_threadsafe(self._loop.stop)


def service_session(*, url: str = MCP_URL, keychain_service: str = KEYCHAIN_SERVICE,
                    keychain: Keychain | None = None) -> McpSession:
    """The session the service and `bridge-event` use. Fails fast without a stored login."""
    storage = KeychainTokenStorage(keychain or Keychain(keychain_service))

    def require_login() -> None:
        if not storage.has_login():
            raise TrackerError("tracker_auth", LOGIN_HINT)

    return McpSession(url, service_auth(storage, url), before_connect=require_login)


# -- interactive login -----------------------------------------------------------------------

class CallbackServer:
    """Localhost HTTP server that receives the authorization redirect once."""

    def __init__(self, port: int = REDIRECT_PORT):
        self.result: dict | None = None
        self._done = threading.Event()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):  # noqa: N802 - http.server API
                parsed = urllib.parse.urlparse(self.path)
                if parsed.path != CALLBACK_PATH:
                    self.send_response(404)
                    self.end_headers()
                    return
                q = urllib.parse.parse_qs(parsed.query)
                outer.result = {k: v[0] for k, v in q.items() if v}
                ok = "code" in outer.result
                self.send_response(200 if ok else 400)
                self.send_header("Content-Type", "text/plain; charset=utf-8")
                self.end_headers()
                self.wfile.write(b"Symphony is logged in to Linear. You can close this tab.\n"
                                 if ok else b"Linear authorization failed.\n")
                outer._done.set()

            def log_message(self, *args):  # keep the code out of stderr
                pass

        self.server = HTTPServer(("127.0.0.1", port), Handler)
        self.port = self.server.server_address[1]
        self._thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self._thread.start()

    def submit(self, pasted: str) -> bool:
        """Accept the redirect address pasted by hand, for a browser on another machine.
        Returns False (and changes nothing) if the text holds no authorization code."""
        text = pasted.strip().strip("'\"")
        parsed = urllib.parse.urlparse(text)
        query = parsed.query if parsed.scheme else text.lstrip("?")
        if parsed.scheme and parsed.path != CALLBACK_PATH:
            return False
        q = {k: v[0] for k, v in urllib.parse.parse_qs(query).items() if v}
        if "code" not in q and "error" not in q:
            return False
        if not self._done.is_set():
            self.result = q
            self._done.set()
        return True

    def wait(self, timeout_s: float) -> dict:
        if not self._done.wait(timeout_s):
            raise LoginRequired("timed out waiting for the browser authorization")
        return self.result or {}

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


def login(*, url: str = MCP_URL, keychain: Keychain | None = None, port: int = REDIRECT_PORT,
          open_browser: Callable[[str], Any] = webbrowser.open,
          session_factory: Callable[..., McpSession] = McpSession,
          timeout_s: float = LOGIN_TIMEOUT_S, out: Callable[[str], None] = print,
          paste_input: Callable[[], str] | None = None) -> list[str]:
    """Register Symphony as an OAuth client, authorize in the browser, store the tokens in the
    Keychain, then list the server's tools. Starts from an empty record each time.

    With `paste_input` (for example `input` on a terminal), the redirect address can also be
    pasted back by hand, for a browser on another machine that cannot reach 127.0.0.1 here.
    Whichever arrives first, the local redirect or the paste, completes the login."""
    keychain = keychain or Keychain()
    keychain.delete()
    storage = KeychainTokenStorage(keychain)
    callback = CallbackServer(port)

    def read_pastes() -> None:
        while True:
            try:
                line = paste_input()
            except (EOFError, OSError):
                return
            if callback.submit(line):
                return
            out("That doesn't contain an authorization code. Paste the whole address that "
                f"starts with http://127.0.0.1:{callback.port}{CALLBACK_PATH}?code=")

    async def redirect(auth_url: str) -> None:
        out("Opening the browser to authorize Symphony in Linear. If it does not open, visit:")
        out(auth_url)
        open_browser(auth_url)
        if paste_input is not None:
            out("If your browser is on another machine, the page it lands on after you approve "
                "won't load. Copy that page's whole address and paste it here, then press Enter:")
            threading.Thread(target=read_pastes, daemon=True).start()

    async def wait_callback() -> AuthorizationCodeResult:
        q = await asyncio.to_thread(callback.wait, timeout_s)
        if "code" not in q:
            raise LoginRequired(f"authorization failed: {q.get('error', 'no code')}")
        return AuthorizationCodeResult(code=q["code"], state=q.get("state"), iss=q.get("iss"))

    provider = StoredOAuthProvider(url, client_metadata(callback.port), storage,
                                   redirect_handler=redirect, callback_handler=wait_callback)
    session = session_factory(url, provider, timeout_s=max(60.0, timeout_s))
    try:
        tools = session.list_tools()
        storage.set_metadata(provider.context.oauth_metadata)
    finally:
        session.close()
        callback.close()
    if not storage.has_login():
        raise TrackerError("tracker_auth", "login finished without a refresh token")
    return tools

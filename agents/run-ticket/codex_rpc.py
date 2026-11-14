"""Minimal JSON-RPC client for the local Codex app-server daemon (WebSocket over a Unix socket)."""
import base64, json, os, socket, struct

SOCK = os.path.expanduser("~/.codex/app-server-control/app-server-control.sock")


class Codex:
    def __init__(self, sock=SOCK, timeout=60):
        self.s = socket.socket(socket.AF_UNIX)
        self.s.settimeout(timeout)
        self.s.connect(sock)
        key = base64.b64encode(os.urandom(16)).decode()
        self.s.sendall(("GET / HTTP/1.1\r\nHost: localhost\r\nUpgrade: websocket\r\nConnection: Upgrade\r\n"
                        f"Sec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n").encode())
        head = b""
        while b"\r\n\r\n" not in head:
            head += self.s.recv(1)
        if b" 101 " not in head.split(b"\r\n", 1)[0]:
            raise ConnectionError(head.decode(errors="replace"))
        self.n = 0
        self.notes = []

    def _recv_exact(self, n):
        buf = b""
        while len(buf) < n:
            chunk = self.s.recv(n - len(buf))
            if not chunk:
                raise ConnectionError("socket closed")
            buf += chunk
        return buf

    def _send(self, obj, opcode=1):
        data = json.dumps(obj).encode() if opcode == 1 else obj
        mask = os.urandom(4)
        n = len(data)
        hdr = bytes([0x80 | opcode])
        if n < 126:
            hdr += bytes([0x80 | n])
        elif n < 65536:
            hdr += bytes([0x80 | 126]) + struct.pack(">H", n)
        else:
            hdr += bytes([0x80 | 127]) + struct.pack(">Q", n)
        self.s.sendall(hdr + mask + bytes(b ^ mask[i % 4] for i, b in enumerate(data)))

    def _recv(self):
        parts = []
        while True:
            b0, b1 = self._recv_exact(2)
            op, n = b0 & 0x0F, b1 & 0x7F
            if n == 126:
                n = struct.unpack(">H", self._recv_exact(2))[0]
            elif n == 127:
                n = struct.unpack(">Q", self._recv_exact(8))[0]
            mask = self._recv_exact(4) if b1 & 0x80 else None
            data = self._recv_exact(n)
            if mask:
                data = bytes(b ^ mask[i % 4] for i, b in enumerate(data))
            if op == 9:  # ping
                self._send(data, opcode=10)
                continue
            if op == 8:
                raise ConnectionError("server closed")
            if op in (0, 1, 2):
                parts.append(data)
                if b0 & 0x80:
                    return json.loads(b"".join(parts))

    def call(self, method, params=None):
        self.n += 1
        i = self.n
        self._send({"jsonrpc": "2.0", "id": i, "method": method, "params": params or {}})
        while True:
            m = self._recv()
            if m.get("id") == i and ("result" in m or "error" in m):
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m["result"]
            self.notes.append(m)

    def notify(self, method, params=None):
        msg = {"jsonrpc": "2.0", "method": method}
        if params is not None:
            msg["params"] = params
        self._send(msg)

    def close(self):
        try:
            self._send(b"", opcode=8)
        finally:
            self.s.close()


def connect(name="run-ticket"):
    c = Codex()
    c.call("initialize", {"clientInfo": {"name": name, "version": "0.1"}})
    c.notify("initialized")
    return c

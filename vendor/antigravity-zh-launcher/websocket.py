# Adapted from guuguo/antigravity-zh-launcher at 057ca7cd9bbc5e55ac8fa6822905f164b1bc2e4c, see LICENSE.
import base64
import hashlib
import json
import os
import socket
import struct
import time


class MiniWebSocket:
    def __init__(self, port, path, timeout=5.0):
        self.sock = socket.create_connection(("127.0.0.1", port), timeout=timeout)
        self.buf = bytearray()
        self._id = 1

        key = base64.b64encode(os.urandom(16)).decode("ascii")
        handshake = (
            f"GET {path} HTTP/1.1\r\n"
            f"Host: 127.0.0.1:{port}\r\n"
            f"Upgrade: websocket\r\n"
            f"Connection: Upgrade\r\n"
            f"Sec-WebSocket-Key: {key}\r\n"
            f"Sec-WebSocket-Version: 13\r\n\r\n"
        ).encode("ascii")
        self.sock.sendall(handshake)

        while b"\r\n\r\n" not in self.buf:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("WebSocket 握手连接关闭")
            self.buf.extend(chunk)
        header_end = self.buf.index(b"\r\n\r\n") + 4
        header = bytes(self.buf[:header_end]).decode("latin1")
        del self.buf[:header_end]
        if " 101 " not in header.splitlines()[0]:
            raise ConnectionError(f"WebSocket 握手失败: {header.splitlines()[0]}")

    def close(self):
        try:
            self.sock.close()
        except Exception:
            pass

    def _read_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(max(4096, n - len(self.buf)))
            if not chunk:
                raise EOFError("WebSocket 连接已断开")
            self.buf.extend(chunk)
        out = bytes(self.buf[:n])
        del self.buf[:n]
        return out

    def _send_frame(self, opcode, payload):
        mask = os.urandom(4)
        length = len(payload)
        header = bytearray([0x80 | (opcode & 0x0F)])
        if length < 126:
            header.append(0x80 | length)
        elif length <= 0xFFFF:
            header.append(0x80 | 126)
            header.extend(struct.pack("!H", length))
        else:
            header.append(0x80 | 127)
            header.extend(struct.pack("!Q", length))
        header.extend(mask)
        masked = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))
        self.sock.sendall(bytes(header) + masked)

    def send_text(self, text):
        self._send_frame(0x1, text.encode("utf-8"))

    def recv_text(self, timeout=5.0):
        self.sock.settimeout(timeout)
        fragments = []
        while True:
            b1, b2 = self._read_exact(2)
            fin = (b1 & 0x80) != 0
            opcode = b1 & 0x0F
            masked = (b2 & 0x80) != 0
            length = b2 & 0x7F
            if length == 126:
                length = struct.unpack("!H", self._read_exact(2))[0]
            elif length == 127:
                length = struct.unpack("!Q", self._read_exact(8))[0]
            mask = self._read_exact(4) if masked else b""
            payload = self._read_exact(length) if length > 0 else b""
            if masked:
                payload = bytes(b ^ mask[i & 3] for i, b in enumerate(payload))

            if opcode == 0x8:
                raise EOFError("WebSocket close frame")
            if opcode == 0x9:
                self._send_frame(0xA, payload)
                continue
            if opcode == 0xA:
                continue
            if opcode in (0x1, 0x2, 0x0):
                fragments.append(payload)
                if fin:
                    return b"".join(fragments).decode("utf-8", errors="replace")

    def call(self, method, params=None, timeout=6.0):
        req_id = self._id
        self._id += 1
        self.send_text(json.dumps({"id": req_id, "method": method, "params": params or {}}))
        deadline = time.monotonic() + timeout
        while True:
            rem = deadline - time.monotonic()
            if rem <= 0:
                raise TimeoutError(f"CDP {method} 超时")
            msg = json.loads(self.recv_text(timeout=rem))
            if msg.get("id") == req_id:
                if "error" in msg:
                    raise RuntimeError(f"CDP {method} 错误: {msg['error']}")
                res = msg.get("result") or {}
                if "exceptionDetails" in res:
                    raise RuntimeError(f"JS 异常: {res['exceptionDetails']}")
                return res

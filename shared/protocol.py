import json
import socket
from typing import Any, Dict


class ProtocolError(RuntimeError):
    pass


def send_json(sock: socket.socket, message: Dict[str, Any]) -> None:
    payload = json.dumps(message, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    sock.sendall(payload + b"\n")


def recv_json(sock: socket.socket) -> Dict[str, Any]:
    chunks = []
    while True:
        chunk = sock.recv(4096)
        if not chunk:
            raise ProtocolError("connection closed before newline-delimited JSON message")
        newline = chunk.find(b"\n")
        if newline >= 0:
            chunks.append(chunk[:newline])
            break
        chunks.append(chunk)
        if sum(len(part) for part in chunks) > 1024 * 1024:
            raise ProtocolError("message exceeded 1 MiB limit")
    raw = b"".join(chunks).decode("utf-8")
    try:
        message = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ProtocolError(f"invalid JSON message: {exc}") from exc
    if not isinstance(message, dict):
        raise ProtocolError("JSON message must be an object")
    return message


class JsonlRpcClient:
    def __init__(self, host: str, port: int, timeout_sec: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec

    def request(self, method: str, params: Dict[str, Any] | None = None) -> Dict[str, Any]:
        message = {"method": method, "params": params or {}}
        with socket.create_connection((self.host, self.port), timeout=self.timeout_sec) as sock:
            sock.settimeout(self.timeout_sec)
            send_json(sock, message)
            response = recv_json(sock)
        if not response.get("ok"):
            error = response.get("error") or "unknown remote error"
            raise ProtocolError(str(error))
        result = response.get("result")
        if not isinstance(result, dict):
            raise ProtocolError("remote result must be an object")
        return result

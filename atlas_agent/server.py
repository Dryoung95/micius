import argparse
import json
import socketserver
from typing import Any, Dict

from atlas_agent.safety import SafetyError, SafetyGuard
from atlas_agent.tools import ToolRegistry, build_registry
from shared.protocol import ProtocolError, recv_json, send_json


class AtlasRequestHandler(socketserver.BaseRequestHandler):
    registry: ToolRegistry
    safety: SafetyGuard

    def handle(self) -> None:
        try:
            request = recv_json(self.request)
            response = self.dispatch(request)
        except Exception as exc:
            response = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        send_json(self.request, response)

    def dispatch(self, request: Dict[str, Any]) -> Dict[str, Any]:
        method = request.get("method")
        params = request.get("params") or {}
        if not isinstance(params, dict):
            raise ProtocolError("params must be an object")
        if method == "hello":
            return {
                "ok": True,
                "result": {
                    "name": "micius-device-node",
                    "protocol": "jsonl-rpc-v1",
                    "safe_mode": self.safety.safe_mode,
                },
            }
        if method == "list_tools":
            return {"ok": True, "result": {"tools": self.registry.list_schemas()}}
        if method == "call_tool":
            name = params.get("name")
            args = params.get("arguments") or {}
            if not isinstance(name, str):
                raise ProtocolError("tool name must be a string")
            if not isinstance(args, dict):
                raise ProtocolError("tool arguments must be an object")
            try:
                self.safety.check_tool_call(name, args)
                result = self.registry.call(name, args)
            except SafetyError:
                raise
            except Exception as exc:
                return {
                    "ok": True,
                    "result": {
                        "tool": name,
                        "status": "error",
                        "error": f"{type(exc).__name__}: {exc}",
                    },
                }
            return {"ok": True, "result": {"tool": name, "status": "ok", "data": result}}
        raise ProtocolError(f"unknown method: {method}")


class ThreadedTcpServer(socketserver.ThreadingMixIn, socketserver.TCPServer):
    allow_reuse_address = True
    daemon_threads = True


def main() -> None:
    parser = argparse.ArgumentParser(description="Micius remote tool node for embedded device prototypes.")
    parser.add_argument("--config", help="Optional JSON config file.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--device-id", default="atlas_200i")
    parser.add_argument("--manifest", help="Path to the persistent capability manifest JSON file.")
    args = parser.parse_args()

    if args.config:
        with open(args.config, "r", encoding="utf-8") as f:
            config = json.load(f)
        args.host = config.get("host", args.host)
        args.port = int(config.get("port", args.port))
        args.device_id = config.get("device_id", args.device_id)
        args.manifest = config.get("manifest", args.manifest)

    AtlasRequestHandler.registry = build_registry(args.device_id, args.manifest)
    AtlasRequestHandler.safety = SafetyGuard(safe_mode=True)

    with ThreadedTcpServer((args.host, args.port), AtlasRequestHandler) as server:
        address = server.server_address
        print(f"micius-device-node listening on {address[0]}:{address[1]}")
        server.serve_forever()


if __name__ == "__main__":
    main()

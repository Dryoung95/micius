from typing import Any, Dict, List

from shared.protocol import JsonlRpcClient


class RemoteDeviceClient:
    def __init__(self, host: str, port: int, timeout_sec: float = 10.0) -> None:
        self.host = host
        self.port = port
        self.timeout_sec = timeout_sec
        self.rpc = JsonlRpcClient(host, port, timeout_sec=timeout_sec)

    def hello(self) -> Dict[str, Any]:
        return self.rpc.request("hello")

    def list_tools(self) -> List[Dict[str, Any]]:
        probe = JsonlRpcClient(self.host, self.port, timeout_sec=min(self.timeout_sec, 3.0))
        result = probe.request("list_tools")
        tools = result.get("tools")
        if not isinstance(tools, list):
            raise RuntimeError("remote device node returned invalid tool list")
        return tools

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        result = self.rpc.request("call_tool", {"name": name, "arguments": arguments})
        return result


class RemoteAtlasClient(RemoteDeviceClient):
    """Backward-compatible name for existing code and configs."""

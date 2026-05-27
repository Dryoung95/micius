import shutil
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Dict

from shared.protocol import JsonlRpcClient


PROJECT_ROOT = Path(__file__).resolve().parents[1]


def get_device_node_config(config: Dict[str, Any]) -> Dict[str, Any]:
    """Return the effective generic device-node config.

    `atlas` is kept as a legacy config section because existing local installs use
    it. New code should treat the result as a generic embedded device node.
    """
    legacy = config.get("atlas") if isinstance(config.get("atlas"), dict) else {}
    generic = config.get("device_node") if isinstance(config.get("device_node"), dict) else {}
    node: Dict[str, Any] = {}
    node.update(legacy)
    node.update({key: value for key, value in generic.items() if value is not None})

    host = str(node.get("host") or "127.0.0.1")
    port = int(node.get("port") or 8765)
    timeout_sec = float(node.get("timeout_sec") or 10)
    device = config.get("device") if isinstance(config.get("device"), dict) else {}
    ssh = node.get("ssh") if isinstance(node.get("ssh"), dict) else {}

    return {
        "host": host,
        "port": port,
        "timeout_sec": timeout_sec,
        "transport": str(node.get("transport") or "jsonl_tcp"),
        "device_id": str(node.get("device_id") or device.get("id") or "embedded_node"),
        "label": str(node.get("label") or "configured_device_node"),
        "config_source": "device_node" if generic else "atlas_legacy",
        "ssh": {
            "enabled": bool(ssh.get("enabled", False)),
            "user": str(ssh.get("user") or ""),
            "port": int(ssh.get("port") or 22),
            "connect_timeout_sec": float(ssh.get("connect_timeout_sec") or min(timeout_sec, 5)),
        },
    }


def build_connection_report(
    config: Dict[str, Any],
    *,
    include_ssh: bool = False,
    ssh_user: str | None = None,
    remote_timeout_sec: float | None = None,
) -> Dict[str, Any]:
    node = get_device_node_config(config)
    timeout_sec = float(remote_timeout_sec or min(float(node["timeout_sec"]), 3.0))
    tcp = tcp_probe(str(node["host"]), int(node["port"]), timeout_sec=min(timeout_sec, 1.5))
    report: Dict[str, Any] = {
        "status": "offline",
        "node": _public_node(node, ssh_user=ssh_user),
        "checks": {
            "tcp": tcp,
        },
        "commands": build_device_node_commands(config, ssh_user=ssh_user),
        "next_actions": [],
    }

    if tcp.get("ok"):
        jsonl = jsonl_tool_probe(str(node["host"]), int(node["port"]), timeout_sec=timeout_sec)
        report["checks"]["jsonl_tools"] = jsonl
        report["status"] = "online" if jsonl.get("ok") else "tcp_open_jsonl_failed"
    else:
        report["status"] = "offline"

    ssh_cfg = node.get("ssh", {})
    should_probe_ssh = include_ssh or bool(ssh_cfg.get("enabled")) or bool(ssh_user)
    if should_probe_ssh:
        user = ssh_user or str(ssh_cfg.get("user") or "")
        report["checks"]["ssh_client"] = ssh_client_check()
        report["checks"]["ssh"] = ssh_probe(
            host=str(node["host"]),
            user=user,
            port=int(ssh_cfg.get("port") or 22),
            timeout_sec=float(ssh_cfg.get("connect_timeout_sec") or 5),
        )

    report["next_actions"] = _next_actions(report)
    return report


def tcp_probe(host: str, port: int, timeout_sec: float = 1.0) -> Dict[str, Any]:
    target = "127.0.0.1" if host == "0.0.0.0" else host
    start = time.perf_counter()
    try:
        with socket.create_connection((target, port), timeout=timeout_sec):
            pass
    except OSError as exc:
        return {
            "ok": False,
            "host": host,
            "port": port,
            "error": f"{type(exc).__name__}: {exc}",
        }
    return {
        "ok": True,
        "host": host,
        "port": port,
        "latency_ms": round((time.perf_counter() - start) * 1000, 1),
    }


def jsonl_tool_probe(host: str, port: int, timeout_sec: float = 3.0) -> Dict[str, Any]:
    client = JsonlRpcClient(host, port, timeout_sec=timeout_sec)
    try:
        hello = client.request("hello")
        tools_result = client.request("list_tools")
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        return {"ok": False, "hello": hello, "error": "remote result did not include a tools list"}
    names = [tool.get("function", {}).get("name") for tool in tools if isinstance(tool, dict)]
    return {
        "ok": True,
        "hello": hello,
        "tool_count": len(tools),
        "tool_names": [name for name in names if isinstance(name, str)][:80],
    }


def ssh_client_check() -> Dict[str, Any]:
    path = shutil.which("ssh")
    return {
        "ok": bool(path),
        "path": path,
        "note": None if path else "ssh client not found in PATH",
    }


def ssh_probe(host: str, user: str, port: int = 22, timeout_sec: float = 5.0) -> Dict[str, Any]:
    if not user:
        return {
            "ok": False,
            "skipped": True,
            "reason": "ssh user is not configured; pass /connect ssh <user> or set device_node.ssh.user",
        }
    if not shutil.which("ssh"):
        return {"ok": False, "skipped": True, "reason": "ssh client not found in PATH"}
    target = f"{user}@{host}"
    command = [
        "ssh",
        "-o",
        "BatchMode=yes",
        "-o",
        f"ConnectTimeout={max(1, int(timeout_sec))}",
        "-p",
        str(port),
        target,
        "uname -a",
    ]
    try:
        completed = subprocess.run(
            command,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout_sec + 3,
        )
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}", "target": target, "port": port}
    return {
        "ok": completed.returncode == 0,
        "target": target,
        "port": port,
        "returncode": completed.returncode,
        "stdout": completed.stdout[-1200:],
        "stderr": completed.stderr[-1200:],
        "note": "BatchMode avoids password prompts; use the interactive ssh command if password login is required.",
    }


def build_device_node_commands(config: Dict[str, Any], ssh_user: str | None = None) -> Dict[str, Any]:
    node = get_device_node_config(config)
    host = str(node["host"])
    port = int(node["port"])
    device_id = str(node["device_id"])
    ssh_cfg = node.get("ssh", {})
    user = ssh_user or str(ssh_cfg.get("user") or "<user>")
    ssh_port = int(ssh_cfg.get("port") or 22)
    project_name = PROJECT_ROOT.name
    local_manifest = PROJECT_ROOT / "data" / "atlas_manifest.json"
    remote_manifest = "data/atlas_manifest.json"

    return {
        "local_tool_server": _join_command(
            [
                sys.executable,
                "-m",
                "atlas_agent.server",
                "--host",
                "127.0.0.1",
                "--port",
                str(port),
                "--device-id",
                device_id,
                "--manifest",
                str(local_manifest),
            ]
        ),
        "linux_device_tool_server": (
            f"cd ~/{project_name} && "
            f"python3 -m atlas_agent.server --host 0.0.0.0 --port {port} "
            f"--device-id {device_id} --manifest {remote_manifest}"
        ),
        "ssh_interactive": f"ssh -p {ssh_port} {user}@{host}",
        "prepare_remote_project_dir": f"ssh -p {ssh_port} {user}@{host} \"mkdir -p ~/{project_name}\"",
        "copy_project_to_linux_device": (
            f"Set-Location -LiteralPath {_quote_arg(str(PROJECT_ROOT))}; "
            f"scp -P {ssh_port} -r . {user}@{host}:~/{project_name}/"
        ),
        "configure_pc_node": {
            "device_node.host": host,
            "device_node.port": port,
            "device_node.transport": "jsonl_tcp",
        },
        "mcu_note": "ESP32-class nodes usually connect through a serial/MQTT bridge; they are not expected to run the full Python tool server.",
    }


def is_local_host(host: str) -> bool:
    return host in {"127.0.0.1", "localhost", "::1", "0.0.0.0"}


def _public_node(node: Dict[str, Any], ssh_user: str | None = None) -> Dict[str, Any]:
    ssh = node.get("ssh") if isinstance(node.get("ssh"), dict) else {}
    return {
        "host": node.get("host"),
        "port": node.get("port"),
        "timeout_sec": node.get("timeout_sec"),
        "transport": node.get("transport"),
        "device_id": node.get("device_id"),
        "label": node.get("label"),
        "config_source": node.get("config_source"),
        "ssh": {
            "enabled": bool(ssh.get("enabled", False)),
            "user": ssh_user or ssh.get("user") or "",
            "port": ssh.get("port", 22),
        },
    }


def _next_actions(report: Dict[str, Any]) -> list[str]:
    checks = report.get("checks", {})
    if report.get("status") == "online":
        return ["Run /refresh to reload tool schemas and device context after hardware changes."]
    if not checks.get("tcp", {}).get("ok"):
        return [
            "Confirm the device IP and the PC/device are on the same network.",
            "Start the remote embedded tool server on the device node.",
            "Run /connect commands to print the exact local, SSH, and server commands.",
        ]
    if not checks.get("jsonl_tools", {}).get("ok"):
        return [
            "A TCP service is listening, but it is not responding as Micius JSONL tools.",
            "Restart the remote embedded tool server and run /connect refresh.",
        ]
    return ["Run /connect doctor for deeper diagnostics."]


def _join_command(parts: list[str]) -> str:
    return " ".join(_quote_arg(part) for part in parts)


def _quote_arg(part: str) -> str:
    if not part:
        return '""'
    if any(ch.isspace() for ch in part):
        return '"' + part.replace('"', '\\"') + '"'
    return part

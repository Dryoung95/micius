import importlib
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any, Dict, List
from urllib.parse import quote_plus
from urllib.request import Request, urlopen

from local_agent.device_connect import build_connection_report
from local_agent.device_research import DeviceResearchLog


SELF_TOOL_NAMES = {
    "micius_self_status",
    "micius_connection_check",
    "micius_usb_scan",
    "micius_serial_monitor",
    "micius_dependency_install",
    "micius_platformio",
    "micius_web_search",
    "micius_diagnostic_report",
    "micius_device_research",
    "micius_esp32_flash",
    "micius_set_model",
    "micius_config_read",
    "micius_config_update",
    "micius_file_list",
    "micius_file_read",
    "micius_file_write",
    "micius_file_replace",
    "micius_run_check",
}


def self_tool_schemas() -> List[Dict[str, Any]]:
    return [
        {
            "type": "function",
            "function": {
                "name": "micius_self_status",
                "description": "Inspect Micius self-management capability, allowed edit roots, current model, and config path.",
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_connection_check",
                "description": "Diagnose the configured embedded device node connection and return bring-up commands.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_ssh": {
                            "type": "boolean",
                            "description": "Also run a non-interactive SSH diagnostic if possible. Default false.",
                        },
                        "ssh_user": {
                            "type": "string",
                            "description": "Optional SSH username override for the diagnostic and command hints.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_usb_scan",
                "description": (
                    "Scan USB devices and serial ports visible to the local machine running Micius. "
                    "Use this for local USB camera, serial adapter, ESP32, and sensor bring-up checks; "
                    "it does not require a remote embedded device node."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_all": {
                            "type": "boolean",
                            "description": "Return broader USB controller details when available. Default false.",
                        }
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_serial_monitor",
                "description": (
                    "Read serial output from a local embedded board for a bounded duration. "
                    "Use after flashing firmware or when diagnosing sensors and boot logs."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string", "description": "Serial port such as COM6 or /dev/ttyUSB0."},
                        "baud": {"type": "integer", "description": "Baud rate. Default 115200."},
                        "duration_sec": {
                            "type": "number",
                            "description": "Read duration. Default 5 seconds, max 60.",
                        },
                        "max_bytes": {
                            "type": "integer",
                            "description": "Maximum bytes of decoded output to keep. Default 12000, max 50000.",
                        },
                        "install_if_missing": {
                            "type": "boolean",
                            "description": "Install pyserial automatically if missing. Default true.",
                        },
                    },
                    "required": ["port"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_dependency_install",
                "description": (
                    "Check or install allowlisted local Python dependencies required by Micius tools. "
                    "Use this instead of asking the user to run pip when a supported dependency such as esptool is missing."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "dependency": {
                            "type": "string",
                            "description": "Dependency key. Currently supported: esptool, pyserial, platformio.",
                        },
                        "operation": {
                            "type": "string",
                            "description": "check or install. Default check.",
                        },
                        "timeout_sec": {
                            "type": "number",
                            "description": "Install timeout. Default 180 seconds, max 600.",
                        },
                    },
                    "required": ["dependency"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_platformio",
                "description": (
                    "Run allowlisted PlatformIO operations on an embedded project inside Micius allowed project areas. "
                    "Can check PlatformIO, build firmware, upload firmware, clean builds, or list serial devices."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "description": "One of: check, build, upload, clean, devices.",
                        },
                        "project_dir": {
                            "type": "string",
                            "description": "Relative project directory containing platformio.ini. Required for build/upload/clean.",
                        },
                        "environment": {
                            "type": "string",
                            "description": "Optional PlatformIO environment name, e.g. esp32dev.",
                        },
                        "port": {
                            "type": "string",
                            "description": "Optional serial upload port, e.g. COM6 or /dev/ttyUSB0.",
                        },
                        "install_if_missing": {
                            "type": "boolean",
                            "description": "Install PlatformIO automatically if missing. Default true.",
                        },
                        "timeout_sec": {
                            "type": "number",
                            "description": "Command timeout. Default 600 seconds, max 1800.",
                        },
                    },
                    "required": ["operation"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_web_search",
                "description": (
                    "Search the public web for current documentation, hardware references, release notes, "
                    "or recent information. Returns titles, snippets, and URLs; it does not fetch full page content."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "query": {"type": "string", "description": "Search query."},
                        "max_results": {
                            "type": "integer",
                            "description": "Maximum results to return. Default 5, max 10.",
                        },
                        "timeout_sec": {
                            "type": "number",
                            "description": "Network timeout. Default 15 seconds, max 30.",
                        },
                    },
                    "required": ["query"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_esp32_flash",
                "description": "Safely invoke esptool.py for ESP32 flash diagnostics or firmware flashing on a local serial port.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "port": {"type": "string", "description": "Serial port, e.g. COM5 or /dev/ttyUSB0."},
                        "baud": {"type": "integer", "description": "Baud rate. Default 460800 for write_flash, 115200 for flash_id."},
                        "operation": {"type": "string", "description": "flash_id or write_flash."},
                        "firmware_path": {"type": "string", "description": "Firmware .bin path for write_flash. Must be under allowed project roots."},
                        "address": {"type": "string", "description": "Flash address for firmware_path, default 0x1000."},
                        "chip": {"type": "string", "description": "Chip name passed to esptool, default auto."},
                        "timeout_sec": {"type": "number", "description": "Command timeout, max 120 seconds."},
                    },
                    "required": ["port", "operation"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_diagnostic_report",
                "description": (
                    "Generate a redacted local diagnostic report for support or open-source issue reports. "
                    "Includes config summary, tools, USB/serial state, dependencies, recent events, and optional report file path."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "contact_email": {
                            "type": "string",
                            "description": "Optional feedback email to include in the report.",
                        },
                        "include_usb": {"type": "boolean", "description": "Include USB scan. Default true."},
                        "include_recent_events": {
                            "type": "boolean",
                            "description": "Include recent redacted event history. Default true.",
                        },
                        "write_file": {
                            "type": "boolean",
                            "description": "Write report to data/reports. Default true.",
                        },
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_device_research",
                "description": (
                    "Create and maintain structured embedded device research tasks. "
                    "Use this to structure hardware bring-up into task construction, design, coding, "
                    "verification, profiling, and skill-curation stages; record tool evidence; "
                    "and distill reusable workflow skills."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "operation": {
                            "type": "string",
                            "description": "One of: create, list, show, record, finish, skill.",
                        },
                        "task_id": {"type": "string", "description": "DeviceResearch task id for show/record/finish/skill."},
                        "description": {"type": "string", "description": "Task description for create."},
                        "target": {"type": "string", "description": "Optional target behavior or device goal."},
                        "board": {"type": "string", "description": "Optional board or device family."},
                        "port": {"type": "string", "description": "Optional observed serial/network/device port."},
                        "project_dir": {"type": "string", "description": "Optional firmware or script project directory."},
                        "kind": {"type": "string", "description": "Record kind, e.g. usb.scan or serial.monitor."},
                        "stage": {"type": "string", "description": "Research stage for record. Default hardware_verifier."},
                        "summary": {"type": "string", "description": "Human-readable evidence summary for record."},
                        "payload": {"type": "object", "description": "Structured evidence payload for record."},
                        "status": {"type": "string", "description": "Evidence status, e.g. ok, failed, observed."},
                        "skill_name": {"type": "string", "description": "Workflow skill name for skill operation."},
                        "limit": {"type": "integer", "description": "Max tasks for list. Default 20."},
                    },
                    "required": ["operation"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_set_model",
                "description": "Switch the model used by Micius. Use this when the user asks to change models.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "model": {"type": "string", "description": "New model name."},
                        "persist": {"type": "boolean", "description": "Write the model into config for future sessions. Default true."},
                    },
                    "required": ["model"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_config_read",
                "description": "Read the loaded local Micius config. Secrets are redacted by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": "Top-level config section such as llm, device_node, atlas, agent, boards, memory, self_management, or all.",
                        },
                        "redact_secrets": {"type": "boolean", "description": "Redact API keys and secret-like values. Default true."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_config_update",
                "description": "Merge structured values into the loaded Micius config and optionally persist them.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "section": {
                            "type": "string",
                            "description": "Top-level section to update, or all for a root-level merge.",
                        },
                        "values": {"type": "object", "description": "JSON object to merge into the selected config section."},
                        "persist": {"type": "boolean", "description": "Write config to disk. Default true."},
                    },
                    "required": ["section", "values"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_file_list",
                "description": "List files under an allowed Micius project area.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative directory path to list. Default project root."},
                        "pattern": {"type": "string", "description": "Glob pattern such as *.py or **/*.md. Default *."},
                        "max_results": {"type": "integer", "description": "Maximum files to return. Default 80, max 200."},
                    },
                    "required": [],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_file_read",
                "description": "Read a text file from an allowed Micius project area.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to a text file."},
                        "max_chars": {"type": "integer", "description": "Maximum characters to return. Default 8000, max 20000."},
                        "redact_secrets": {"type": "boolean", "description": "Redact API keys and secret-like values. Default true."},
                    },
                    "required": ["path"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_file_write",
                "description": "Write a text file in an allowed Micius project area. Existing files are backed up by default.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to write."},
                        "content": {"type": "string", "description": "Full new file content."},
                        "overwrite": {"type": "boolean", "description": "Allow replacing an existing file. Default false."},
                        "create_backup": {"type": "boolean", "description": "Create a backup before replacing. Default true."},
                    },
                    "required": ["path", "content"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_file_replace",
                "description": "Replace exact text inside an allowed Micius project file. Prefer this for source self-edits.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "path": {"type": "string", "description": "Relative path to edit."},
                        "old_text": {"type": "string", "description": "Exact text to replace."},
                        "new_text": {"type": "string", "description": "Replacement text."},
                        "expected_replacements": {
                            "type": "integer",
                            "description": "Expected replacement count. Default 1. Set 0 to allow any positive count.",
                        },
                        "create_backup": {"type": "boolean", "description": "Create a backup before editing. Default true."},
                    },
                    "required": ["path", "old_text", "new_text"],
                },
            },
        },
        {
            "type": "function",
            "function": {
                "name": "micius_run_check",
                "description": "Run a predefined local self-check after Micius edits itself.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "check": {
                            "type": "string",
                            "description": "One of: py_compile, cli_smoke.",
                        }
                    },
                    "required": ["check"],
                },
            },
        },
    ]


class LocalSelfTools:
    def __init__(self, owner: Any, config_path: str | None = None) -> None:
        self.owner = owner
        self.project_root = Path(__file__).resolve().parents[1]
        self.config_path = Path(config_path).resolve() if config_path else None
        self.backup_dir = self.project_root / "data" / "self_backups"

    def schemas(self) -> List[Dict[str, Any]]:
        if os.getenv("MICIUS_DISABLE_SELF_TOOLS"):
            return []
        if not self.owner.config.get("self_management", {}).get("enabled", True):
            return []
        return self_tool_schemas()

    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name not in SELF_TOOL_NAMES:
            raise KeyError(f"unknown self tool: {name}")
        if os.getenv("MICIUS_DISABLE_SELF_TOOLS"):
            raise PermissionError("Micius self-management tools are disabled by MICIUS_DISABLE_SELF_TOOLS")
        if name == "micius_self_status":
            return self._self_status()
        if name == "micius_connection_check":
            return self._connection_check(args)
        if name == "micius_usb_scan":
            return self._usb_scan(args)
        if name == "micius_serial_monitor":
            return self._serial_monitor(args)
        if name == "micius_dependency_install":
            return self._dependency_install(args)
        if name == "micius_platformio":
            return self._platformio(args)
        if name == "micius_web_search":
            return self._web_search(args)
        if name == "micius_esp32_flash":
            return self._esp32_flash(args)
        if name == "micius_diagnostic_report":
            return self._diagnostic_report(args)
        if name == "micius_device_research":
            return self._device_research(args)
        if name == "micius_set_model":
            return self._set_model(args)
        if name == "micius_config_read":
            return self._config_read(args)
        if name == "micius_config_update":
            return self._config_update(args)
        if name == "micius_file_list":
            return self._file_list(args)
        if name == "micius_file_read":
            return self._file_read(args)
        if name == "micius_file_write":
            return self._file_write(args)
        if name == "micius_file_replace":
            return self._file_replace(args)
        if name == "micius_run_check":
            return self._run_check(args)
        raise KeyError(f"unknown self tool: {name}")

    def _self_status(self) -> Dict[str, Any]:
        return {
            "status": "ok",
            "model": self.owner.model,
            "config_path": str(self.config_path) if self.config_path else None,
            "project_root": str(self.project_root),
            "allowed_edit_roots": [str(path) for path in self._allowed_roots()],
            "allowed_single_files": [str(path) for path in self._allowed_files()],
            "source_edits_allowed": self._source_edits_allowed(),
            "restart_required_after_source_edit": True,
        }

    def _connection_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        return build_connection_report(
            self.owner.config,
            include_ssh=bool(args.get("include_ssh", False)),
            ssh_user=str(args.get("ssh_user") or "").strip() or None,
        )

    def _usb_scan(self, args: Dict[str, Any]) -> Dict[str, Any]:
        include_all = bool(args.get("include_all", False))
        if os.name == "nt":
            return self._usb_scan_windows(include_all=include_all)
        return self._usb_scan_posix(include_all=include_all)

    def _usb_scan_windows(self, include_all: bool = False) -> Dict[str, Any]:
        powershell = shutil.which("powershell.exe") or shutil.which("powershell")
        if not powershell:
            raise RuntimeError("powershell is required for USB scanning on Windows")
        class_filter = "@('USB','USBDevice','Ports','Camera','Image')" if not include_all else "@()"
        where_clause = (
            "$_.InstanceId -like 'USB*' -or $_.Class -in "
            + class_filter
            if not include_all
            else "$_.InstanceId -like 'USB*' -or $_.PNPClass -or $_.Class"
        )
        script = f"""
$ErrorActionPreference = 'SilentlyContinue'
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$OutputEncoding = [System.Text.Encoding]::UTF8
$usb = Get-PnpDevice -PresentOnly | Where-Object {{ {where_clause} }} | Select-Object Class,FriendlyName,InstanceId,Status
$serial = Get-CimInstance Win32_SerialPort | Select-Object DeviceID,Name,PNPDeviceID,Description,ProviderType
$controllers = Get-CimInstance Win32_USBController | Select-Object Name,DeviceID,Status
[pscustomobject]@{{
  usb_devices = @($usb)
  serial_ports = @($serial)
  usb_controllers = @($controllers)
}} | ConvertTo-Json -Depth 5 -Compress
"""
        completed = subprocess.run(
            [powershell, "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", script],
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        if completed.stdout.strip():
            data = json.loads(completed.stdout)
        else:
            data = {}
        return {
            "status": "ok" if completed.returncode == 0 else "partial",
            "host": sys.platform,
            "method": "powershell:Get-PnpDevice/Get-CimInstance",
            "note": "This reports USB devices visible to the local OS, not empty physical ports.",
            "data": {
                "usb_devices": _as_list(data.get("usb_devices")),
                "serial_ports": _as_list(data.get("serial_ports")),
                "usb_controllers": _as_list(data.get("usb_controllers")),
            },
            "stderr": completed.stderr.strip()[-2000:],
        }

    def _usb_scan_posix(self, include_all: bool = False) -> Dict[str, Any]:
        lsusb_rows: List[str] = []
        if shutil.which("lsusb"):
            completed = subprocess.run(
                ["lsusb"],
                cwd=self.project_root,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=10,
            )
            if completed.stdout:
                lsusb_rows = [line for line in completed.stdout.splitlines() if line.strip()]
        serial_patterns = ["/dev/ttyUSB*", "/dev/ttyACM*", "/dev/cu.*"]
        if include_all:
            serial_patterns.append("/dev/ttyS*")
        serial_ports = sorted({str(path) for pattern in serial_patterns for path in Path("/").glob(pattern.lstrip("/"))})
        video_devices = sorted({str(path) for path in Path("/").glob("dev/video*")})
        return {
            "status": "ok",
            "host": sys.platform,
            "method": "lsusb/devfs",
            "note": "This reports USB devices visible to the local OS, not empty physical ports.",
            "data": {
                "lsusb": lsusb_rows,
                "serial_ports": serial_ports,
                "video_devices": video_devices,
            },
        }

    def _serial_monitor(self, args: Dict[str, Any]) -> Dict[str, Any]:
        port = _short_text(args.get("port"), 80).strip()
        if not port:
            raise ValueError("port is required")
        if not _is_safe_serial_port(port):
            raise ValueError("unsupported serial port format")
        if importlib.util.find_spec("serial") is None:
            if not bool(args.get("install_if_missing", True)):
                raise RuntimeError("pyserial is missing")
            install = self._dependency_install({"dependency": "pyserial", "operation": "install", "timeout_sec": 180})
            if install.get("status") != "installed":
                return {"status": "failed", "reason": "pyserial installation failed", "install": install}
            importlib.invalidate_caches()
        serial_mod = importlib.import_module("serial")
        baud = int(args.get("baud") or 115200)
        if baud not in {9600, 19200, 38400, 57600, 74880, 115200, 230400, 460800, 921600, 1000000, 1500000, 2000000}:
            raise ValueError("unsupported baud rate")
        duration = max(0.5, min(float(args.get("duration_sec") or 5), 60.0))
        max_bytes = max(256, min(int(args.get("max_bytes") or 12000), 50000))
        started = time.time()
        chunks: List[bytes] = []
        byte_count = 0
        try:
            with serial_mod.Serial(port=port, baudrate=baud, timeout=0.2) as serial_port:
                serial_port.reset_input_buffer()
                while time.time() - started < duration and byte_count < max_bytes:
                    data = serial_port.read(min(1024, max_bytes - byte_count))
                    if data:
                        chunks.append(data)
                        byte_count += len(data)
        except Exception as exc:
            return {
                "status": "error",
                "port": port,
                "baud": baud,
                "duration_sec": duration,
                "error_type": type(exc).__name__,
                "error": str(exc),
            }
        raw = b"".join(chunks)
        text = raw.decode("utf-8", errors="replace")
        lines = [line for line in text.replace("\r\n", "\n").replace("\r", "\n").split("\n") if line.strip()]
        return {
            "status": "ok",
            "port": port,
            "baud": baud,
            "duration_sec": round(time.time() - started, 3),
            "bytes_read": len(raw),
            "truncated": byte_count >= max_bytes,
            "line_count": len(lines),
            "lines_tail": lines[-80:],
            "text_tail": text[-max_bytes:],
        }

    def _dependency_install(self, args: Dict[str, Any]) -> Dict[str, Any]:
        dependency = _short_text(args.get("dependency"), 80).strip().lower()
        operation = _short_text(args.get("operation") or "check", 20).strip().lower()
        allowed = {
            "esptool": {"packages": ["esptool"], "modules": ["esptool"]},
            "platformio": {"packages": ["platformio"], "modules": ["platformio"]},
            "pyserial": {"packages": ["pyserial"], "modules": ["serial"]},
        }
        if dependency not in allowed:
            raise ValueError("dependency must be one of: " + ", ".join(sorted(allowed)))
        if operation not in {"check", "install"}:
            raise ValueError("operation must be one of: check, install")
        spec = allowed[dependency]
        modules = list(spec["modules"])
        before = {module: importlib.util.find_spec(module) is not None for module in modules}
        if operation == "check" or all(before.values()):
            return {
                "status": "installed" if all(before.values()) else "missing",
                "dependency": dependency,
                "operation": operation,
                "modules": before,
                "python": sys.executable,
            }
        timeout = max(30.0, min(float(args.get("timeout_sec") or 180), 600.0))
        command = [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            *spec["packages"],
        ]
        started = time.time()
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        importlib.invalidate_caches()
        after = {module: importlib.util.find_spec(module) is not None for module in modules}
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        return {
            "status": "installed" if completed.returncode == 0 and all(after.values()) else "failed",
            "dependency": dependency,
            "operation": operation,
            "python": sys.executable,
            "returncode": completed.returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "modules_before": before,
            "modules_after": after,
            "output_tail": output[-6000:],
        }

    def _platformio(self, args: Dict[str, Any]) -> Dict[str, Any]:
        operation = _short_text(args.get("operation"), 40).strip().lower()
        if operation not in {"check", "build", "upload", "clean", "devices"}:
            raise ValueError("operation must be one of: check, build, upload, clean, devices")
        install_result = None
        if importlib.util.find_spec("platformio") is None:
            if not bool(args.get("install_if_missing", True)):
                raise RuntimeError("platformio is missing")
            install_result = self._dependency_install(
                {
                    "dependency": "platformio",
                    "operation": "install",
                    "timeout_sec": max(180, int(args.get("timeout_sec") or 600)),
                }
            )
            if install_result.get("status") != "installed":
                return {
                    "status": "failed",
                    "operation": operation,
                    "reason": "platformio installation failed",
                    "install": install_result,
                }
        timeout = max(30.0, min(float(args.get("timeout_sec") or 600), 1800.0))
        cwd = self.project_root
        project_dir = None
        cmd = [sys.executable, "-m", "platformio"]
        if operation == "check":
            cmd += ["--version"]
        elif operation == "devices":
            cmd += ["device", "list"]
        else:
            project_dir = self._resolve_platformio_project(str(args.get("project_dir") or ""))
            cwd = project_dir
            cmd += ["run"]
            environment = _short_text(args.get("environment") or "", 80).strip()
            if environment:
                if not re.fullmatch(r"[A-Za-z0-9_.-]+", environment):
                    raise ValueError("invalid PlatformIO environment name")
                cmd += ["-e", environment]
            port = _short_text(args.get("port") or "", 80).strip()
            if port:
                if not _is_safe_serial_port(port):
                    raise ValueError("unsupported serial port format")
                cmd += ["--upload-port", port]
            if operation == "upload":
                cmd += ["-t", "upload"]
            elif operation == "clean":
                cmd += ["-t", "clean"]
        started = time.time()
        try:
            completed = subprocess.run(
                cmd,
                cwd=cwd,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout,
            )
            returncode = completed.returncode
            output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
            status = "ok" if completed.returncode == 0 else "failed"
        except subprocess.TimeoutExpired as exc:
            returncode = None
            stdout = exc.stdout.decode("utf-8", errors="replace") if isinstance(exc.stdout, bytes) else (exc.stdout or "")
            stderr = exc.stderr.decode("utf-8", errors="replace") if isinstance(exc.stderr, bytes) else (exc.stderr or "")
            output = (stdout + "\n" + stderr).strip()
            status = "timeout"
        firmware = self._platformio_firmware_files(project_dir) if project_dir else []
        return {
            "status": status,
            "operation": operation,
            "project_dir": str(project_dir.relative_to(self.project_root)).replace("\\", "/") if project_dir else None,
            "command": cmd,
            "returncode": returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "firmware": firmware,
            "install": install_result,
            "output_tail": output[-10000:],
        }

    def _resolve_platformio_project(self, raw_path: str) -> Path:
        if not raw_path.strip():
            raise ValueError("project_dir is required")
        project_dir = self._resolve_path(raw_path)
        if not project_dir.exists() or not project_dir.is_dir():
            raise FileNotFoundError(str(project_dir))
        if not (project_dir / "platformio.ini").is_file():
            raise FileNotFoundError(str(project_dir / "platformio.ini"))
        return project_dir

    def _platformio_firmware_files(self, project_dir: Path) -> List[str]:
        build_dir = project_dir / ".pio" / "build"
        if not build_dir.exists():
            return []
        files = []
        for path in sorted(build_dir.glob("*/firmware.bin")):
            try:
                rel = path.relative_to(self.project_root)
            except ValueError:
                continue
            files.append(str(rel).replace("\\", "/"))
        return files

    def _web_search(self, args: Dict[str, Any]) -> Dict[str, Any]:
        query = _short_text(args.get("query"), 300).strip()
        if not query:
            raise ValueError("query is required")
        max_results = max(1, min(int(args.get("max_results") or 5), 10))
        timeout = max(3.0, min(float(args.get("timeout_sec") or 15), 30.0))
        url = "https://www.bing.com/search?format=rss&q=" + quote_plus(query)
        request = Request(
            url,
            headers={
                "User-Agent": "Mozilla/5.0 Micius-Agent/0.1",
                "Accept": "application/rss+xml, application/xml, text/xml;q=0.9, */*;q=0.8",
            },
        )
        started = time.time()
        with urlopen(request, timeout=timeout) as response:
            raw = response.read(400000)
        text = raw.decode("utf-8", errors="replace")
        root = ET.fromstring(text)
        results = []
        seen_urls = set()
        for item in root.findall("./channel/item"):
            if len(results) >= max_results:
                break
            link = (item.findtext("link") or "").strip()
            if not link or link in seen_urls:
                continue
            seen_urls.add(link)
            results.append(
                {
                    "title": _clean_rss_text(item.findtext("title") or ""),
                    "url": link,
                    "snippet": _clean_rss_text(item.findtext("description") or ""),
                }
            )
        return {
            "status": "ok",
            "query": query,
            "engine": "bing_rss",
            "elapsed_sec": round(time.time() - started, 3),
            "result_count": len(results),
            "results": results,
            "note": "Search results may be incomplete or ranked by the search provider; cite URLs when using them.",
        }

    def _esp32_flash(self, args: Dict[str, Any]) -> Dict[str, Any]:
        port = _short_text(args.get("port"), 80).strip()
        operation = _short_text(args.get("operation"), 40).strip()
        chip = _short_text(args.get("chip") or "auto", 40).strip()
        if not port:
            raise ValueError("port is required")
        if operation not in {"flash_id", "write_flash"}:
            raise ValueError("operation must be one of: flash_id, write_flash")
        if not _is_safe_serial_port(port):
            raise ValueError("unsupported serial port format")
        if chip not in {"auto", "esp32", "esp32s2", "esp32s3", "esp32c2", "esp32c3", "esp32c6", "esp32h2"}:
            raise ValueError("unsupported chip")
        timeout = max(5.0, min(float(args.get("timeout_sec") or 60), 120.0))
        baud = int(args.get("baud") or (115200 if operation == "flash_id" else 460800))
        if baud not in {9600, 57600, 74880, 115200, 230400, 460800, 921600}:
            raise ValueError("unsupported baud rate")
        esptool = shutil.which("esptool.py") or shutil.which("esptool")
        command_prefix: List[str]
        if esptool:
            command_prefix = [esptool]
        else:
            command_prefix = [sys.executable, "-m", "esptool"]
        cmd = command_prefix + ["--chip", chip, "--port", port, "--baud", str(baud)]
        firmware_resolved = None
        if operation == "flash_id":
            cmd += ["flash-id"]
        else:
            firmware = str(args.get("firmware_path") or "").strip()
            if not firmware:
                raise ValueError("firmware_path is required for write_flash")
            firmware_path = Path(firmware)
            if not firmware_path.is_absolute():
                firmware_path = self.project_root / firmware_path
            firmware_resolved = firmware_path.resolve()
            if not self._is_allowed_path(firmware_resolved):
                raise PermissionError("firmware_path must be under an allowed project area")
            if not firmware_resolved.is_file():
                raise FileNotFoundError(str(firmware_resolved))
            if firmware_resolved.suffix.lower() != ".bin":
                raise ValueError("firmware_path must be a .bin file")
            address = _short_text(args.get("address") or "0x1000", 20).strip()
            if not re.fullmatch(r"0x[0-9A-Fa-f]+|[0-9]+", address):
                raise ValueError("invalid flash address")
            cmd += ["write_flash", "-z", address, str(firmware_resolved)]
        started = time.time()
        completed = subprocess.run(
            cmd,
            cwd=self.project_root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
        )
        output = ((completed.stdout or "") + "\n" + (completed.stderr or "")).strip()
        return {
            "status": "ok" if completed.returncode == 0 else "failed",
            "operation": operation,
            "port": port,
            "chip": chip,
            "baud": baud,
            "firmware_path": str(firmware_resolved) if firmware_resolved else None,
            "returncode": completed.returncode,
            "elapsed_sec": round(time.time() - started, 3),
            "output_tail": output[-6000:],
        }

    def _diagnostic_report(self, args: Dict[str, Any]) -> Dict[str, Any]:
        include_usb = bool(args.get("include_usb", True))
        include_recent_events = bool(args.get("include_recent_events", True))
        write_file = bool(args.get("write_file", True))
        contact_email = _short_text(args.get("contact_email") or os.getenv("MICIUS_FEEDBACK_EMAIL") or "", 200).strip()
        tool_names = sorted(
            tool["function"]["name"]
            for tool in self.owner.tools
            if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
        )
        report: Dict[str, Any] = {
            "generated_at": _format_timestamp(time.time()),
            "project_root": str(self.project_root),
            "python": sys.version.split()[0],
            "platform": sys.platform,
            "model": self.owner.model,
            "config_path": str(self.config_path) if self.config_path else None,
            "contact_email": contact_email or None,
            "remote_error": getattr(self.owner, "remote_error", None),
            "config": _redact(self.owner.config),
            "tool_count": len(tool_names),
            "tools": tool_names,
            "self_status": self._self_status(),
            "dependencies": {
                name: self._dependency_install({"dependency": name, "operation": "check"})
                for name in ("esptool", "pyserial", "platformio")
            },
        }
        if include_usb:
            try:
                report["usb"] = self._usb_scan({"include_all": False})
            except Exception as exc:
                report["usb"] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        try:
            report["platformio_devices"] = self._platformio({"operation": "devices", "install_if_missing": False, "timeout_sec": 30})
        except Exception as exc:
            report["platformio_devices"] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        report["firmware"] = self._known_firmware_files()
        if include_recent_events:
            try:
                events = self.owner.memory.recent_events(limit=20).get("events", [])
                report["recent_events"] = _redact(json.loads(_redact_text(json.dumps(events, ensure_ascii=False, default=str))))
            except Exception as exc:
                report["recent_events"] = {"status": "error", "error_type": type(exc).__name__, "error": str(exc)}
        text = _format_diagnostic_report(report)
        path = None
        if write_file:
            output_dir = self.project_root / "data" / "reports"
            output_dir.mkdir(parents=True, exist_ok=True)
            path = output_dir / f"micius_report_{int(time.time())}.md"
            path.write_text(text, encoding="utf-8")
        return {
            "status": "ok",
            "path": str(path) if path else None,
            "contact_email": contact_email or None,
            "summary": {
                "tool_count": len(tool_names),
                "remote_error": getattr(self.owner, "remote_error", None),
                "usb_status": report.get("usb", {}).get("status") if isinstance(report.get("usb"), dict) else None,
                "firmware_count": len(report["firmware"]),
            },
            "preview": text[:5000],
        }

    def _device_research(self, args: Dict[str, Any]) -> Dict[str, Any]:
        operation = _short_text(args.get("operation"), 40).strip().lower()
        log = DeviceResearchLog.from_config(self.owner.config)
        if operation == "create":
            return log.create_task(
                description=str(args.get("description") or ""),
                target=str(args.get("target") or ""),
                board=str(args.get("board") or ""),
                port=str(args.get("port") or ""),
                project_dir=str(args.get("project_dir") or ""),
            )
        if operation == "list":
            return log.list_tasks(limit=int(args.get("limit") or 20))
        task_id = _short_text(args.get("task_id"), 96).strip()
        if not task_id:
            raise ValueError("task_id is required")
        if operation == "show":
            return log.show_task(task_id, include_trace=True)
        if operation == "record":
            return log.record_event(
                task_id=task_id,
                kind=_short_text(args.get("kind") or "manual.observation", 80).strip(),
                summary=str(args.get("summary") or ""),
                payload=args.get("payload") if isinstance(args.get("payload"), dict) else {},
                stage=_short_text(args.get("stage") or "hardware_verifier", 80).strip(),
                status=_short_text(args.get("status") or "observed", 40).strip(),
            )
        if operation == "finish":
            return log.finish_task(task_id, status=_short_text(args.get("status") or "done", 40).strip())
        if operation == "skill":
            skill_name = _short_text(args.get("skill_name"), 80).strip()
            if not skill_name:
                raise ValueError("skill_name is required for skill operation")
            generated = log.build_skill_body(task_id)
            saved = self.owner.memory.add_skill(
                skill_name,
                generated["body"],
                title=generated.get("suggested_title") or skill_name,
                tags=["device-research", "akg-style"],
                triggers=["similar embedded bring-up", "repeated board or peripheral workflow"],
            )
            log.record_event(
                task_id=task_id,
                kind="skill.curated",
                summary=f"Saved workflow skill {skill_name}.",
                payload={"skill": saved},
                stage="skill_curator",
                status="ok",
            )
            return {"status": "ok", "task_id": task_id, "generated": generated, "skill": saved}
        raise ValueError("operation must be one of: create, list, show, record, finish, skill")

    def _known_firmware_files(self) -> List[Dict[str, Any]]:
        files = []
        for root in self._allowed_roots():
            if not root.exists():
                continue
            for path in sorted(root.glob("**/*.bin"))[:80]:
                if any(part in {"__pycache__", ".git", ".venv", "node_modules"} for part in path.parts):
                    continue
                try:
                    rel = path.relative_to(self.project_root)
                except ValueError:
                    continue
                files.append(
                    {
                        "path": str(rel).replace("\\", "/"),
                        "size_bytes": path.stat().st_size,
                        "modified_at": _format_timestamp(path.stat().st_mtime),
                    }
                )
                if len(files) >= 80:
                    return files
        return files

    def _set_model(self, args: Dict[str, Any]) -> Dict[str, Any]:
        model = _short_text(args.get("model"), 160).strip()
        if not model:
            raise ValueError("model is required")
        persist = bool(args.get("persist", True))
        previous = self.owner.model
        self.owner.set_model(model, reset=False)
        if persist:
            self._save_config()
        self.owner.memory.record_usage("model", model, {"action": "self_set", "persist": persist})
        return {
            "status": "model_switched",
            "previous_model": previous,
            "model": model,
            "persisted": persist and self.config_path is not None,
            "config_path": str(self.config_path) if persist and self.config_path else None,
        }

    def _config_read(self, args: Dict[str, Any]) -> Dict[str, Any]:
        section = str(args.get("section") or "all").strip() or "all"
        redact = bool(args.get("redact_secrets", True))
        config = self.owner.config
        if section != "all":
            if section not in config:
                raise KeyError(f"unknown config section: {section}")
            payload: Any = {section: config[section]}
        else:
            payload = config
        if redact:
            payload = _redact(payload)
        return {
            "status": "ok",
            "section": section,
            "config_path": str(self.config_path) if self.config_path else None,
            "config": payload,
        }

    def _config_update(self, args: Dict[str, Any]) -> Dict[str, Any]:
        section = str(args.get("section") or "").strip()
        values = args.get("values")
        if not section:
            raise ValueError("section is required")
        if not isinstance(values, dict):
            raise ValueError("values must be an object")
        persist = bool(args.get("persist", True))
        config = self.owner.config
        if section == "all":
            _deep_merge(config, values)
            changed_sections = sorted(values.keys())
        else:
            target = config.setdefault(section, {})
            if not isinstance(target, dict):
                raise ValueError(f"config section is not an object: {section}")
            _deep_merge(target, values)
            changed_sections = [section]
        self.owner.apply_runtime_config(changed_sections, reset=False)
        if persist:
            self._save_config()
        return {
            "status": "config_updated",
            "changed_sections": changed_sections,
            "persisted": persist and self.config_path is not None,
            "config_path": str(self.config_path) if persist and self.config_path else None,
            "restart_recommended": bool(set(changed_sections) & {"agent", "atlas", "device_node", "self_management"}),
        }

    def _file_list(self, args: Dict[str, Any]) -> Dict[str, Any]:
        raw_path = str(args.get("path") or ".")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        base = candidate.resolve()
        if base == self.project_root:
            return self._file_list_project_root(args)
        base = self._resolve_path(raw_path)
        if not base.exists():
            raise FileNotFoundError(str(base))
        if not base.is_dir():
            raise ValueError("path must be a directory")
        pattern = str(args.get("pattern") or "*")
        max_results = max(1, min(int(args.get("max_results") or 80), 200))
        files = []
        for path in sorted(base.glob(pattern)):
            if len(files) >= max_results:
                break
            if path.is_file() and self._is_allowed_path(path):
                files.append(
                    {
                        "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
                        "size_bytes": path.stat().st_size,
                    }
                )
        return {"status": "ok", "base": str(base), "pattern": pattern, "files": files}

    def _file_list_project_root(self, args: Dict[str, Any]) -> Dict[str, Any]:
        max_results = max(1, min(int(args.get("max_results") or 80), 200))
        entries = []
        allowed_paths = self._allowed_roots() + self._allowed_files()
        for path in sorted(allowed_paths, key=lambda item: item.as_posix().lower()):
            if len(entries) >= max_results:
                break
            if not path.exists():
                continue
            entries.append(
                {
                    "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
                    "kind": "dir" if path.is_dir() else "file",
                    "size_bytes": path.stat().st_size if path.is_file() else None,
                }
            )
        return {
            "status": "ok",
            "base": str(self.project_root),
            "pattern": "allowed-roots",
            "files": entries,
            "note": "Project root listing is limited to self-management allowlist entries.",
        }

    def _file_read(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve_path(str(args.get("path") or ""))
        if not path.exists():
            raise FileNotFoundError(str(path))
        if not path.is_file():
            raise ValueError("path must be a file")
        max_chars = max(1, min(int(args.get("max_chars") or 8000), 20000))
        text = path.read_text(encoding="utf-8-sig")
        truncated = len(text) > max_chars
        text = text[:max_chars]
        if bool(args.get("redact_secrets", True)):
            text = _redact_text(text)
        return {
            "status": "ok",
            "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
            "content": text,
            "truncated": truncated,
        }

    def _file_write(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve_path(str(args.get("path") or ""))
        content = str(args.get("content") or "")
        if len(content) > 120000:
            raise ValueError("content exceeds 120000 characters")
        overwrite = bool(args.get("overwrite", False))
        create_backup = bool(args.get("create_backup", True))
        if path.exists() and not overwrite:
            raise FileExistsError(f"file exists; set overwrite=true: {path}")
        backup = self._backup(path) if path.exists() and create_backup else None
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        return {
            "status": "written",
            "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
            "backup": str(backup) if backup else None,
            "bytes": path.stat().st_size,
            "restart_required": self._is_source_file(path),
        }

    def _file_replace(self, args: Dict[str, Any]) -> Dict[str, Any]:
        path = self._resolve_path(str(args.get("path") or ""))
        old_text = str(args.get("old_text") or "")
        new_text = str(args.get("new_text") or "")
        expected = int(args.get("expected_replacements", 1))
        create_backup = bool(args.get("create_backup", True))
        if not old_text:
            raise ValueError("old_text is required")
        if not path.exists() or not path.is_file():
            raise FileNotFoundError(str(path))
        text = path.read_text(encoding="utf-8-sig")
        count = text.count(old_text)
        if count == 0:
            raise ValueError("old_text was not found")
        if expected > 0 and count != expected:
            raise ValueError(f"replacement count mismatch: expected {expected}, found {count}")
        backup = self._backup(path) if create_backup else None
        path.write_text(text.replace(old_text, new_text), encoding="utf-8")
        return {
            "status": "replaced",
            "path": str(path.relative_to(self.project_root)).replace("\\", "/"),
            "replacements": count,
            "backup": str(backup) if backup else None,
            "restart_required": self._is_source_file(path),
        }

    def _run_check(self, args: Dict[str, Any]) -> Dict[str, Any]:
        check = str(args.get("check") or "").strip()
        if check == "py_compile":
            command = [
                sys.executable,
                "-m",
                "py_compile",
                "local_agent/agent.py",
                "local_agent/cli.py",
                "local_agent/self_tools.py",
                "local_agent/llm_client.py",
                "local_agent/remote_tools.py",
                "local_agent/device_connect.py",
                "local_agent/device_research.py",
                "local_agent/micius_memory.py",
                "local_agent/board_knowledge.py",
                "atlas_agent/server.py",
                "atlas_agent/tools.py",
            ]
        elif check == "cli_smoke":
            command = [sys.executable, "-m", "local_agent.cli", "--no-auto-device"]
        else:
            raise ValueError("check must be one of: py_compile, cli_smoke")
        completed = subprocess.run(
            command,
            cwd=self.project_root,
            input="/exit\n" if check == "cli_smoke" else None,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=20,
        )
        return {
            "status": "ok" if completed.returncode == 0 else "failed",
            "check": check,
            "returncode": completed.returncode,
            "stdout": completed.stdout[-4000:],
            "stderr": completed.stderr[-4000:],
        }

    def _save_config(self) -> None:
        if self.config_path is None:
            return
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.config_path.with_suffix(self.config_path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(self.owner.config, f, ensure_ascii=False, indent=2)
            f.write("\n")
        tmp.replace(self.config_path)

    def _resolve_path(self, raw_path: str) -> Path:
        if not raw_path.strip():
            raise ValueError("path is required")
        candidate = Path(raw_path)
        if not candidate.is_absolute():
            candidate = self.project_root / candidate
        path = candidate.resolve()
        if not self._is_allowed_path(path):
            raise PermissionError(f"path is outside Micius self-management allowlist: {path}")
        return path

    def _is_allowed_path(self, path: Path) -> bool:
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path
        if any(part in {".git", "__pycache__", ".venv", "node_modules"} for part in resolved.parts):
            return False
        if resolved in self._allowed_files():
            return True
        for root in self._allowed_roots():
            try:
                resolved.relative_to(root)
                if self._is_source_file(resolved) and not self._source_edits_allowed():
                    return False
                return True
            except ValueError:
                continue
        return False

    def _allowed_roots(self) -> List[Path]:
        roots = [
            self.project_root / "configs",
            self.project_root / "local_agent",
            self.project_root / "atlas_agent",
            self.project_root / "shared",
            self.project_root / "board_knowledge",
            self.project_root / "docs",
            self.project_root / "micius_memory" / "skills",
            self.project_root / "micius_memory" / "reflections",
            self.project_root / "brand",
            self.project_root / "site",
        ]
        return [path.resolve() for path in roots]

    def _allowed_files(self) -> List[Path]:
        files = [
            self.project_root / "README.md",
            self.project_root / "pyproject.toml",
            self.project_root / "micius",
            self.project_root / "micius.cmd",
            self.project_root / "micius_memory" / "MEMORY.md",
            self.project_root / "micius_memory" / "USER.md",
        ]
        return [path.resolve() for path in files]

    def _source_edits_allowed(self) -> bool:
        return bool(self.owner.config.get("self_management", {}).get("allow_source_edits", True))

    def _is_source_file(self, path: Path) -> bool:
        return path.suffix in {".py", ".cmd"} or path.name in {"micius", "pyproject.toml"}

    def _backup(self, path: Path) -> Path:
        self.backup_dir.mkdir(parents=True, exist_ok=True)
        rel = path.relative_to(self.project_root).as_posix()
        safe = re.sub(r"[^a-zA-Z0-9_.-]+", "__", rel)
        backup = self.backup_dir / f"{int(time.time() * 1000)}__{safe}.bak"
        shutil.copy2(path, backup)
        return backup


def _deep_merge(target: Dict[str, Any], patch: Dict[str, Any]) -> None:
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(target.get(key), dict):
            _deep_merge(target[key], value)
        else:
            target[key] = value


def _short_text(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_len:
        raise ValueError(f"text exceeds {max_len} characters")
    return text


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _is_secret_key(str(key)):
                result[key] = _redacted_secret(item)
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    return value


def _redact_text(text: str) -> str:
    patterns = [
        (r"(api_key\"\s*:\s*\")([^\"]+)(\")", r"\1<redacted>\3"),
        (r"(sk-[a-zA-Z0-9_-]{12,})", "<redacted-api-key>"),
        (r"(Authorization:\s*Bearer\s+)(\S+)", r"\1<redacted>"),
    ]
    for pattern, replacement in patterns:
        text = re.sub(pattern, replacement, text)
    return text


def _is_secret_key(key: str) -> bool:
    lowered = key.lower()
    if lowered in {"api_key", "apikey", "secret", "password", "authorization"}:
        return True
    if lowered.endswith("_secret") or lowered.endswith("_password"):
        return True
    if lowered.endswith("_token") and lowered not in {"max_tokens"}:
        return True
    return False


def _redacted_secret(value: Any) -> str:
    text = "" if value is None else str(value)
    if len(text) <= 10:
        return "<redacted>"
    return text[:4] + "..." + text[-4:]


def _format_timestamp(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _format_diagnostic_report(report: Dict[str, Any]) -> str:
    lines = [
        "# Micius Diagnostic Report",
        "",
        f"- generated_at: {report.get('generated_at')}",
        f"- contact_email: {report.get('contact_email') or '<not configured>'}",
        f"- project_root: {report.get('project_root')}",
        f"- python: {report.get('python')}",
        f"- platform: {report.get('platform')}",
        f"- model: {report.get('model')}",
        f"- remote_error: {report.get('remote_error') or '<none>'}",
        "",
        "## Summary",
        "",
        f"- tool_count: {report.get('tool_count')}",
        f"- firmware_count: {len(report.get('firmware') or [])}",
        f"- usb_status: {(report.get('usb') or {}).get('status') if isinstance(report.get('usb'), dict) else '<skipped>'}",
        "",
        "## Config Redacted",
        "",
        "```json",
        json.dumps(report.get("config", {}), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Dependencies",
        "",
        "```json",
        json.dumps(report.get("dependencies", {}), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## USB And Serial",
        "",
        "```json",
        json.dumps(
            {
                "usb": report.get("usb"),
                "platformio_devices": report.get("platformio_devices"),
            },
            ensure_ascii=False,
            indent=2,
            default=str,
        ),
        "```",
        "",
        "## Firmware",
        "",
        "```json",
        json.dumps(report.get("firmware", []), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Tools",
        "",
        "```json",
        json.dumps(report.get("tools", []), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Recent Events Redacted",
        "",
        "```json",
        json.dumps(report.get("recent_events", []), ensure_ascii=False, indent=2, default=str),
        "```",
        "",
        "## Notes",
        "",
        "- This report is generated locally and secrets are redacted before writing.",
        "- If you share this report, review it once before sending.",
    ]
    return _redact_text("\n".join(lines)) + "\n"


def _as_list(value: Any) -> List[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _is_safe_serial_port(port: str) -> bool:
    return bool(re.fullmatch(r"(COM\d{1,3}|/dev/(ttyUSB|ttyACM)\d+|/dev/cu\.[A-Za-z0-9_.-]+)", port))


def _clean_rss_text(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip()

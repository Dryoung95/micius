import json
import math
import platform
import re
import shutil
import subprocess
import time
import base64
from pathlib import Path
from typing import Any, Callable, Dict, List

from atlas_agent.manifest import CapabilityManifest, ManifestError, NAME_PATTERN, default_manifest_path


ToolFunc = Callable[[Dict[str, Any]], Dict[str, Any]]


class ToolRegistry:
    def __init__(self, manifest: CapabilityManifest) -> None:
        self._tools: Dict[str, ToolFunc] = {}
        self._schemas: Dict[str, Dict[str, Any]] = {}
        self.virtual_outputs: Dict[str, Any] = {}
        self.started_at = time.time()
        self.manifest = manifest

    def register(self, schema: Dict[str, Any], func: ToolFunc) -> None:
        name = schema["function"]["name"]
        self._schemas[name] = schema
        self._tools[name] = func

    def list_schemas(self) -> List[Dict[str, Any]]:
        return [self._schemas[name] for name in sorted(self._schemas)]

    def call(self, name: str, args: Dict[str, Any]) -> Dict[str, Any]:
        if name not in self._tools:
            raise KeyError(f"unknown tool: {name}")
        return self._tools[name](args)


def build_registry(device_id: str, manifest_path: str | None = None) -> ToolRegistry:
    manifest = CapabilityManifest(Path(manifest_path) if manifest_path else default_manifest_path(), device_id)
    registry = ToolRegistry(manifest)

    def get_device_status(args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "device_id": args.get("device_id") or device_id,
            "role": "micius_remote_tool_node",
            "safe_mode": True,
            "uptime_sec": round(time.time() - registry.started_at, 3),
            "platform": platform.platform(),
            "python": platform.python_version(),
            "available_tools": [schema["function"]["name"] for schema in registry.list_schemas()],
            "virtual_outputs": registry.virtual_outputs,
            "capability_manifest": {
                "node_count": len(registry.manifest.data.get("nodes", {})),
                "peripheral_count": len(registry.manifest.data.get("peripherals", {})),
                "note_count": len(registry.manifest.data.get("notes", [])),
                "path": str(registry.manifest.path),
            },
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "get_device_status",
                "description": "Return current Micius remote device-node status and available tools.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device_id": {"type": "string", "description": "Optional device id."}
                    },
                    "required": [],
                },
            },
        },
        get_device_status,
    )

    def read_mock_sensor(args: Dict[str, Any]) -> Dict[str, Any]:
        sensor = str(args.get("sensor") or "distance")
        now = time.time()
        if sensor == "distance":
            value = 0.45 + 0.18 * math.sin(now / 3.0)
            unit = "m"
        elif sensor == "temperature":
            value = 39.0 + 2.0 * math.sin(now / 20.0)
            unit = "celsius"
        elif sensor == "battery":
            value = 76.0
            unit = "percent"
        else:
            value = 0.0
            unit = "unknown"
        return {
            "sensor": sensor,
            "value": round(value, 4),
            "unit": unit,
            "timestamp": now,
            "source": "mock",
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "read_mock_sensor",
                "description": "Read a simulated sensor value on the Atlas node. Use before real hardware is wired.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "sensor": {
                            "type": "string",
                            "description": "One of: distance, temperature, battery.",
                        }
                    },
                    "required": ["sensor"],
                },
            },
        },
        read_mock_sensor,
    )

    def get_capability_manifest(args: Dict[str, Any]) -> Dict[str, Any]:
        include_notes = bool(args.get("include_notes", True))
        snapshot = registry.manifest.snapshot()
        if not include_notes:
            snapshot["manifest"] = dict(snapshot["manifest"])
            snapshot["manifest"]["notes"] = []
        return snapshot

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "get_capability_manifest",
                "description": (
                    "Return the persistent device capability manifest: registered peripherals, "
                    "safety notes, protocols, and known device memory."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_notes": {"type": "boolean", "description": "Whether to include device notes."}
                    },
                    "required": [],
                },
            },
        },
        get_capability_manifest,
    )

    def get_device_levels(args: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "levels": registry.manifest.level_profiles(),
            "nodes": registry.manifest.list_nodes(),
            "peripherals": registry.manifest.list_peripherals(),
            "model": {
                "atlas": "Linux-capable edge node that can host Micius tools and bridge to hardware",
                "esp32": "lightweight MCU node behind an edge node or PC for IO, sampling, and failsafe work",
            },
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "get_device_levels",
                "description": (
                    "Return the two supported device levels and registered nodes: "
                    "Linux-capable edge nodes and esp32-class MCU subnodes."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        get_device_levels,
    )

    def register_device_node(args: Dict[str, Any]) -> Dict[str, Any]:
        node = registry.manifest.upsert_node(args)
        return {
            "status": "registered",
            "node": node,
            "level_profile": registry.manifest.level_profiles().get(node["level"], {}),
            "manifest_path": str(registry.manifest.path),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "register_device_node",
                "description": (
                    "Persistently register or update a device node. Use level=atlas for Linux-capable edge boards "
                    "and level=esp32 for ESP32-class MCU subnodes connected by serial, Wi-Fi, MQTT, etc."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Stable node id, e.g. atlas_200i or esp32_drive."},
                        "level": {"type": "string", "description": "atlas or esp32."},
                        "transport": {"type": "string", "description": "local, serial, uart, usb, wifi, mqtt, tcp, or custom."},
                        "endpoint": {"type": "string", "description": "Transport endpoint such as COM5, /dev/ttyUSB0, or mqtt topic prefix."},
                        "description": {"type": "string"},
                        "capabilities": {"type": "array", "items": {"type": "string"}},
                        "safety": {"type": "object"},
                        "metadata": {"type": "object"},
                    },
                    "required": ["name", "level", "transport"],
                },
            },
        },
        register_device_node,
    )

    def register_peripheral(args: Dict[str, Any]) -> Dict[str, Any]:
        spec = registry.manifest.upsert_peripheral(args)
        node = registry.manifest.get_node(spec["node"])
        return {
            "status": "registered",
            "peripheral": spec,
            "node": node,
            "manifest_path": str(registry.manifest.path),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "register_peripheral",
                "description": (
                    "Persistently register or update an Atlas-connected peripheral. "
                    "Use this to extend the device capability graph across future sessions."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {
                            "type": "string",
                            "description": "Stable id, e.g. front_distance or arm_servo_1.",
                        },
                        "node": {
                            "type": "string",
                            "description": "Device node this peripheral is attached to. Defaults to the Atlas node.",
                        },
                        "kind": {
                            "type": "string",
                            "description": "sensor, actuator, io, camera, serial_bus, mcu, or service.",
                        },
                        "protocol": {
                            "type": "string",
                            "description": "mock, virtual, gpio, serial, i2c, spi, can, camera, mqtt, or custom.",
                        },
                        "description": {"type": "string"},
                        "unit": {"type": "string"},
                        "value_type": {"type": "string"},
                        "read": {
                            "type": "object",
                            "description": "Read metadata. For mock sensors use {\"mock_sensor\":\"distance\"}.",
                        },
                        "write": {"type": "object"},
                        "safety": {"type": "object", "description": "Limits, thresholds, permissions, or hazard notes."},
                        "tags": {"type": "array", "items": {"type": "string"}},
                    },
                    "required": ["name", "kind", "protocol"],
                },
            },
        },
        register_peripheral,
    )

    def read_registered_peripheral(args: Dict[str, Any]) -> Dict[str, Any]:
        name = str(args.get("name") or "")
        peripheral = registry.manifest.get_peripheral(name)
        node = registry.manifest.get_node(peripheral["node"])
        protocol = peripheral.get("protocol")
        read_cfg = peripheral.get("read", {})
        if protocol == "mock":
            sensor = str(read_cfg.get("mock_sensor") or read_cfg.get("sensor") or name)
            reading = read_mock_sensor({"sensor": sensor})
            reading["registered_name"] = name
            reading["peripheral"] = peripheral
            reading["node"] = node
            return reading
        if protocol == "virtual":
            return {
                "registered_name": name,
                "value": registry.virtual_outputs.get(name),
                "source": "virtual_output",
                "peripheral": peripheral,
                "node": node,
                "timestamp": time.time(),
            }
        if node.get("level") == "esp32":
            return {
                "registered_name": name,
                "status": "bridge_not_implemented",
                "message": (
                    f"peripheral is attached to ESP32 node {node['name']!r} via {node['transport']!r}; "
                    "runtime bridge is registered but not implemented yet"
                ),
                "peripheral": peripheral,
                "node": node,
            }
        return {
            "registered_name": name,
            "status": "not_implemented",
            "message": f"protocol {protocol!r} is registered but no runtime driver is implemented yet",
            "peripheral": peripheral,
            "node": node,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "read_registered_peripheral",
                "description": (
                    "Read a peripheral previously registered in the persistent manifest. "
                    "Currently supports mock and virtual protocols; other protocols return a structured not_implemented result."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Registered peripheral name."}
                    },
                    "required": ["name"],
                },
            },
        },
        read_registered_peripheral,
    )

    def capture_camera_frame(args: Dict[str, Any]) -> Dict[str, Any]:
        device = str(args.get("device") or "/dev/video0")
        width = int(args.get("width") or 640)
        height = int(args.get("height") or 480)
        timeout_sec = float(args.get("timeout_sec") or 8)
        include_base64 = bool(args.get("include_base64", True))
        if not re.fullmatch(r"/dev/video\d+", device):
            raise ValueError("camera device must look like /dev/video0")
        if width not in {160, 176, 320, 352, 640, 800, 1280}:
            raise ValueError("unsupported width for safe camera capture")
        if height not in {120, 144, 240, 288, 480, 600, 720}:
            raise ValueError("unsupported height for safe camera capture")
        if timeout_sec <= 0 or timeout_sec > 20:
            raise ValueError("timeout_sec must be in (0, 20]")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            return {
                "status": "error",
                "error": "ffmpeg not found on Atlas node",
            }
        capture_dir = registry.manifest.path.parent / "captures"
        capture_dir.mkdir(parents=True, exist_ok=True)
        output = capture_dir / f"camera_{int(time.time() * 1000)}.jpg"
        command = [
            ffmpeg,
            "-y",
            "-hide_banner",
            "-loglevel",
            "error",
            "-f",
            "v4l2",
            "-video_size",
            f"{width}x{height}",
            "-i",
            device,
            "-frames:v",
            "1",
            str(output),
        ]
        completed = subprocess.run(command, capture_output=True, text=True, timeout=timeout_sec)
        if completed.returncode != 0:
            return {
                "status": "error",
                "device": device,
                "command": command,
                "stderr": completed.stderr.strip(),
            }
        result = {
            "status": "ok",
            "device": device,
            "width": width,
            "height": height,
            "path": str(output),
            "size_bytes": output.stat().st_size,
            "timestamp": time.time(),
        }
        if include_base64:
            result["mime_type"] = "image/jpeg"
            result["image_base64"] = base64.b64encode(output.read_bytes()).decode("ascii")
        return result

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "capture_camera_frame",
                "description": "Capture one JPEG frame from a UVC camera on the connected Linux-capable device node using ffmpeg.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "device": {"type": "string", "description": "Video device path, e.g. /dev/video0."},
                        "width": {"type": "integer", "description": "Frame width. Default 640."},
                        "height": {"type": "integer", "description": "Frame height. Default 480."},
                        "timeout_sec": {"type": "number", "description": "Capture timeout, max 20 seconds."},
                        "include_base64": {
                            "type": "boolean",
                            "description": "Return JPEG bytes as base64. Keep true when the model should describe the image.",
                        },
                    },
                    "required": [],
                },
            },
        },
        capture_camera_frame,
    )

    def record_device_note(args: Dict[str, Any]) -> Dict[str, Any]:
        note = registry.manifest.add_note(
            title=str(args.get("title") or ""),
            body=str(args.get("body") or ""),
            scope=str(args.get("scope") or "device"),
        )
        return {
            "status": "recorded",
            "note": note,
            "manifest_path": str(registry.manifest.path),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "record_device_note",
                "description": "Persist a device memory note such as wiring, calibration, hazards, or observed behavior.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "title": {"type": "string"},
                        "body": {"type": "string"},
                        "scope": {"type": "string", "description": "device, wiring, calibration, safety, or project."},
                    },
                    "required": ["title", "body"],
                },
            },
        },
        record_device_note,
    )

    def set_virtual_output(args: Dict[str, Any]) -> Dict[str, Any]:
        name = str(args.get("name") or "output")
        value = args.get("value")
        registry.virtual_outputs[name] = value
        return {
            "name": name,
            "value": value,
            "status": "set",
            "virtual_outputs": registry.virtual_outputs,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "set_virtual_output",
                "description": "Set a virtual output value. This is a safe stand-in for GPIO or actuator output.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string"},
                        "value": {
                            "description": "Any small JSON value to store as the output state."
                        },
                    },
                    "required": ["name", "value"],
                },
            },
        },
        set_virtual_output,
    )

    def execute_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        script = str(args.get("script") or "")
        _validate_dsl_script(script)
        context: Dict[str, Any] = {}
        outputs: Dict[str, Any] = {}
        logs: List[str] = []
        for raw_line in script.splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            match = re.fullmatch(r"READ_(?:SENSOR|PERIPHERAL)\s+([a-zA-Z_][\w-]*)\s+AS\s+([a-zA-Z_]\w*)", line)
            if match:
                sensor, var_name = match.groups()
                try:
                    reading = read_registered_peripheral({"name": sensor})
                    if reading.get("status") in {"not_implemented", "bridge_not_implemented"}:
                        reading = read_mock_sensor({"sensor": sensor})
                except (ManifestError, ValueError):
                    reading = read_mock_sensor({"sensor": sensor})
                context[var_name] = reading["value"]
                unit = reading.get("unit", "")
                logs.append(f"{var_name}={reading['value']} {unit}".strip())
                continue
            match = re.fullmatch(
                r"IF\s+([a-zA-Z_]\w*)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s+THEN\s+SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+)(?:\s+ELSE\s+SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+))?",
                line,
            )
            if match:
                var_name, operator, limit, key, value, else_key, else_value = match.groups()
                current = float(context.get(var_name, 0.0))
                if _compare_number(current, operator, float(limit)):
                    outputs[key] = _coerce_scalar(value)
                    registry.virtual_outputs[key] = outputs[key]
                    logs.append(f"condition true; {key}={outputs[key]}")
                else:
                    if else_key is not None and else_value is not None:
                        outputs[else_key] = _coerce_scalar(else_value)
                        registry.virtual_outputs[else_key] = outputs[else_key]
                        logs.append(f"condition false; {else_key}={outputs[else_key]}")
                    else:
                        logs.append("condition false")
                continue
            match = re.fullmatch(r"SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+)", line)
            if match:
                key, value = match.groups()
                outputs[key] = _coerce_scalar(value)
                registry.virtual_outputs[key] = outputs[key]
                logs.append(f"{key}={outputs[key]}")
                continue
            raise ValueError(f"unsupported DSL line: {line}")
        return {
            "status": "ok",
            "context": context,
            "outputs": outputs,
            "logs": logs,
            "virtual_outputs": registry.virtual_outputs,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "execute_dsl_script",
                "description": (
                    "Execute a restricted line-based DSL. Supported forms: "
                    "READ_SENSOR distance AS d; READ_PERIPHERAL front_distance AS d; "
                    "IF d < 0.35 THEN SET action=stop ELSE SET action=go; SET key=value."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string", "description": "Restricted DSL script."}
                    },
                    "required": ["script"],
                },
            },
        },
        execute_dsl_script,
    )

    def write_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        name = _validate_script_name(str(args.get("name") or ""))
        script = str(args.get("script") or "").strip()
        if not script:
            raise ValueError("script is required")
        _validate_dsl_script(script)
        description = _short_text(args.get("description", ""), 512)
        tags = _string_items(args.get("tags", []), 32, 64)
        overwrite = bool(args.get("overwrite", True))
        path = _script_path(registry.manifest, name)
        if path.exists() and not overwrite:
            raise ValueError(f"script already exists: {name}")
        now = time.time()
        existing = _read_script_file(path) if path.exists() else {}
        permissions = args.get("permissions") or existing.get("permissions") or {}
        if not isinstance(permissions, dict):
            raise ValueError("permissions must be an object")
        record = {
            "schema_version": 1,
            "type": "micius_dsl_script",
            "name": name,
            "description": description,
            "script": script,
            "tags": tags,
            "permissions": {
                "can_read_peripherals": bool(permissions.get("can_read_peripherals", True)),
                "can_set_virtual_outputs": bool(permissions.get("can_set_virtual_outputs", True)),
                "can_call_host_shell": False,
            },
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        _write_json_file(path, record)
        return {
            "status": "saved",
            "script": _script_summary(record),
            "path": str(path),
            "validated": True,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "write_dsl_script",
                "description": (
                    "Persist a restricted DSL script on the Atlas node. Use this when Micius should remember "
                    "a reusable embedded behavior instead of only executing an inline one-off script."
                ),
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Stable script id, e.g. avoid_front_obstacle."},
                        "description": {"type": "string"},
                        "script": {"type": "string", "description": "Restricted DSL script text."},
                        "tags": {"type": "array", "items": {"type": "string"}},
                        "permissions": {"type": "object"},
                        "overwrite": {"type": "boolean", "description": "Whether an existing script may be replaced. Default true."},
                    },
                    "required": ["name", "script"],
                },
            },
        },
        write_dsl_script,
    )

    def list_dsl_scripts(args: Dict[str, Any]) -> Dict[str, Any]:
        include_script = bool(args.get("include_script", False))
        scripts = _list_script_records(registry.manifest)
        return {
            "status": "ok",
            "script_count": len(scripts),
            "scripts": [
                record if include_script else _script_summary(record)
                for record in scripts
            ],
            "directory": str(_scripts_dir(registry.manifest)),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "list_dsl_scripts",
                "description": "List reusable restricted DSL scripts saved on the Atlas node.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "include_script": {"type": "boolean", "description": "Include full script text. Default false."}
                    },
                    "required": [],
                },
            },
        },
        list_dsl_scripts,
    )

    def get_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        name = _validate_script_name(str(args.get("name") or ""))
        record = _load_script_record(registry.manifest, name)
        return {
            "status": "ok",
            "script": record,
            "path": str(_script_path(registry.manifest, name)),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "get_dsl_script",
                "description": "Read a saved restricted DSL script by name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Saved script id."}
                    },
                    "required": ["name"],
                },
            },
        },
        get_dsl_script,
    )

    def run_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        name = _validate_script_name(str(args.get("name") or ""))
        record = _load_script_record(registry.manifest, name)
        result = execute_dsl_script({"script": record["script"]})
        return {
            "status": "ok",
            "script": _script_summary(record),
            "execution": result,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "run_dsl_script",
                "description": "Run a saved restricted DSL script by name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Saved script id."}
                    },
                    "required": ["name"],
                },
            },
        },
        run_dsl_script,
    )

    def validate_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        script = str(args.get("script") or "")
        _validate_dsl_script(script)
        executable_lines = [
            line.strip()
            for line in script.splitlines()
            if line.strip() and not line.strip().startswith("#")
        ]
        return {
            "status": "valid",
            "line_count": len(executable_lines),
            "supported_forms": [
                "READ_SENSOR name AS var",
                "READ_PERIPHERAL name AS var",
                "IF var < 0.35 THEN SET action=stop ELSE SET action=go",
                "SET key=value",
            ],
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "validate_dsl_script",
                "description": "Validate a restricted DSL script without executing it.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "script": {"type": "string", "description": "Restricted DSL script text."}
                    },
                    "required": ["script"],
                },
            },
        },
        validate_dsl_script,
    )

    def delete_dsl_script(args: Dict[str, Any]) -> Dict[str, Any]:
        name = _validate_script_name(str(args.get("name") or ""))
        path = _script_path(registry.manifest, name)
        if not path.exists():
            return {
                "status": "not_found",
                "name": name,
                "path": str(path),
            }
        path.unlink()
        return {
            "status": "deleted",
            "name": name,
            "path": str(path),
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "delete_dsl_script",
                "description": "Delete a saved restricted DSL script by name.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Saved script id."}
                    },
                    "required": ["name"],
                },
            },
        },
        delete_dsl_script,
    )

    def list_device_resources(args: Dict[str, Any]) -> Dict[str, Any]:
        resources = [
            _resource("micius://device/status", "Device status", "status", "Runtime status and virtual outputs."),
            _resource("micius://device/manifest", "Capability manifest", "manifest", "Persistent nodes, peripherals, and notes."),
            _resource("micius://device/nodes", "Device nodes", "nodes", "Registered Atlas and ESP32-class nodes."),
            _resource("micius://device/peripherals", "Peripherals", "peripherals", "Registered sensors, actuators, cameras, and buses."),
            _resource("micius://device/tools", "Tool schemas", "tools", "Callable controlled tools exposed by this Atlas node."),
            _resource("micius://device/outputs", "Virtual outputs", "outputs", "Current safe virtual output state."),
            _resource("micius://scripts", "DSL scripts", "scripts", "Saved reusable restricted DSL scripts."),
        ]
        for node in registry.manifest.list_nodes():
            name = node["name"]
            resources.append(_resource(f"micius://nodes/{name}", f"Node {name}", "node", node.get("description", "")))
        for peripheral in registry.manifest.list_peripherals():
            name = peripheral["name"]
            resources.append(
                _resource(
                    f"micius://peripherals/{name}",
                    f"Peripheral {name}",
                    "peripheral",
                    peripheral.get("description", ""),
                )
            )
            resources.append(
                _resource(
                    f"micius://peripherals/{name}/reading",
                    f"Reading {name}",
                    "peripheral_reading",
                    "Read the current value through the registered safe driver.",
                )
            )
        for script in _list_script_records(registry.manifest):
            resources.append(
                _resource(
                    f"micius://scripts/{script['name']}",
                    f"Script {script['name']}",
                    "script",
                    script.get("description", ""),
                )
            )
        return {
            "status": "ok",
            "protocol": "micius-embedded-capability-v1",
            "resource_count": len(resources),
            "resources": resources,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "list_device_resources",
                "description": (
                    "List MCP-like embedded resources that the model can read: status, manifest, "
                    "nodes, peripherals, current readings, tool schemas, virtual outputs, and saved scripts."
                ),
                "parameters": {"type": "object", "properties": {}, "required": []},
            },
        },
        list_device_resources,
    )

    def read_device_resource(args: Dict[str, Any]) -> Dict[str, Any]:
        uri = str(args.get("uri") or "").strip()
        parts = _resource_parts(uri)
        if parts == ["device", "status"]:
            content = get_device_status({})
        elif parts == ["device", "manifest"]:
            content = registry.manifest.snapshot()
        elif parts == ["device", "nodes"]:
            content = {"nodes": registry.manifest.list_nodes()}
        elif parts == ["device", "peripherals"]:
            content = {"peripherals": registry.manifest.list_peripherals()}
        elif parts == ["device", "tools"]:
            content = {"tools": registry.list_schemas()}
        elif parts == ["device", "outputs"]:
            content = {"virtual_outputs": registry.virtual_outputs}
        elif parts == ["scripts"]:
            content = list_dsl_scripts({"include_script": False})
        elif len(parts) == 2 and parts[0] == "scripts":
            content = get_dsl_script({"name": parts[1]})
        elif len(parts) == 2 and parts[0] == "nodes":
            content = {"node": registry.manifest.get_node(parts[1])}
        elif len(parts) == 2 and parts[0] == "peripherals":
            content = {"peripheral": registry.manifest.get_peripheral(parts[1])}
        elif len(parts) == 3 and parts[0] == "peripherals" and parts[2] == "reading":
            content = read_registered_peripheral({"name": parts[1]})
        else:
            raise ValueError(f"unknown resource uri: {uri}")
        return {
            "status": "ok",
            "uri": uri,
            "mime_type": "application/json",
            "content": content,
        }

    registry.register(
        {
            "type": "function",
            "function": {
                "name": "read_device_resource",
                "description": "Read one MCP-like embedded resource by URI.",
                "parameters": {
                    "type": "object",
                    "properties": {
                        "uri": {"type": "string", "description": "Resource URI from list_device_resources."}
                    },
                    "required": ["uri"],
                },
            },
        },
        read_device_resource,
    )

    return registry


def _coerce_scalar(value: str) -> Any:
    if value.lower() == "true":
        return True
    if value.lower() == "false":
        return False
    try:
        if "." in value:
            return float(value)
        return int(value)
    except ValueError:
        return value


def _compare_number(left: float, operator: str, right: float) -> bool:
    if operator == "<":
        return left < right
    if operator == "<=":
        return left <= right
    if operator == ">":
        return left > right
    if operator == ">=":
        return left >= right
    if operator == "==":
        return left == right
    if operator == "!=":
        return left != right
    raise ValueError(f"unsupported comparison operator: {operator}")


def _validate_dsl_script(script: str) -> None:
    if not isinstance(script, str):
        raise ValueError("script must be a string")
    lines = [line.strip() for line in script.splitlines() if line.strip() and not line.strip().startswith("#")]
    if not lines:
        raise ValueError("script must contain at least one executable line")
    for line in lines:
        if re.fullmatch(r"READ_(?:SENSOR|PERIPHERAL)\s+([a-zA-Z_][\w-]*)\s+AS\s+([a-zA-Z_]\w*)", line):
            continue
        if re.fullmatch(
            r"IF\s+([a-zA-Z_]\w*)\s*(<=|>=|==|!=|<|>)\s*(-?\d+(?:\.\d+)?)\s+THEN\s+SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+)(?:\s+ELSE\s+SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+))?",
            line,
        ):
            continue
        if re.fullmatch(r"SET\s+([a-zA-Z_]\w*)\s*=\s*([a-zA-Z0-9_.-]+)", line):
            continue
        raise ValueError(f"unsupported DSL line: {line}")


def _scripts_dir(manifest: CapabilityManifest) -> Path:
    path = manifest.path.parent / "scripts"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _validate_script_name(name: str) -> str:
    name = name.strip()
    if not NAME_PATTERN.fullmatch(name):
        raise ValueError("script name must match ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")
    return name


def _script_path(manifest: CapabilityManifest, name: str) -> Path:
    safe_name = _validate_script_name(name)
    return _scripts_dir(manifest) / f"{safe_name}.json"


def _read_script_file(path: Path) -> Dict[str, Any]:
    if not path.exists():
        raise ValueError(f"script not found: {path.stem}")
    with path.open("r", encoding="utf-8") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError("script file root must be an object")
    return data


def _write_json_file(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def _load_script_record(manifest: CapabilityManifest, name: str) -> Dict[str, Any]:
    record = _read_script_file(_script_path(manifest, name))
    if record.get("type") != "micius_dsl_script":
        raise ValueError(f"unsupported script type for {name}")
    script = record.get("script")
    if not isinstance(script, str):
        raise ValueError(f"script {name} has no script text")
    _validate_dsl_script(script)
    return record


def _list_script_records(manifest: CapabilityManifest) -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in sorted(_scripts_dir(manifest).glob("*.json")):
        try:
            record = _read_script_file(path)
            if record.get("type") == "micius_dsl_script" and isinstance(record.get("name"), str):
                records.append(record)
        except Exception:
            continue
    return records


def _script_summary(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "name": record.get("name"),
        "description": record.get("description", ""),
        "tags": record.get("tags", []),
        "permissions": record.get("permissions", {}),
        "updated_at": record.get("updated_at"),
    }


def _short_text(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_len:
        raise ValueError(f"text exceeds {max_len} characters")
    return text


def _string_items(value: Any, max_items: int, max_len: int) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError("value must be a list")
    items: List[str] = []
    for item in value[:max_items]:
        text = _short_text(item, max_len).strip()
        if text:
            items.append(text)
    return items


def _resource(uri: str, name: str, kind: str, description: str) -> Dict[str, Any]:
    return {
        "uri": uri,
        "name": name,
        "kind": kind,
        "description": description,
        "mime_type": "application/json",
    }


def _resource_parts(uri: str) -> List[str]:
    if not uri.startswith("micius://"):
        raise ValueError("resource uri must start with micius://")
    parts = [part for part in uri[len("micius://") :].split("/") if part]
    if not parts:
        raise ValueError("resource uri is empty")
    for part in parts:
        if not re.fullmatch(r"[a-zA-Z_][a-zA-Z0-9_-]{0,63}", part):
            raise ValueError(f"invalid resource path segment: {part}")
    return parts

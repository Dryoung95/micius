import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List


NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")
ALLOWED_KINDS = {"sensor", "actuator", "io", "camera", "serial_bus", "mcu", "service"}
ALLOWED_PROTOCOLS = {"mock", "virtual", "gpio", "serial", "i2c", "spi", "can", "camera", "mqtt", "custom"}
ALLOWED_NODE_LEVELS = {"atlas", "esp32"}
ALLOWED_NODE_TRANSPORTS = {"local", "serial", "uart", "usb", "wifi", "mqtt", "tcp", "custom"}


DEVICE_LEVEL_PROFILES: Dict[str, Dict[str, Any]] = {
    "atlas": {
        "label": "Linux-capable edge node",
        "role": "Runs Micius remote tool server, manifest, LLM bridge client-side tools, and heavier local perception.",
        "can_host_runtime": True,
        "can_call_llm": True,
        "typical_protocols": ["local", "gpio", "serial", "i2c", "spi", "can", "camera", "mqtt"],
        "recommended_responsibilities": [
            "device manifest",
            "tool server",
            "camera or AI perception",
            "serial bridge to MCU",
            "safe command arbitration",
        ],
    },
    "esp32": {
        "label": "ESP32-class MCU node",
        "role": "Lightweight subnode behind an edge node or PC; handles GPIO, ADC, PWM, simple sensors, and watchdog actions.",
        "can_host_runtime": False,
        "can_call_llm": False,
        "typical_protocols": ["serial", "uart", "wifi", "mqtt"],
        "recommended_responsibilities": [
            "low-level IO",
            "sensor sampling",
            "PWM/servo output",
            "local failsafe",
            "compact telemetry",
        ],
    },
}


class ManifestError(ValueError):
    pass


class CapabilityManifest:
    def __init__(self, path: Path, device_id: str) -> None:
        self.path = path
        self.device_id = device_id
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = self._load()

    def _load(self) -> Dict[str, Any]:
        if not self.path.exists():
            data = self._empty_manifest()
            self._write(data)
            return data
        with self.path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ManifestError("manifest root must be an object")
        changed = False
        data.setdefault("schema_version", 1)
        data.setdefault("device_id", self.device_id)
        data.setdefault("nodes", {})
        changed = self._ensure_default_atlas_node(data) or changed
        data.setdefault("peripherals", {})
        changed = self._migrate_peripherals(data) or changed
        data.setdefault("notes", [])
        data.setdefault("updated_at", time.time())
        if changed:
            data["updated_at"] = time.time()
            self._write(data)
        return data

    def _empty_manifest(self) -> Dict[str, Any]:
        now = time.time()
        return {
            "schema_version": 1,
            "device_id": self.device_id,
            "nodes": {
                self.device_id: self._default_atlas_node(time.time()),
            },
            "peripherals": {},
            "notes": [],
            "created_at": now,
            "updated_at": now,
        }

    def _write(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(self.path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(self.path)

    def snapshot(self) -> Dict[str, Any]:
        return {
            "path": str(self.path),
            "manifest": self.data,
            "node_count": len(self.data.get("nodes", {})),
            "peripheral_count": len(self.data.get("peripherals", {})),
            "note_count": len(self.data.get("notes", [])),
        }

    def level_profiles(self) -> Dict[str, Dict[str, Any]]:
        return DEVICE_LEVEL_PROFILES

    def list_nodes(self) -> List[Dict[str, Any]]:
        nodes = self.data.get("nodes", {})
        return [nodes[name] for name in sorted(nodes)]

    def get_node(self, name: str) -> Dict[str, Any]:
        self._validate_name(name)
        nodes = self.data.get("nodes", {})
        if name not in nodes:
            raise ManifestError(f"unknown device node: {name}")
        return nodes[name]

    def upsert_node(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        name = str(spec.get("name") or spec.get("node_id") or "").strip()
        self._validate_name(name)
        level = str(spec.get("level") or "esp32").strip()
        transport = str(spec.get("transport") or "serial").strip()
        if level not in ALLOWED_NODE_LEVELS:
            raise ManifestError(f"unsupported device node level: {level}")
        if transport not in ALLOWED_NODE_TRANSPORTS:
            raise ManifestError(f"unsupported node transport: {transport}")

        nodes = self.data.setdefault("nodes", {})
        existing = nodes.get(name, {})
        now = time.time()
        node = {
            "name": name,
            "level": level,
            "transport": transport,
            "endpoint": _short_string(spec.get("endpoint", existing.get("endpoint", "")), 256),
            "description": _short_string(spec.get("description", existing.get("description", "")), 512),
            "capabilities": _string_list(spec.get("capabilities", existing.get("capabilities", []))),
            "safety": _object_or_empty(spec.get("safety", existing.get("safety", {}))),
            "metadata": _object_or_empty(spec.get("metadata", existing.get("metadata", {}))),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        nodes[name] = node
        self.data["updated_at"] = now
        self._write(self.data)
        return node

    def list_peripherals(self) -> List[Dict[str, Any]]:
        peripherals = self.data.get("peripherals", {})
        return [peripherals[name] for name in sorted(peripherals)]

    def get_peripheral(self, name: str) -> Dict[str, Any]:
        self._validate_name(name)
        peripherals = self.data.get("peripherals", {})
        if name not in peripherals:
            raise ManifestError(f"unknown peripheral: {name}")
        return peripherals[name]

    def upsert_peripheral(self, spec: Dict[str, Any]) -> Dict[str, Any]:
        name = str(spec.get("name") or "").strip()
        self._validate_name(name)
        kind = str(spec.get("kind") or "sensor").strip()
        protocol = str(spec.get("protocol") or "mock").strip()
        peripherals = self.data.setdefault("peripherals", {})
        existing = peripherals.get(name, {})
        node = str(spec.get("node") or spec.get("node_id") or existing.get("node") or self.device_id).strip()
        if kind not in ALLOWED_KINDS:
            raise ManifestError(f"unsupported peripheral kind: {kind}")
        if protocol not in ALLOWED_PROTOCOLS:
            raise ManifestError(f"unsupported peripheral protocol: {protocol}")
        self.get_node(node)

        now = time.time()
        normalized = {
            "name": name,
            "node": node,
            "kind": kind,
            "protocol": protocol,
            "description": _short_string(spec.get("description", existing.get("description", "")), 512),
            "unit": _short_string(spec.get("unit", existing.get("unit", "")), 64),
            "value_type": _short_string(spec.get("value_type", existing.get("value_type", "")), 64),
            "read": _object_or_empty(spec.get("read", existing.get("read", {}))),
            "write": _object_or_empty(spec.get("write", existing.get("write", {}))),
            "safety": _object_or_empty(spec.get("safety", existing.get("safety", {}))),
            "tags": _string_list(spec.get("tags", existing.get("tags", []))),
            "created_at": existing.get("created_at", now),
            "updated_at": now,
        }
        peripherals[name] = normalized
        self.data["updated_at"] = now
        self._write(self.data)
        return normalized

    def add_note(self, title: str, body: str, scope: str = "device") -> Dict[str, Any]:
        title = _short_string(title, 128).strip()
        body = _short_string(body, 2048).strip()
        scope = _short_string(scope or "device", 64).strip()
        if not title:
            raise ManifestError("note title is required")
        if not body:
            raise ManifestError("note body is required")
        note = {
            "title": title,
            "body": body,
            "scope": scope,
            "created_at": time.time(),
        }
        notes = self.data.setdefault("notes", [])
        notes.append(note)
        if len(notes) > 200:
            del notes[:-200]
        self.data["updated_at"] = time.time()
        self._write(self.data)
        return note

    def _validate_name(self, name: str) -> None:
        if not NAME_PATTERN.fullmatch(name):
            raise ManifestError("name must match ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")

    def _default_atlas_node(self, now: float) -> Dict[str, Any]:
        return {
            "name": self.device_id,
            "level": "atlas",
            "transport": "local",
            "endpoint": "local",
            "description": "Primary Linux-capable edge node running the Micius remote tool server.",
            "capabilities": ["tool_server", "manifest", "mock_sensors", "device_bridge"],
            "safety": {"role": "edge_gateway"},
            "metadata": {},
            "created_at": now,
            "updated_at": now,
        }

    def _ensure_default_atlas_node(self, data: Dict[str, Any]) -> bool:
        nodes = data.setdefault("nodes", {})
        if self.device_id not in nodes:
            nodes[self.device_id] = self._default_atlas_node(time.time())
            return True
        return False

    def _migrate_peripherals(self, data: Dict[str, Any]) -> bool:
        changed = False
        peripherals = data.setdefault("peripherals", {})
        for peripheral in peripherals.values():
            if isinstance(peripheral, dict) and not peripheral.get("node"):
                peripheral["node"] = self.device_id
                changed = True
        return changed


def default_manifest_path() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "atlas_manifest.json"


def _short_string(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_len:
        raise ManifestError(f"string exceeds {max_len} characters")
    return text


def _object_or_empty(value: Any) -> Dict[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ManifestError("field must be an object")
    return value


def _string_list(value: Any) -> List[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ManifestError("tags must be a list")
    tags = []
    for item in value[:32]:
        text = _short_string(item, 64).strip()
        if text:
            tags.append(text)
    return tags

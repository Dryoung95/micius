import copy
import json
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict, List, Tuple


@dataclass(frozen=True)
class ToolPolicy:
    risk: str = "read_only"
    parallel_safe: bool = True
    result_compaction: str = "summary"
    max_inline_chars: int = 6000


DEFAULT_TOOL_POLICY = ToolPolicy()

TOOL_POLICIES: Dict[str, ToolPolicy] = {
    "micius_self_status": ToolPolicy("read_only", True, "full", 6000),
    "micius_usb_scan": ToolPolicy("read_only", True, "summary", 6000),
    "micius_connection_check": ToolPolicy("read_only", True, "summary", 6000),
    "micius_serial_monitor": ToolPolicy("read_only", True, "artifact", 3000),
    "micius_dependency_install": ToolPolicy("shell", False, "artifact", 4000),
    "micius_platformio": ToolPolicy("shell", False, "artifact", 5000),
    "micius_web_search": ToolPolicy("network", True, "summary", 6000),
    "micius_diagnostic_report": ToolPolicy("read_only", True, "artifact", 4000),
    "micius_device_research": ToolPolicy("file_write", False, "summary", 6000),
    "micius_esp32_flash": ToolPolicy("firmware_flash", False, "artifact", 4000),
    "micius_set_model": ToolPolicy("config_write", False, "full", 4000),
    "micius_config_update": ToolPolicy("config_write", False, "summary", 5000),
    "micius_pdf_read": ToolPolicy("read_only", True, "artifact", 5000),
    "micius_file_write": ToolPolicy("file_write", False, "summary", 5000),
    "micius_file_replace": ToolPolicy("file_write", False, "summary", 5000),
    "capture_camera_frame": ToolPolicy("hardware_read", False, "artifact", 3000),
    "read_registered_peripheral": ToolPolicy("hardware_read", False, "summary", 4000),
    "set_virtual_output": ToolPolicy("hardware_write", False, "summary", 4000),
    "write_dsl_script": ToolPolicy("file_write", False, "summary", 6000),
    "run_dsl_script": ToolPolicy("hardware_write", False, "summary", 5000),
    "execute_dsl_script": ToolPolicy("hardware_write", False, "summary", 5000),
}


class ContextLedger:
    def __init__(self) -> None:
        self.request_count = 0
        self.total_prompt_chars = 0
        self.last_prompt_chars = 0
        self.total_tool_result_chars = 0
        self.total_compacted_chars = 0
        self.last_usage: Dict[str, Any] = {}
        self.compactions: List[Dict[str, Any]] = []

    def record_request(self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]]) -> None:
        message_chars = len(json.dumps(messages, ensure_ascii=False, default=str, separators=(",", ":")))
        tool_chars = len(json.dumps(tools, ensure_ascii=False, default=str, separators=(",", ":")))
        self.request_count += 1
        self.last_prompt_chars = message_chars + tool_chars
        self.total_prompt_chars += self.last_prompt_chars

    def record_response_usage(self, usage: Dict[str, Any]) -> None:
        self.last_usage = copy.deepcopy(usage) if isinstance(usage, dict) else {}

    def record_tool_compaction(self, info: Dict[str, Any]) -> None:
        original = int(info.get("original_chars") or 0)
        compacted = int(info.get("compacted_chars") or 0)
        self.total_tool_result_chars += original
        self.total_compacted_chars += max(0, original - compacted)
        if info.get("compacted"):
            self.compactions.append(copy.deepcopy(info))
            self.compactions = self.compactions[-20:]

    def snapshot(self) -> Dict[str, Any]:
        return {
            "requests": self.request_count,
            "last_prompt_chars": self.last_prompt_chars,
            "last_prompt_tokens_estimate": estimate_tokens_from_chars(self.last_prompt_chars),
            "total_prompt_chars": self.total_prompt_chars,
            "total_prompt_tokens_estimate": estimate_tokens_from_chars(self.total_prompt_chars),
            "total_tool_result_chars": self.total_tool_result_chars,
            "total_compacted_chars": self.total_compacted_chars,
            "last_provider_usage": self.last_usage,
            "recent_compactions": self.compactions[-8:],
        }


class ArtifactStore:
    def __init__(self, project_root: Path, session_id: str) -> None:
        self.root = project_root / "data" / "tool_artifacts" / safe_segment(session_id)

    def write_json(self, tool_name: str, payload: Any) -> Path:
        self.root.mkdir(parents=True, exist_ok=True)
        stamp = int(time.time() * 1000)
        path = self.root / f"{stamp}_{safe_segment(tool_name)}.json"
        with path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)
            f.write("\n")
        return path


def policy_for_tool(name: str) -> ToolPolicy:
    if name in TOOL_POLICIES:
        return TOOL_POLICIES[name]
    if name.startswith("micius_"):
        return DEFAULT_TOOL_POLICY
    if any(word in name for word in ("write", "set", "run", "execute", "flash", "upload")):
        return ToolPolicy("hardware_write", False, "summary", 5000)
    if any(word in name for word in ("capture", "camera", "read", "get", "list")):
        return ToolPolicy("hardware_read", False, "summary", 5000)
    return ToolPolicy("remote_tool", False, "summary", 5000)


def tool_policy_snapshot(tool_names: List[str]) -> Dict[str, Any]:
    return {name: asdict(policy_for_tool(name)) for name in sorted(set(tool_names))}


def compact_tool_result(
    *,
    tool_name: str,
    result: Dict[str, Any],
    artifact_store: ArtifactStore,
) -> Tuple[Dict[str, Any], Dict[str, Any]]:
    policy = policy_for_tool(tool_name)
    original_text = json.dumps(result, ensure_ascii=False, default=str, separators=(",", ":"))
    original_chars = len(original_text)
    info: Dict[str, Any] = {
        "tool": tool_name,
        "policy": asdict(policy),
        "original_chars": original_chars,
        "compacted": False,
        "artifact_path": None,
    }
    if policy.result_compaction == "full" and original_chars <= policy.max_inline_chars:
        info["compacted_chars"] = original_chars
        return result, info

    if original_chars <= policy.max_inline_chars and policy.result_compaction != "artifact":
        compacted = _strip_large_binary(copy.deepcopy(result))
        compacted_chars = len(json.dumps(compacted, ensure_ascii=False, default=str, separators=(",", ":")))
        info["compacted_chars"] = compacted_chars
        info["compacted"] = compacted_chars < original_chars
        return compacted, info

    artifact_path = artifact_store.write_json(tool_name, result)
    compacted = {
        "status": result.get("status", "ok") if isinstance(result, dict) else "ok",
        "tool": tool_name,
        "result_compacted": True,
        "artifact_path": str(artifact_path),
        "summary": summarize_value(result),
    }
    compacted_chars = len(json.dumps(compacted, ensure_ascii=False, default=str, separators=(",", ":")))
    info.update(
        {
            "compacted": True,
            "compacted_chars": compacted_chars,
            "artifact_path": str(artifact_path),
        }
    )
    return compacted, info


def summarize_value(value: Any, max_items: int = 24) -> Dict[str, Any]:
    summary: Dict[str, Any] = {}
    if not isinstance(value, dict):
        return {"value": _short_text(value, 800)}
    for key in ("status", "error", "error_type", "path", "local_path", "returncode", "operation", "port", "baud"):
        if key in value:
            summary[key] = _short_text(value[key], 800)
    data = value.get("data")
    if isinstance(data, dict):
        summary["data_keys"] = sorted(str(key) for key in data.keys())[:max_items]
        for key in ("status", "path", "local_path", "resource_count", "script_count", "port", "baud", "duration_sec"):
            if key in data:
                summary[key] = _short_text(data[key], 800)
        for key in ("stdout", "stderr", "output", "log", "text", "content"):
            if key in data:
                summary[f"{key}_tail"] = _tail_text(data[key], 1600)
    else:
        summary["keys"] = sorted(str(key) for key in value.keys())[:max_items]
    for key in ("stdout", "stderr", "output", "log", "text", "content"):
        if key in value:
            summary[f"{key}_tail"] = _tail_text(value[key], 1600)
    return summary


def estimate_tokens_from_chars(chars: int) -> int:
    if chars <= 0:
        return 0
    return max(1, int(chars / 3.6))


def safe_segment(value: str) -> str:
    cleaned = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in str(value))
    return cleaned[:96] or "artifact"


def _strip_large_binary(value: Any) -> Any:
    if isinstance(value, dict):
        for key in list(value.keys()):
            if key in {"image_base64", "base64", "data_base64"}:
                value[key] = "<omitted>"
            else:
                value[key] = _strip_large_binary(value[key])
    elif isinstance(value, list):
        return [_strip_large_binary(item) for item in value]
    return value


def _short_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 32] + "...<truncated>"


def _tail_text(value: Any, max_chars: int) -> str:
    text = "" if value is None else str(value)
    if len(text) <= max_chars:
        return text
    return "<truncated>\n" + text[-max_chars:]

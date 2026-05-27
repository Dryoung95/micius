import json
import re
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
TASK_ID_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,96}$")
KIND_PATTERN = re.compile(r"^[a-zA-Z0-9_.-]{1,80}$")

DEFAULT_STAGES = [
    {
        "id": "task_constructor",
        "title": "Task Constructor",
        "goal": "Convert user intent, board facts, and tool constraints into a structured hardware task.",
    },
    {
        "id": "hardware_designer",
        "title": "Hardware Designer",
        "goal": "Identify board class, ports, protocols, safety limits, and a verification route before acting.",
    },
    {
        "id": "firmware_coder",
        "title": "Firmware / Script Coder",
        "goal": "Create or edit firmware, scripts, or device-node resources in the allowed workspace.",
    },
    {
        "id": "hardware_verifier",
        "title": "Hardware Verifier",
        "goal": "Compile, upload, read serial/device evidence, and compare observed behavior with the task goal.",
    },
    {
        "id": "profiler",
        "title": "Profiler",
        "goal": "Record latency, stability, data quality, throughput, or other task-specific runtime metrics.",
    },
    {
        "id": "skill_curator",
        "title": "Skill Curator",
        "goal": "Distill reusable board, port, error-fix, and workflow knowledge into a Micius skill.",
    },
]


class DeviceResearchLog:
    """Task, trace, and skill log for embedded-device bring-up."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "DeviceResearchLog":
        research_cfg = config.get("device_research", {})
        root_value = research_cfg.get("root", "data/device_research")
        root = Path(root_value)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        return cls(root)

    def create_task(
        self,
        description: str,
        target: str = "",
        board: str = "",
        port: str = "",
        project_dir: str = "",
    ) -> Dict[str, Any]:
        clean_description = _short_text(description, 2000).strip()
        if not clean_description:
            raise ValueError("description is required")
        now = time.time()
        task_id = self._new_task_id(clean_description)
        task_dir = self._task_dir(task_id)
        task_dir.mkdir(parents=True, exist_ok=False)
        task = {
            "schema_version": 1,
            "task_id": task_id,
            "description": clean_description,
            "target": _short_text(target, 200).strip(),
            "board": _short_text(board, 120).strip(),
            "port": _short_text(port, 80).strip(),
            "project_dir": _short_text(project_dir, 240).strip(),
            "status": "active",
            "created_at": _format_time(now),
            "updated_at": _format_time(now),
            "stages": [
                {
                    **stage,
                    "status": "done" if stage["id"] == "task_constructor" else "pending",
                    "updated_at": _format_time(now),
                    "summary": clean_description if stage["id"] == "task_constructor" else "",
                }
                for stage in DEFAULT_STAGES
            ],
            "artifacts": {
                "task": "task.json",
                "plan": "plan.md",
                "trace": "trace.jsonl",
            },
        }
        self._write_task(task_id, task)
        self._append_trace(
            task_id,
            {
                "kind": "task.created",
                "stage": "task_constructor",
                "status": "done",
                "summary": clean_description,
                "payload": {
                    "target": task["target"],
                    "board": task["board"],
                    "port": task["port"],
                    "project_dir": task["project_dir"],
                },
            },
        )
        self._write_plan(task)
        return self._task_summary(task)

    def list_tasks(self, limit: int = 20) -> Dict[str, Any]:
        tasks = []
        for path in sorted(self.root.glob("*/task.json"), reverse=True):
            try:
                task = _read_json(path)
            except (OSError, json.JSONDecodeError):
                continue
            tasks.append(self._task_summary(task))
            if len(tasks) >= max(1, min(int(limit), 100)):
                break
        return {"status": "ok", "root": str(self.root), "tasks": tasks}

    def show_task(self, task_id: str, include_trace: bool = True) -> Dict[str, Any]:
        task = self._read_task(task_id)
        result = {"status": "ok", "task": task, "paths": self.paths(task_id)}
        if include_trace:
            result["trace"] = self.read_trace(task_id, limit=80)
        return result

    def paths(self, task_id: str) -> Dict[str, str]:
        task_dir = self._task_dir(task_id)
        return {
            "task_dir": str(task_dir),
            "task": str(task_dir / "task.json"),
            "plan": str(task_dir / "plan.md"),
            "trace": str(task_dir / "trace.jsonl"),
        }

    def read_trace(self, task_id: str, limit: int = 80) -> List[Dict[str, Any]]:
        trace_path = self._task_dir(task_id) / "trace.jsonl"
        if not trace_path.exists():
            return []
        rows = []
        for line in trace_path.read_text(encoding="utf-8-sig").splitlines():
            if not line.strip():
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                rows.append({"kind": "trace.parse_error", "raw": line[:500]})
        return rows[-max(1, min(int(limit), 500)) :]

    def record_event(
        self,
        task_id: str,
        kind: str,
        summary: str,
        payload: Dict[str, Any] | None = None,
        stage: str = "hardware_verifier",
        status: str = "observed",
    ) -> Dict[str, Any]:
        self._validate_task_id(task_id)
        if not KIND_PATTERN.fullmatch(kind):
            raise ValueError("kind must match ^[a-zA-Z0-9_.-]{1,80}$")
        clean_summary = _short_text(summary, 1000).strip()
        if not clean_summary:
            raise ValueError("summary is required")
        clean_stage = self._validate_stage(stage)
        clean_status = _normalize_status(status)
        task = self._read_task(task_id)
        event = {
            "kind": kind,
            "stage": clean_stage,
            "status": clean_status,
            "summary": clean_summary,
            "payload": _redact(payload or {}),
        }
        self._append_trace(task_id, event)
        self._update_stage(task, clean_stage, clean_status, clean_summary)
        self._write_task(task_id, task)
        self._write_plan(task)
        return {"status": "recorded", "task_id": task_id, "event": event, "paths": self.paths(task_id)}

    def record_tool_result(
        self,
        task_id: str,
        kind: str,
        result: Dict[str, Any],
        stage: str,
        summary: str | None = None,
    ) -> Dict[str, Any]:
        result_status = str(result.get("status") or "observed")
        event_summary = summary or _summarize_result(kind, result)
        return self.record_event(
            task_id=task_id,
            kind=kind,
            summary=event_summary,
            payload=result,
            stage=stage,
            status=result_status,
        )

    def finish_task(self, task_id: str, status: str = "done") -> Dict[str, Any]:
        task = self._read_task(task_id)
        task["status"] = "done" if status not in {"failed", "blocked"} else status
        task["updated_at"] = _format_time(time.time())
        self._write_task(task_id, task)
        self._append_trace(
            task_id,
            {
                "kind": "task.finished",
                "stage": "skill_curator",
                "status": task["status"],
                "summary": f"Task marked {task['status']}.",
                "payload": {},
            },
        )
        self._write_plan(task)
        return {"status": "ok", "task": self._task_summary(task), "paths": self.paths(task_id)}

    def build_skill_body(self, task_id: str) -> Dict[str, Any]:
        task = self._read_task(task_id)
        trace = self.read_trace(task_id, limit=60)
        title = task.get("description", task_id).strip().splitlines()[0][:120]
        body_lines = [
            "Use this workflow when repeating a similar embedded-device bring-up, verification, or error-fix task.",
            "",
            "### Task Shape",
            "",
            f"- Description: {title}",
            f"- Target: {task.get('target') or 'not recorded'}",
            f"- Board: {task.get('board') or 'not recorded'}",
            f"- Port: {task.get('port') or 'not recorded'}",
            f"- Project: {task.get('project_dir') or 'not recorded'}",
            "",
            "### Verified Steps",
            "",
        ]
        for event in trace:
            if event.get("status") in {"ok", "done", "installed", "observed"}:
                body_lines.append(
                    f"- `{event.get('stage')}` / `{event.get('kind')}`: "
                    + str(event.get("summary", "")).replace("\n", " ")[:300]
                )
        body_lines.extend(
            [
                "",
                "### Reuse Rules",
                "",
                "- Re-scan USB/serial state before assuming port names.",
                "- Compile or validate generated code before upload.",
                "- After upload, read serial or sensor evidence before declaring success.",
                "- Record new port names, error signatures, and verified fixes back into Micius memory.",
            ]
        )
        return {
            "status": "ok",
            "task_id": task_id,
            "suggested_title": title,
            "body": "\n".join(body_lines).strip(),
        }

    def _read_task(self, task_id: str) -> Dict[str, Any]:
        self._validate_task_id(task_id)
        path = self._task_dir(task_id) / "task.json"
        if not path.exists():
            raise FileNotFoundError(str(path))
        data = _read_json(path)
        if not isinstance(data, dict):
            raise ValueError("task.json root must be an object")
        return data

    def _write_task(self, task_id: str, task: Dict[str, Any]) -> None:
        task["updated_at"] = _format_time(time.time())
        _write_json(self._task_dir(task_id) / "task.json", task)

    def _append_trace(self, task_id: str, event: Dict[str, Any]) -> None:
        self._validate_task_id(task_id)
        trace_path = self._task_dir(task_id) / "trace.jsonl"
        trace_path.parent.mkdir(parents=True, exist_ok=True)
        row = {
            "created_at": _format_time(time.time()),
            **event,
        }
        with trace_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + "\n")

    def _write_plan(self, task: Dict[str, Any]) -> None:
        task_dir = self._task_dir(task["task_id"])
        lines = [
            f"# DeviceResearch Plan: {task['task_id']}",
            "",
            f"- status: {task.get('status')}",
            f"- description: {task.get('description')}",
            f"- target: {task.get('target') or 'not recorded'}",
            f"- board: {task.get('board') or 'not recorded'}",
            f"- port: {task.get('port') or 'not recorded'}",
            f"- project_dir: {task.get('project_dir') or 'not recorded'}",
            "",
            "## DeviceResearch Stages",
            "",
        ]
        for stage in task.get("stages", []):
            summary = str(stage.get("summary") or "").replace("\n", " ").strip()
            if len(summary) > 240:
                summary = summary[:237] + "..."
            lines.append(f"- [{stage.get('status', 'pending')}] {stage.get('id')}: {stage.get('goal')}")
            if summary:
                lines.append(f"  - evidence: {summary}")
        lines.extend(["", "## Recent Trace", ""])
        for event in self.read_trace(task["task_id"], limit=20):
            lines.append(
                f"- {event.get('created_at')} `{event.get('stage')}` `{event.get('kind')}` "
                f"[{event.get('status')}]: {str(event.get('summary', '')).replace(chr(10), ' ')[:240]}"
            )
        (task_dir / "plan.md").write_text("\n".join(lines).strip() + "\n", encoding="utf-8")

    def _update_stage(self, task: Dict[str, Any], stage_id: str, status: str, summary: str) -> None:
        stages = task.setdefault("stages", [])
        for stage in stages:
            if stage.get("id") == stage_id:
                stage["status"] = _stage_status(status)
                stage["summary"] = summary
                stage["updated_at"] = _format_time(time.time())
                return
        stages.append(
            {
                "id": stage_id,
                "title": stage_id.replace("_", " ").title(),
                "goal": "Custom research stage.",
                "status": _stage_status(status),
                "summary": summary,
                "updated_at": _format_time(time.time()),
            }
        )

    def _validate_stage(self, stage: str) -> str:
        clean = _short_text(stage, 80).strip()
        if not KIND_PATTERN.fullmatch(clean):
            raise ValueError("stage must match ^[a-zA-Z0-9_.-]{1,80}$")
        return clean

    def _validate_task_id(self, task_id: str) -> None:
        if not TASK_ID_PATTERN.fullmatch(str(task_id or "")):
            raise ValueError("invalid task_id")

    def _task_dir(self, task_id: str) -> Path:
        self._validate_task_id(task_id)
        return self.root / task_id

    def _new_task_id(self, description: str) -> str:
        base = f"dr_{int(time.time())}_{_slug(description)}"
        task_id = base[:90].rstrip("_.-")
        suffix = 1
        while (self.root / task_id).exists():
            suffix += 1
            task_id = f"{base[:84].rstrip('_.-')}_{suffix}"
        return task_id

    def _task_summary(self, task: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "task_id": task.get("task_id"),
            "status": task.get("status"),
            "description": task.get("description"),
            "target": task.get("target"),
            "board": task.get("board"),
            "port": task.get("port"),
            "project_dir": task.get("project_dir"),
            "created_at": task.get("created_at"),
            "updated_at": task.get("updated_at"),
            "stage_status": {stage.get("id"): stage.get("status") for stage in task.get("stages", [])},
            "paths": self.paths(str(task.get("task_id"))),
        }


def _read_json(path: Path) -> Dict[str, Any]:
    with path.open("r", encoding="utf-8-sig") as f:
        data = json.load(f)
    if not isinstance(data, dict):
        raise ValueError(f"{path} root must be an object")
    return data


def _write_json(path: Path, data: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
        f.write("\n")
    tmp.replace(path)


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _short_text(value: Any, max_len: int) -> str:
    text = "" if value is None else str(value)
    if len(text) > max_len:
        raise ValueError(f"text exceeds {max_len} characters")
    return text


def _slug(text: str) -> str:
    words = re.findall(r"[A-Za-z0-9]+", text.lower())
    if not words:
        return "task"
    return "-".join(words[:8])[:48] or "task"


def _normalize_status(status: str) -> str:
    clean = str(status or "observed").strip().lower()
    if clean in {"ok", "done", "passed", "success", "installed"}:
        return "ok"
    if clean in {"failed", "fail", "error", "timeout", "blocked"}:
        return "failed"
    if clean in {"partial", "running", "active"}:
        return clean
    return "observed"


def _stage_status(status: str) -> str:
    clean = _normalize_status(status)
    if clean == "ok":
        return "done"
    if clean == "failed":
        return "blocked"
    return "active" if clean in {"observed", "partial", "running", "active"} else clean


def _summarize_result(kind: str, result: Dict[str, Any]) -> str:
    status = result.get("status", "observed")
    if kind.startswith("platformio."):
        op = result.get("operation") or kind.rsplit(".", 1)[-1]
        return f"PlatformIO {op} returned {status}, returncode={result.get('returncode')}."
    if kind == "usb.scan":
        data = result.get("data", {}) if isinstance(result.get("data"), dict) else {}
        return (
            f"USB scan returned {status}: "
            f"{len(data.get('serial_ports') or [])} serial ports, "
            f"{len(data.get('usb_devices') or data.get('lsusb') or [])} USB rows."
        )
    if kind == "serial.monitor":
        return (
            f"Serial monitor returned {status}: "
            f"{result.get('bytes_read', 0)} bytes, {result.get('line_count', 0)} lines."
        )
    if kind == "connection.check":
        return f"Device-node connection check returned {status}."
    return f"{kind} returned {status}."


def _redact(value: Any) -> Any:
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            lowered = str(key).lower()
            if any(token in lowered for token in ("api_key", "apikey", "password", "secret", "token", "authorization")):
                result[key] = "<redacted>"
            else:
                result[key] = _redact(item)
        return result
    if isinstance(value, list):
        return [_redact(item) for item in value]
    if isinstance(value, str):
        return re.sub(r"sk-[A-Za-z0-9_-]{12,}", "<redacted-api-key>", value)
    return value

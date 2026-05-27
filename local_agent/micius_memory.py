import json
import re
import sqlite3
import time
from pathlib import Path
from typing import Any, Dict, List


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SKILL_NAME_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")


class MiciusMemory:
    def __init__(self, root: Path, context_max_chars: int = 8000) -> None:
        self.root = root
        self.context_max_chars = context_max_chars
        self.memory_path = root / "MEMORY.md"
        self.user_path = root / "USER.md"
        self.db_path = root / "sessions.db"
        self.reflections_dir = root / "reflections"
        self.skills_dir = root / "skills"
        self.usage_path = root / "skill_usage.json"
        self._ensure_layout()

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "MiciusMemory":
        memory_cfg = config.get("memory", {})
        root_value = memory_cfg.get("root", "micius_memory")
        root = Path(root_value)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        max_chars = int(memory_cfg.get("context_max_chars", 8000))
        return cls(root=root, context_max_chars=max_chars)

    def _ensure_layout(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.reflections_dir.mkdir(parents=True, exist_ok=True)
        self.skills_dir.mkdir(parents=True, exist_ok=True)
        if not self.memory_path.exists():
            self.memory_path.write_text(
                "# Micius Memory\n\n"
                "Persistent facts Micius should remember across sessions.\n\n"
                "## Device Facts\n\n"
                "- A remote embedded device node may be configured by host and port.\n"
                "- Board profiles are examples, not the product boundary.\n\n"
                "## Lessons\n\n"
                "- Keep hardware control behind structured tools and safe DSL scripts.\n",
                encoding="utf-8",
            )
        if not self.user_path.exists():
            self.user_path.write_text(
                "# User Preferences\n\n"
                "- Prefer concise Chinese answers.\n"
                "- Micius should grow as a general embedded Agent workbench across many boards and MCU nodes.\n",
                encoding="utf-8",
            )
        if not self.usage_path.exists():
            self._write_json(self.usage_path, {"items": {}, "updated_at": time.time()})
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    created_at REAL NOT NULL,
                    kind TEXT NOT NULL,
                    content TEXT NOT NULL,
                    metadata_json TEXT NOT NULL
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_created_at ON events(created_at)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_events_kind ON events(kind)")
            try:
                conn.execute(
                    """
                    CREATE VIRTUAL TABLE IF NOT EXISTS events_fts
                    USING fts5(content, kind, metadata_json, content='events', content_rowid='id')
                    """
                )
                conn.execute(
                    """
                    CREATE TRIGGER IF NOT EXISTS events_ai AFTER INSERT ON events BEGIN
                        INSERT INTO events_fts(rowid, content, kind, metadata_json)
                        VALUES (new.id, new.content, new.kind, new.metadata_json);
                    END
                    """
                )
            except sqlite3.OperationalError:
                pass

    def build_context(self) -> str:
        context = {
            "memory_root": str(self.root),
            "memory": self._read_text(self.memory_path),
            "user": self._read_text(self.user_path),
            "recent_reflections": self.list_reflections(limit=3).get("reflections", []),
            "workflow_skills": self.list_skills(limit=12).get("skills", []),
            "usage_top": self.usage_top(limit=8),
        }
        text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(text) > self.context_max_chars:
            return text[: self.context_max_chars - 64] + "...<truncated>"
        return text

    def read(self, target: str = "all") -> Dict[str, Any]:
        if target == "memory":
            return {"target": "memory", "path": str(self.memory_path), "content": self._read_text(self.memory_path)}
        if target == "user":
            return {"target": "user", "path": str(self.user_path), "content": self._read_text(self.user_path)}
        return {
            "target": "all",
            "root": str(self.root),
            "memory": self._read_text(self.memory_path),
            "user": self._read_text(self.user_path),
        }

    def add_fact(self, text: str, target: str = "memory", source: str = "manual") -> Dict[str, Any]:
        clean = text.strip()
        if not clean:
            raise ValueError("memory text is required")
        if len(clean) > 1200:
            raise ValueError("memory text exceeds 1200 characters")
        path = self.user_path if target == "user" else self.memory_path
        timestamp = _format_time(time.time())
        with path.open("a", encoding="utf-8") as f:
            f.write(f"\n- [{timestamp}] {clean}\n")
        self.log_event("memory.add", clean, {"target": target, "source": source})
        return {"status": "added", "target": target, "path": str(path), "text": clean}

    def log_event(self, kind: str, content: str, metadata: Dict[str, Any] | None = None) -> None:
        text = str(content)
        if len(text) > 12000:
            text = text[:11936] + "...<truncated>"
        metadata_json = json.dumps(metadata or {}, ensure_ascii=False, separators=(",", ":"))
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO events(created_at, kind, content, metadata_json) VALUES (?, ?, ?, ?)",
                (time.time(), kind, text, metadata_json),
            )

    def recent_events(self, limit: int = 20, kind: str | None = None) -> Dict[str, Any]:
        limit = max(1, min(int(limit), 100))
        params: List[Any] = []
        where = ""
        if kind:
            where = "WHERE kind = ?"
            params.append(kind)
        params.append(limit)
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                f"SELECT id, created_at, kind, content, metadata_json FROM events {where} ORDER BY id DESC LIMIT ?",
                params,
            ).fetchall()
        return {"status": "ok", "events": [self._event_row(row) for row in rows]}

    def search_events(self, query: str, limit: int = 20) -> Dict[str, Any]:
        clean = query.strip()
        if not clean:
            raise ValueError("search query is required")
        limit = max(1, min(int(limit), 100))
        with sqlite3.connect(self.db_path) as conn:
            try:
                rows = conn.execute(
                    """
                    SELECT e.id, e.created_at, e.kind, e.content, e.metadata_json
                    FROM events_fts f
                    JOIN events e ON e.id = f.rowid
                    WHERE events_fts MATCH ?
                    ORDER BY e.id DESC
                    LIMIT ?
                    """,
                    (clean, limit),
                ).fetchall()
            except sqlite3.OperationalError:
                rows = conn.execute(
                    """
                    SELECT id, created_at, kind, content, metadata_json
                    FROM events
                    WHERE content LIKE ? OR kind LIKE ? OR metadata_json LIKE ?
                    ORDER BY id DESC
                    LIMIT ?
                    """,
                    (f"%{clean}%", f"%{clean}%", f"%{clean}%", limit),
                ).fetchall()
        return {"status": "ok", "query": clean, "events": [self._event_row(row) for row in rows]}

    def add_reflection(self, title: str, body: str, tags: List[str] | None = None) -> Dict[str, Any]:
        clean_title = title.strip()
        clean_body = body.replace("\\n", "\n").strip()
        if not clean_title:
            raise ValueError("reflection title is required")
        if not clean_body:
            raise ValueError("reflection body is required")
        safe_name = "".join(ch.lower() if ch.isalnum() else "-" for ch in clean_title).strip("-")[:64] or "reflection"
        path = self.reflections_dir / f"{int(time.time())}_{safe_name}.md"
        tag_text = ", ".join(tags or [])
        path.write_text(
            f"# {clean_title}\n\n"
            f"- created_at: {_format_time(time.time())}\n"
            f"- tags: {tag_text}\n\n"
            f"{clean_body}\n",
            encoding="utf-8",
        )
        self.log_event("reflection.add", clean_title + "\n" + clean_body, {"path": str(path), "tags": tags or []})
        return {"status": "recorded", "path": str(path), "title": clean_title}

    def list_reflections(self, limit: int = 20) -> Dict[str, Any]:
        reflections = []
        for path in sorted(self.reflections_dir.glob("*.md"), reverse=True)[: max(1, min(int(limit), 100))]:
            text = self._read_text(path)
            title = path.stem
            for line in text.splitlines():
                if line.startswith("# "):
                    title = line[2:].strip()
                    break
            reflections.append({"title": title, "path": str(path), "size_bytes": path.stat().st_size})
        return {"status": "ok", "reflections": reflections}

    def read_reflection(self, name_or_path: str) -> Dict[str, Any]:
        candidate = Path(name_or_path)
        if not candidate.is_absolute():
            direct = self.reflections_dir / name_or_path
            if direct.exists():
                candidate = direct
            else:
                matches = list(self.reflections_dir.glob(f"*{name_or_path}*.md"))
                if not matches:
                    raise ValueError(f"reflection not found: {name_or_path}")
                candidate = sorted(matches, reverse=True)[0]
        return {"status": "ok", "path": str(candidate), "content": self._read_text(candidate)}

    def add_skill(
        self,
        name: str,
        body: str,
        title: str | None = None,
        tags: List[str] | None = None,
        triggers: List[str] | None = None,
    ) -> Dict[str, Any]:
        clean_name = self._validate_skill_name(name)
        clean_body = body.strip()
        if not clean_body:
            raise ValueError("skill body is required")
        if len(clean_body) > 8000:
            raise ValueError("skill body exceeds 8000 characters")
        clean_title = (title or clean_name.replace("_", " ").replace("-", " ").title()).strip()
        now = time.time()
        path = self.skills_dir / f"{clean_name}.md"
        metadata = {
            "name": clean_name,
            "title": clean_title,
            "created_at": _format_time(now),
            "updated_at": _format_time(now),
            "tags": tags or [],
            "triggers": triggers or [],
        }
        path.write_text(
            "# " + clean_title + "\n\n"
            "<!-- micius-skill-metadata\n"
            + json.dumps(metadata, ensure_ascii=False, indent=2)
            + "\n-->\n\n"
            "## When To Use\n\n"
            + _skill_when_to_use(clean_title, triggers or [])
            + "\n\n"
            "## Workflow\n\n"
            + clean_body.strip()
            + "\n",
            encoding="utf-8",
        )
        self.log_event("workflow_skill.add", clean_name + "\n" + clean_body, {"path": str(path), "tags": tags or []})
        self.record_usage("workflow_skill", clean_name, {"action": "add"})
        return {"status": "saved", "name": clean_name, "path": str(path), "title": clean_title}

    def list_skills(self, limit: int = 50) -> Dict[str, Any]:
        skills = []
        for path in sorted(self.skills_dir.glob("*.md"))[: max(1, min(int(limit), 200))]:
            skills.append(self._skill_summary(path))
        return {"status": "ok", "skills_dir": str(self.skills_dir), "skills": skills}

    def read_skill(self, name: str) -> Dict[str, Any]:
        path = self._skill_path(name)
        if not path.exists():
            raise ValueError(f"workflow skill not found: {name}")
        content = self._read_text(path)
        summary = self._skill_summary(path)
        self.log_event("workflow_skill.read", summary["name"], {"path": str(path)})
        return {"status": "ok", "skill": summary, "content": content}

    def use_skill(self, name: str) -> Dict[str, Any]:
        result = self.read_skill(name)
        skill_name = result["skill"]["name"]
        self.record_usage("workflow_skill", skill_name, {"action": "use"})
        self.log_event("workflow_skill.use", skill_name, {"path": result["skill"]["path"]})
        return result

    def search_skills(self, query: str, limit: int = 20) -> Dict[str, Any]:
        clean = query.strip().lower()
        if not clean:
            raise ValueError("skill search query is required")
        matches = []
        for path in sorted(self.skills_dir.glob("*.md")):
            text = self._read_text(path)
            if clean in text.lower() or clean in path.stem.lower():
                summary = self._skill_summary(path)
                index = text.lower().find(clean)
                if index >= 0:
                    start = max(0, index - 120)
                    end = min(len(text), index + 240)
                    summary["snippet"] = text[start:end].replace("\n", " ")
                matches.append(summary)
            if len(matches) >= max(1, min(int(limit), 100)):
                break
        return {"status": "ok", "query": query, "skills": matches}

    def delete_skill(self, name: str) -> Dict[str, Any]:
        path = self._skill_path(name)
        if not path.exists():
            return {"status": "not_found", "name": name, "path": str(path)}
        path.unlink()
        self._remove_usage("workflow_skill", self._validate_skill_name(name))
        self.log_event("workflow_skill.delete", name, {"path": str(path)})
        return {"status": "deleted", "name": name, "path": str(path)}

    def record_usage(self, kind: str, name: str, metadata: Dict[str, Any] | None = None) -> None:
        usage = self._read_json(self.usage_path, {"items": {}})
        items = usage.setdefault("items", {})
        key = f"{kind}:{name}"
        item = items.setdefault(key, {"kind": kind, "name": name, "count": 0, "first_used_at": time.time()})
        item["count"] = int(item.get("count", 0)) + 1
        item["last_used_at"] = time.time()
        item["metadata"] = metadata or item.get("metadata", {})
        usage["updated_at"] = time.time()
        self._write_json(self.usage_path, usage)

    def _remove_usage(self, kind: str, name: str) -> None:
        usage = self._read_json(self.usage_path, {"items": {}})
        items = usage.setdefault("items", {})
        items.pop(f"{kind}:{name}", None)
        usage["updated_at"] = time.time()
        self._write_json(self.usage_path, usage)

    def usage_top(self, limit: int = 20) -> List[Dict[str, Any]]:
        usage = self._read_json(self.usage_path, {"items": {}})
        items = list(usage.get("items", {}).values())
        items.sort(key=lambda item: (int(item.get("count", 0)), float(item.get("last_used_at", 0))), reverse=True)
        return items[: max(1, min(int(limit), 100))]

    def curator_status(self) -> Dict[str, Any]:
        with sqlite3.connect(self.db_path) as conn:
            event_count = conn.execute("SELECT COUNT(*) FROM events").fetchone()[0]
            repeated_events = conn.execute(
                "SELECT kind, COUNT(*) FROM events GROUP BY kind HAVING COUNT(*) >= 3 ORDER BY COUNT(*) DESC LIMIT 10"
            ).fetchall()
        usage = self._read_json(self.usage_path, {"items": {}})
        stale = []
        now = time.time()
        for key, item in usage.get("items", {}).items():
            last_used = float(item.get("last_used_at", 0))
            if last_used and now - last_used > 30 * 24 * 3600:
                stale.append({"key": key, "name": item.get("name"), "kind": item.get("kind"), "last_used_at": _format_time(last_used)})
        return {
            "status": "ok",
            "root": str(self.root),
            "memory_bytes": self.memory_path.stat().st_size,
            "user_bytes": self.user_path.stat().st_size,
            "event_count": event_count,
            "reflection_count": len(list(self.reflections_dir.glob("*.md"))),
            "workflow_skill_count": len(list(self.skills_dir.glob("*.md"))),
            "usage_count": len(usage.get("items", {})),
            "repeated_event_kinds": [{"kind": row[0], "count": row[1]} for row in repeated_events],
            "stale_usage": stale[:20],
            "suggestions": self._curator_suggestions(event_count, stale),
            "skill_suggestions": self._workflow_skill_suggestions(repeated_events, usage),
        }

    def curator_run(self) -> Dict[str, Any]:
        status = self.curator_status()
        reflection = self.add_reflection(
            "Micius curator pass",
            "Curator status snapshot:\n\n```json\n"
            + json.dumps(status, ensure_ascii=False, indent=2)
            + "\n```\n",
            tags=["curator"],
        )
        return {"status": "recorded", "curator_status": status, "reflection": reflection}

    def _curator_suggestions(self, event_count: int, stale: List[Dict[str, Any]]) -> List[str]:
        suggestions = []
        if self.memory_path.stat().st_size > 12000:
            suggestions.append("MEMORY.md is growing; summarize older bullets into stable facts.")
        if event_count > 5000:
            suggestions.append("sessions.db has many events; export or compact old low-value events.")
        if stale:
            suggestions.append("Some scripts/skills have not been used for 30+ days; consider archiving or merging them.")
        if not suggestions:
            suggestions.append("No cleanup required.")
        return suggestions

    def _workflow_skill_suggestions(self, repeated_events: List[tuple[Any, ...]], usage: Dict[str, Any]) -> List[str]:
        suggestions = []
        for item in self.usage_top(limit=20):
            kind = item.get("kind")
            name = item.get("name")
            count = int(item.get("count", 0))
            if kind in {"tool", "script", "peripheral", "board", "camera"} and count >= 3:
                suggestions.append(f"Consider turning repeated {kind} usage `{name}` ({count}x) into a workflow skill.")
        for kind, count in repeated_events:
            if str(kind).startswith(("tool.", "script.", "camera.", "peripheral.")) and int(count) >= 3:
                suggestions.append(f"Repeated event kind `{kind}` occurred {count} times; review whether a skill or DSL script should capture it.")
        if not suggestions:
            suggestions.append("No workflow skill candidates yet.")
        return suggestions[:10]

    def _event_row(self, row: tuple[Any, ...]) -> Dict[str, Any]:
        metadata = {}
        try:
            metadata = json.loads(row[4])
        except json.JSONDecodeError:
            metadata = {"raw": row[4]}
        content = row[3]
        if len(content) > 1200:
            content = content[:1160] + "...<truncated>"
        return {
            "id": row[0],
            "created_at": _format_time(float(row[1])),
            "kind": row[2],
            "content": content,
            "metadata": metadata,
        }

    def _read_text(self, path: Path) -> str:
        return path.read_text(encoding="utf-8-sig") if path.exists() else ""

    def _read_json(self, path: Path, default: Dict[str, Any]) -> Dict[str, Any]:
        if not path.exists():
            return default
        with path.open("r", encoding="utf-8-sig") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default

    def _write_json(self, path: Path, data: Dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        with tmp.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2, sort_keys=True)
            f.write("\n")
        tmp.replace(path)

    def _validate_skill_name(self, name: str) -> str:
        clean = name.strip()
        if not SKILL_NAME_PATTERN.fullmatch(clean):
            raise ValueError("skill name must match ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")
        return clean

    def _skill_path(self, name: str) -> Path:
        return self.skills_dir / f"{self._validate_skill_name(name)}.md"

    def _skill_summary(self, path: Path) -> Dict[str, Any]:
        text = self._read_text(path)
        metadata = _extract_skill_metadata(text)
        title = metadata.get("title") or path.stem
        for line in text.splitlines():
            if line.startswith("# "):
                title = line[2:].strip()
                break
        description = ""
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if line.strip() == "## When To Use":
                description = _first_nonempty(lines[index + 1 :])
                break
        return {
            "name": metadata.get("name") or path.stem,
            "title": title,
            "description": description,
            "tags": metadata.get("tags", []),
            "triggers": metadata.get("triggers", []),
            "path": str(path),
            "updated_at": metadata.get("updated_at"),
            "size_bytes": path.stat().st_size,
        }


def _format_time(timestamp: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(timestamp))


def _skill_when_to_use(title: str, triggers: List[str]) -> str:
    if triggers:
        return "Use this workflow when: " + "; ".join(triggers) + "."
    return f"Use this workflow when repeating the `{title}` process."


def _extract_skill_metadata(text: str) -> Dict[str, Any]:
    start_marker = "<!-- micius-skill-metadata"
    end_marker = "-->"
    start = text.find(start_marker)
    if start < 0:
        return {}
    start += len(start_marker)
    end = text.find(end_marker, start)
    if end < 0:
        return {}
    raw = text[start:end].strip()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _first_nonempty(lines: List[str]) -> str:
    for line in lines:
        clean = line.strip()
        if clean and not clean.startswith("#"):
            return clean
    return ""

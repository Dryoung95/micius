import json
import re
from pathlib import Path
from typing import Any, Dict, List


BOARD_ID_PATTERN = re.compile(r"^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")
PROJECT_ROOT = Path(__file__).resolve().parents[1]


class BoardKnowledgeError(ValueError):
    pass


class BoardKnowledgeBase:
    def __init__(self, root: Path, active_boards: List[str] | None = None, context_max_chars: int = 10000) -> None:
        self.root = root
        self.boards_dir = root / "boards"
        self.skills_dir = root / "skills"
        self.manuals_dir = root / "manuals"
        self.active_boards = active_boards or []
        self.context_max_chars = context_max_chars

    @classmethod
    def from_config(cls, config: Dict[str, Any]) -> "BoardKnowledgeBase":
        boards_cfg = config.get("boards", {})
        root_value = boards_cfg.get("knowledge_dir", "board_knowledge")
        root = Path(root_value)
        if not root.is_absolute():
            root = PROJECT_ROOT / root
        active = boards_cfg.get("active", ["atlas_200i_dk_a2"])
        if isinstance(active, str):
            active_boards = [active]
        elif isinstance(active, list):
            active_boards = [str(item) for item in active]
        else:
            active_boards = []
        max_chars = int(boards_cfg.get("context_max_chars", 10000))
        return cls(root=root, active_boards=active_boards, context_max_chars=max_chars)

    def list_boards(self) -> Dict[str, Any]:
        profiles = []
        if self.boards_dir.exists():
            for path in sorted(self.boards_dir.glob("*.json")):
                try:
                    profile = self._read_profile(path.stem)
                except Exception as exc:
                    profiles.append({"board_id": path.stem, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
                    continue
                profiles.append(self._profile_summary(profile))
        return {
            "status": "ok",
            "knowledge_root": str(self.root),
            "active_boards": self.active_boards,
            "boards": profiles,
        }

    def list_manuals(self) -> Dict[str, Any]:
        manuals = []
        if self.manuals_dir.exists():
            for path in sorted(self.manuals_dir.rglob("*")):
                if path.is_file():
                    manuals.append(
                        {
                            "path": str(path.relative_to(self.root)).replace("\\", "/"),
                            "size_bytes": path.stat().st_size,
                        }
                    )
        return {
            "status": "ok",
            "manuals_dir": str(self.manuals_dir),
            "manuals": manuals,
        }

    def get_profile(self, board_id: str) -> Dict[str, Any]:
        return self._read_profile(board_id)

    def get_ports(self, board_id: str | None = None) -> Dict[str, Any]:
        profile = self._read_profile(board_id or self._default_board_id())
        return {
            "status": "ok",
            "board_id": profile["board_id"],
            "ports": profile.get("ports", []),
            "port_aliases": profile.get("port_aliases", {}),
        }

    def get_peripherals(self, board_id: str | None = None) -> Dict[str, Any]:
        profile = self._read_profile(board_id or self._default_board_id())
        return {
            "status": "ok",
            "board_id": profile["board_id"],
            "peripherals": profile.get("peripherals", []),
        }

    def get_skill(self, board_id: str | None = None) -> Dict[str, Any]:
        selected = board_id or self._default_board_id()
        self._validate_board_id(selected)
        path = self.skills_dir / f"{selected}.md"
        if not path.exists():
            raise BoardKnowledgeError(f"board skill not found: {selected}")
        return {
            "status": "ok",
            "board_id": selected,
            "path": str(path),
            "content": path.read_text(encoding="utf-8-sig"),
        }

    def set_active_boards(self, board_ids: List[str]) -> None:
        for board_id in board_ids:
            self._read_profile(board_id)
        self.active_boards = board_ids

    def build_context(self) -> str:
        boards = []
        for board_id in self.active_boards:
            try:
                profile = self._read_profile(board_id)
                skill = self.get_skill(board_id).get("content", "")
                boards.append(
                    {
                        "profile": {
                            "board_id": profile.get("board_id"),
                            "display_name": profile.get("display_name"),
                            "aliases": profile.get("aliases", []),
                            "manual_status": profile.get("manual_status", {}),
                            "port_aliases": profile.get("port_aliases", {}),
                            "ports": profile.get("ports", []),
                            "peripherals": profile.get("peripherals", []),
                        },
                        "skill_markdown": skill,
                    }
                )
            except Exception as exc:
                boards.append({"board_id": board_id, "status": "error", "error": f"{type(exc).__name__}: {exc}"})
        context = {
            "status": "ok",
            "knowledge_root": str(self.root),
            "active_boards": self.active_boards,
            "manuals": self.list_manuals().get("manuals", []),
            "boards": boards,
        }
        text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        if len(text) > self.context_max_chars:
            return text[: self.context_max_chars - 64] + "...<truncated>"
        return text

    def _read_profile(self, board_id: str) -> Dict[str, Any]:
        self._validate_board_id(board_id)
        path = self.boards_dir / f"{board_id}.json"
        if not path.exists():
            raise BoardKnowledgeError(f"board profile not found: {board_id}")
        with path.open("r", encoding="utf-8-sig") as f:
            profile = json.load(f)
        if not isinstance(profile, dict):
            raise BoardKnowledgeError("board profile root must be an object")
        if profile.get("board_id") != board_id:
            raise BoardKnowledgeError("board_id does not match filename")
        profile.setdefault("ports", [])
        profile.setdefault("peripherals", [])
        profile.setdefault("port_aliases", {})
        return profile

    def _default_board_id(self) -> str:
        if not self.active_boards:
            raise BoardKnowledgeError("no active board configured")
        return self.active_boards[0]

    def _validate_board_id(self, board_id: str) -> None:
        if not BOARD_ID_PATTERN.fullmatch(board_id):
            raise BoardKnowledgeError("board id must match ^[a-zA-Z_][a-zA-Z0-9_-]{0,63}$")

    def _profile_summary(self, profile: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "board_id": profile.get("board_id"),
            "display_name": profile.get("display_name"),
            "aliases": profile.get("aliases", []),
            "manual_status": profile.get("manual_status", {}),
            "port_count": len(profile.get("ports", [])),
            "peripheral_count": len(profile.get("peripherals", [])),
            "active": profile.get("board_id") in self.active_boards,
        }

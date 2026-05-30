import argparse
import base64
import copy
import json
import os
import time
from pathlib import Path
from typing import Any, Callable, Dict, List

from local_agent.board_knowledge import BoardKnowledgeBase
from local_agent.context_engine import (
    ArtifactStore,
    ContextLedger,
    compact_tool_result,
    tool_policy_snapshot,
)
from local_agent.device_connect import build_connection_report, get_device_node_config
from local_agent.llm_client import LLMClient
from local_agent.micius_memory import MiciusMemory
from local_agent.remote_tools import RemoteDeviceClient
from local_agent.self_tools import SELF_TOOL_NAMES, LocalSelfTools


DEFAULT_SYSTEM_PROMPT = (
    "You are Micius, an embedded-agent bridge for physical devices. "
    "Your job is to coordinate general embedded device nodes through controlled tools, "
    "including Linux-capable boards, Atlas-class edge boards, ESP32-class MCU nodes, sensors, and actuators. "
    "maintain persistent device capability memory, inspect sensors, write reusable restricted DSL scripts, "
    "and choose safe actions. Treat the connected device node as an MCP-like embedded capability server: "
    "read resources before assuming hardware state, call controlled tools for actions, and use "
    "write_dsl_script/run_dsl_script for reusable behavior. Answer in concise Chinese. "
    "Do not invent tool results; if you need device data, call tools."
)


class LocalAgent:
    def __init__(self, config: Dict[str, Any], config_path: str | None = None) -> None:
        self.config = config
        self.config_path = config_path
        self.status_callback: Callable[[str, str], None] | None = None
        llm_cfg = config["llm"]
        node_cfg = get_device_node_config(config)
        self.llm = LLMClient(
            base_url=llm_cfg["base_url"],
            api_key=llm_cfg.get("api_key") or os.getenv(llm_cfg.get("api_key_env", "LLM_API_KEY")),
            timeout_sec=float(llm_cfg.get("timeout_sec", 60)),
            transport=llm_cfg.get("transport", "auto"),
            provider=llm_cfg.get("provider", "openai"),
            anthropic_version=llm_cfg.get("anthropic_version", "2023-06-01"),
        )
        self.atlas = RemoteDeviceClient(
            host=node_cfg.get("host", "127.0.0.1"),
            port=int(node_cfg.get("port", 8765)),
            timeout_sec=float(node_cfg.get("timeout_sec", 10)),
        )
        self.model = llm_cfg["model"]
        self.max_steps = int(config.get("agent", {}).get("max_steps", 6))
        self.temperature = float(llm_cfg.get("temperature", 0.2))
        self.max_tokens = config.get("agent", {}).get("max_tokens", llm_cfg.get("max_tokens"))
        self.system_prompt = config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        self.messages: List[Dict[str, Any]] = []
        self.session_id = f"session_{int(time.time() * 1000)}"
        self.context_ledger = ContextLedger()
        self.artifact_store = ArtifactStore(Path(__file__).resolve().parents[1], self.session_id)
        self._tool_call_counts: Dict[str, int] = {}
        self.self_tools = LocalSelfTools(self, config_path=config_path)
        self.remote_error: str | None = None
        self.remote_tools: List[Dict[str, Any]] = []
        self.refresh_tools()
        self.tools = self._combined_tools()
        self.board_knowledge = BoardKnowledgeBase.from_config(config)
        self.memory = MiciusMemory.from_config(config)
        self.device_context = self._load_device_context()
        self.board_context = self.board_knowledge.build_context()
        self.memory_context = self.memory.build_context()
        self.memory.log_event(
            "session.start",
            "Micius session started",
            {"session_id": self.session_id, "model": self.model},
        )
        self.reset()

    def set_status_callback(self, callback: Callable[[str, str], None] | None) -> None:
        self.status_callback = callback

    def _emit_status(self, event: str, detail: str = "") -> None:
        if self.status_callback is None:
            return
        self.status_callback(event, detail)

    def reset(self) -> None:
        self.messages = [
            {"role": "system", "content": self.system_prompt},
            {
                "role": "system",
                "content": "Available remote embedded device tools: " + ", ".join(tool["function"]["name"] for tool in self.remote_tools),
            },
            {
                "role": "system",
                "content": (
                    "Available local Micius self-management tools: "
                    + ", ".join(tool["function"]["name"] for tool in self.self_tools.schemas())
                    + ". Use micius_set_model when the user asks to switch models. "
                    "Use micius_connection_check when the user asks whether devices are connected or how to bring up a device node. "
                    "Use micius_serial_monitor to read bounded local serial logs after flashing firmware or diagnosing a board. "
                    "Use micius_dependency_install to check or install allowlisted local dependencies such as esptool. "
                    "Use micius_platformio to install/check PlatformIO and build or upload embedded firmware inside allowed project directories. "
                    "Use micius_device_research to turn hardware bring-up into a structured task with trace evidence and reusable skill curation. "
                    "Use micius_web_search for current public documentation, hardware references, release notes, or recent facts, and cite URLs from search results. "
                    "Use micius_pdf_read to extract text from allowed PDF manuals, datasheets, and papers before summarizing their contents. "
                    "Use micius_diagnostic_report when the user wants a feedback report, issue report, or open-source support bundle. "
                    "Format answers for a terminal Markdown renderer: use short paragraphs and bullets, use fenced code blocks only for commands, code, or file lists. "
                    "Use local file/config tools only inside their allowlist, keep edits scoped, and mention when a restart is needed."
                ),
            },
            {
                "role": "system",
                "content": "Embedded device context, resources, and saved scripts: " + self.device_context,
            },
            {
                "role": "system",
                "content": "Board manual skills and port map knowledge: " + self.board_context,
            },
            {
                "role": "system",
                "content": "Persistent Micius memory, user preferences, reflections, and usage signals: " + self.memory_context,
            },
        ]

    def _combined_tools(self) -> List[Dict[str, Any]]:
        return self.remote_tools + self.self_tools.schemas()

    def _available_tool_names(self) -> set[str]:
        return {
            tool["function"]["name"]
            for tool in self.tools
            if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
        }

    def refresh_tools(self) -> None:
        try:
            self.remote_tools = self.atlas.list_tools()
            self.remote_error = None
        except Exception as exc:
            self.remote_tools = []
            self.remote_error = f"{type(exc).__name__}: {exc}"
        self.tools = self._combined_tools()

    def run(self, user_prompt: str) -> str:
        self.reset()
        return self.ask(user_prompt)

    def ask(self, user_prompt: str) -> str:
        self._tool_call_counts = {}
        self.memory.log_event("user.prompt", user_prompt, {"session_id": self.session_id})
        turn_start = len(self.messages)
        tool_activity = False
        self.messages.append({"role": "user", "content": user_prompt})
        for step in range(self.max_steps):
            self._emit_status("thinking", f"step {step + 1}/{self.max_steps}")
            self.context_ledger.record_request(self.messages, self.tools)
            try:
                response = self.llm.chat_completions(
                    model=self.model,
                    messages=self.messages,
                    tools=self.tools,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    tool_choice="auto",
                )
            except Exception as exc:
                if tool_activity:
                    return self._fallback_after_llm_error(exc, turn_start)
                raise
            self.context_ledger.record_response_usage(response.get("usage") or {})
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            finish_reason = choice.get("finish_reason")
            tool_calls = message.get("tool_calls") or []
            if tool_calls:
                self._emit_status("tool_plan", f"{len(tool_calls)} tool call(s)")
                self._append_assistant_message(message)
                for tool_call in tool_calls:
                    try:
                        self._handle_tool_call(tool_call)
                        tool_activity = True
                    except Exception as exc:
                        self._append_tool_error(tool_call, exc)
                        tool_activity = True
                continue
            content = message.get("content")
            if content is None:
                content = ""
            self.messages.append({"role": "assistant", "content": content})
            self.memory.log_event("assistant.response", str(content), {"session_id": self.session_id})
            self._emit_status("final", "answer ready")
            return str(content)
        return self._finalize_after_max_steps()

    def _fallback_after_llm_error(self, exc: Exception, turn_start: int) -> str:
        self._emit_status("final", "answer ready")
        content = _summarize_turn_without_llm(self.messages[turn_start:], exc)
        self.messages.append({"role": "assistant", "content": content})
        self.memory.log_event(
            "assistant.response",
            content,
            {"session_id": self.session_id, "fallback_after_llm_error": True},
        )
        return content

    def _append_assistant_message(self, message: Dict[str, Any]) -> None:
        assistant_entry: Dict[str, Any] = {"role": "assistant", "content": message.get("content")}
        if message.get("tool_calls"):
            assistant_entry["tool_calls"] = message["tool_calls"]
        self.messages.append(assistant_entry)

    def _handle_tool_call(self, tool_call: Dict[str, Any]) -> None:
        function = tool_call.get("function") or {}
        name = function.get("name")
        raw_args = function.get("arguments") or "{}"
        if not isinstance(name, str):
            raise RuntimeError("tool call missing function name")
        if not isinstance(raw_args, str):
            raise RuntimeError("tool call arguments must be a string")
        if name not in self._available_tool_names():
            self._emit_status("tool_error", name)
            raise RuntimeError(f"model requested unavailable tool: {name}")
        arguments = _parse_tool_arguments(raw_args, name)
        if not isinstance(arguments, dict):
            raise RuntimeError("tool arguments must decode to an object")
        signature = name + ":" + json.dumps(arguments, ensure_ascii=False, sort_keys=True, default=str)
        call_count = self._tool_call_counts.get(signature, 0) + 1
        self._tool_call_counts[signature] = call_count
        if call_count > 3:
            raise RuntimeError(f"repeated tool call suppressed after {call_count - 1} identical attempts: {name}")
        self.memory.log_event(
            "tool.call",
            name,
            {"session_id": self.session_id, "arguments": _compact_json(arguments)},
        )
        self._emit_status("tool_call", name)
        result = self.call_tool(name, arguments)
        self._emit_status("tool_result", _summarize_tool_result(name, result))
        image_base64 = None
        image_mime = "image/jpeg"
        tool_result = result
        if name == "capture_camera_frame":
            tool_result = copy.deepcopy(result)
            data = tool_result.get("data")
            if isinstance(data, dict):
                image_base64 = data.pop("image_base64", None)
                image_mime = str(data.get("mime_type") or image_mime)
                if image_base64:
                    data["image_attached_to_next_message"] = True
        model_tool_result, compaction_info = compact_tool_result(
            tool_name=name,
            result=tool_result,
            artifact_store=self.artifact_store,
        )
        self.context_ledger.record_tool_compaction(compaction_info)
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": json.dumps(model_tool_result, ensure_ascii=False),
            }
        )
        self.memory.log_event(
            "tool.result",
            name,
            {
                "session_id": self.session_id,
                "result": _compact_json(model_tool_result),
                "compaction": compaction_info,
            },
        )
        if image_base64:
            self.messages.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "The previous tool call returned a camera image. Describe the image content, not only the file path.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {
                                "url": f"data:{image_mime};base64,{image_base64}",
                            },
                        },
                    ],
                }
            )
        if name in {
            "register_device_node",
            "register_peripheral",
            "record_device_note",
            "set_virtual_output",
            "execute_dsl_script",
            "write_dsl_script",
            "run_dsl_script",
        }:
            self._emit_status("context", "refreshing device context")
            self.device_context = self._load_device_context()
            self.messages.append(
                {
                    "role": "system",
                    "content": "Updated embedded device context, resources, and saved scripts: " + self.device_context,
                }
            )

    def _append_tool_error(self, tool_call: Dict[str, Any], exc: Exception) -> None:
        function = tool_call.get("function") or {}
        name = function.get("name")
        tool_name = name if isinstance(name, str) and name else "<unknown>"
        self._emit_status("tool_error", tool_name)
        payload = {
            "status": "error",
            "tool": tool_name,
            "error_type": type(exc).__name__,
            "error": str(exc),
        }
        self.messages.append(
            {
                "role": "tool",
                "tool_call_id": tool_call.get("id"),
                "content": json.dumps(payload, ensure_ascii=False),
            }
        )
        self.memory.log_event(
            "tool.error",
            tool_name,
            {"session_id": self.session_id, "error": _compact_json(payload)},
        )

    def _finalize_after_max_steps(self) -> str:
        self._emit_status("final", "answer ready")
        final_messages = self.messages + [
            {
                "role": "user",
                "content": (
                    "工具调用轮数已经达到上限。请只基于上面的工具结果，用中文简短总结："
                    "已经完成了什么、还差什么、是否需要重启或手动验证。不要再调用工具。"
                ),
            }
        ]
        try:
            self.context_ledger.record_request(final_messages, [])
            response = self.llm.chat_completions(
                model=self.model,
                messages=final_messages,
                tools=None,
                temperature=self.temperature,
                max_tokens=self.max_tokens,
            )
            self.context_ledger.record_response_usage(response.get("usage") or {})
            choice = (response.get("choices") or [{}])[0]
            message = choice.get("message") or {}
            content = str(message.get("content") or "").strip()
        except Exception as exc:
            content = (
                f"工具调用轮数已达到上限（max_steps={self.max_steps}），"
                f"且最终总结请求失败：{type(exc).__name__}: {exc}"
            )
        self.messages.append({"role": "assistant", "content": content})
        self.memory.log_event(
            "assistant.response",
            content,
            {"session_id": self.session_id, "finalized_after_max_steps": True},
        )
        return content

    def _load_device_context(self) -> str:
        tool_names = {tool["function"]["name"] for tool in self.remote_tools if "function" in tool}
        context: Dict[str, Any] = {}
        if self.remote_error:
            return json.dumps({"status": "unavailable", "error": self.remote_error}, ensure_ascii=False)
        if "get_capability_manifest" not in tool_names:
            return "{}"
        try:
            result = self.atlas.call_tool("get_capability_manifest", {"include_notes": True})
        except Exception as exc:
            return json.dumps({"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}, ensure_ascii=False)
        context["manifest"] = {
            "status": result.get("status"),
            "data": result.get("data"),
        }
        if "list_device_resources" in tool_names:
            try:
                resources = self.atlas.call_tool("list_device_resources", {})
                context["resources"] = resources.get("data", {})
            except Exception as exc:
                context["resources"] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        if "list_dsl_scripts" in tool_names:
            try:
                scripts = self.atlas.call_tool("list_dsl_scripts", {"include_script": False})
                context["scripts"] = scripts.get("data", {})
            except Exception as exc:
                context["scripts"] = {"status": "unavailable", "error": f"{type(exc).__name__}: {exc}"}
        text = json.dumps(context, ensure_ascii=False, separators=(",", ":"))
        max_chars = int(self.config.get("agent", {}).get("device_context_max_chars", 6000))
        if len(text) > max_chars:
            return text[: max_chars - 64] + "...<truncated>"
        return text

    def list_device_resources(self) -> Dict[str, Any]:
        return self.atlas.call_tool("list_device_resources", {})

    def read_device_resource(self, uri: str) -> Dict[str, Any]:
        return self.atlas.call_tool("read_device_resource", {"uri": uri})

    def list_dsl_scripts(self, include_script: bool = False) -> Dict[str, Any]:
        return self.atlas.call_tool("list_dsl_scripts", {"include_script": include_script})

    def get_dsl_script(self, name: str) -> Dict[str, Any]:
        return self.atlas.call_tool("get_dsl_script", {"name": name})

    def validate_dsl_script(self, script: str) -> Dict[str, Any]:
        return self.atlas.call_tool("validate_dsl_script", {"script": script})

    def run_dsl_script(self, name: str) -> Dict[str, Any]:
        result = self.atlas.call_tool("run_dsl_script", {"name": name})
        self.device_context = self._load_device_context()
        self.memory.record_usage("script", name, {"action": "run"})
        self.memory.log_event("script.run", name, {"session_id": self.session_id, "result": _compact_json(result)})
        return result

    def delete_dsl_script(self, name: str) -> Dict[str, Any]:
        result = self.atlas.call_tool("delete_dsl_script", {"name": name})
        self.device_context = self._load_device_context()
        self.memory.log_event("script.delete", name, {"session_id": self.session_id, "result": _compact_json(result)})
        return result

    def write_dsl_script(
        self,
        name: str,
        script: str,
        description: str = "",
        overwrite: bool = True,
    ) -> Dict[str, Any]:
        result = self.atlas.call_tool(
            "write_dsl_script",
            {
                "name": name,
                "script": script,
                "description": description,
                "overwrite": overwrite,
            },
        )
        self.device_context = self._load_device_context()
        self.memory.record_usage("script", name, {"action": "write"})
        self.memory.log_event("script.write", name + "\n" + script, {"session_id": self.session_id, "result": _compact_json(result)})
        return result

    def get_device_status(self, device_id: str = "embedded_node") -> Dict[str, Any]:
        hello = self.atlas.hello()
        status = self.atlas.call_tool("get_device_status", {"device_id": device_id})
        return {"hello": hello, "status": status}

    def connection_report(self, include_ssh: bool = False, ssh_user: str | None = None) -> Dict[str, Any]:
        return build_connection_report(self.config, include_ssh=include_ssh, ssh_user=ssh_user)

    def get_capability_manifest(self, include_notes: bool = True) -> Dict[str, Any]:
        return self.atlas.call_tool("get_capability_manifest", {"include_notes": include_notes})

    def get_device_levels(self) -> Dict[str, Any]:
        return self.atlas.call_tool("get_device_levels", {})

    def read_registered_peripheral(self, name: str) -> Dict[str, Any]:
        result = self.atlas.call_tool("read_registered_peripheral", {"name": name})
        self.memory.record_usage("peripheral", name, {"action": "read"})
        self.memory.log_event("peripheral.read", name, {"session_id": self.session_id, "result": _compact_json(result)})
        return result

    def set_virtual_output(self, name: str, value: Any) -> Dict[str, Any]:
        result = self.atlas.call_tool("set_virtual_output", {"name": name, "value": value})
        self.device_context = self._load_device_context()
        self.memory.record_usage("output", name, {"action": "set"})
        self.memory.log_event("output.set", name, {"session_id": self.session_id, "value": value, "result": _compact_json(result)})
        return result

    def record_device_note(self, title: str, body: str, scope: str = "device") -> Dict[str, Any]:
        result = self.atlas.call_tool("record_device_note", {"title": title, "body": body, "scope": scope})
        self.device_context = self._load_device_context()
        self.memory.add_fact(f"{scope}: {title} - {body}", target="memory", source="device_note")
        return result

    def call_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        if name in SELF_TOOL_NAMES:
            result = self.self_tools.call(name, arguments)
            self.memory.record_usage("self_tool", name, {"action": "call"})
            self.memory.log_event("self_tool.call", name, {"session_id": self.session_id, "arguments": arguments, "result": _compact_json(result)})
            return result
        result = self.atlas.call_tool(name, arguments)
        self.device_context = self._load_device_context()
        self.memory.record_usage("tool", name, {"action": "call"})
        self.memory.log_event("tool.manual_call", name, {"session_id": self.session_id, "arguments": arguments, "result": _compact_json(result)})
        return result

    def call_remote_tool(self, name: str, arguments: Dict[str, Any]) -> Dict[str, Any]:
        return self.call_tool(name, arguments)

    def context_status(self) -> Dict[str, Any]:
        tool_names = [
            tool["function"]["name"]
            for tool in self.tools
            if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
        ]
        message_chars = len(json.dumps(self.messages, ensure_ascii=False, default=str, separators=(",", ":")))
        return {
            "session_id": self.session_id,
            "message_count": len(self.messages),
            "message_chars": message_chars,
            "device_context_chars": len(self.device_context),
            "board_context_chars": len(self.board_context),
            "memory_context_chars": len(self.memory_context),
            "tool_count": len(tool_names),
            "ledger": self.context_ledger.snapshot(),
        }

    def cost_status(self) -> Dict[str, Any]:
        llm_cfg = self.config.get("llm", {})
        return {
            "provider": llm_cfg.get("provider", "openai"),
            "model": self.model,
            "endpoint": llm_cfg.get("base_url"),
            "note": "Token counts are local estimates unless provider usage is returned by the API.",
            "ledger": self.context_ledger.snapshot(),
        }

    def permissions_status(self) -> Dict[str, Any]:
        tool_names = [
            tool["function"]["name"]
            for tool in self.tools
            if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
        ]
        policies = tool_policy_snapshot(tool_names)
        risk_counts: Dict[str, int] = {}
        for policy in policies.values():
            risk = str(policy.get("risk") or "unknown")
            risk_counts[risk] = risk_counts.get(risk, 0) + 1
        self_cfg = self.config.get("self_management", {})
        return {
            "self_management_enabled": bool(self_cfg.get("enabled", True)),
            "allow_source_edits": bool(self_cfg.get("allow_source_edits", True)),
            "full_filesystem_access": bool(self_cfg.get("full_filesystem_access") or self_cfg.get("allow_all_files")),
            "extra_allowed_roots": self_cfg.get("extra_allowed_roots", []),
            "remote_device_online": not bool(self.remote_error),
            "remote_error": self.remote_error,
            "risk_counts": risk_counts,
            "tool_policies": policies,
        }

    def list_boards(self) -> Dict[str, Any]:
        return self.board_knowledge.list_boards()

    def list_board_manuals(self) -> Dict[str, Any]:
        return self.board_knowledge.list_manuals()

    def get_board_profile(self, board_id: str) -> Dict[str, Any]:
        self.memory.record_usage("board", board_id, {"action": "profile"})
        return self.board_knowledge.get_profile(board_id)

    def get_board_ports(self, board_id: str | None = None) -> Dict[str, Any]:
        if board_id:
            self.memory.record_usage("board", board_id, {"action": "ports"})
        return self.board_knowledge.get_ports(board_id)

    def get_board_peripherals(self, board_id: str | None = None) -> Dict[str, Any]:
        if board_id:
            self.memory.record_usage("board", board_id, {"action": "peripherals"})
        return self.board_knowledge.get_peripherals(board_id)

    def get_board_skill(self, board_id: str | None = None) -> Dict[str, Any]:
        if board_id:
            self.memory.record_usage("board", board_id, {"action": "skill"})
        return self.board_knowledge.get_skill(board_id)

    def set_active_boards(self, board_ids: List[str]) -> None:
        self.board_knowledge.set_active_boards(board_ids)
        self.config.setdefault("boards", {})["active"] = board_ids
        self.board_context = self.board_knowledge.build_context()
        self.memory.record_usage("board", ",".join(board_ids), {"action": "activate"})
        self.reset()

    def set_model(self, model: str, reset: bool = True) -> None:
        model = model.strip()
        if not model:
            raise ValueError("model name is required")
        self.model = model
        self.config["llm"]["model"] = model
        self.memory.record_usage("model", model, {"action": "set"})
        if reset:
            self.reset()

    def apply_runtime_config(self, changed_sections: List[str], reset: bool = True) -> None:
        changed = set(changed_sections)
        if "llm" in changed:
            llm_cfg = self.config["llm"]
            self.llm = LLMClient(
                base_url=llm_cfg["base_url"],
                api_key=llm_cfg.get("api_key") or os.getenv(llm_cfg.get("api_key_env", "LLM_API_KEY")),
                timeout_sec=float(llm_cfg.get("timeout_sec", 60)),
                transport=llm_cfg.get("transport", "auto"),
                provider=llm_cfg.get("provider", "openai"),
                anthropic_version=llm_cfg.get("anthropic_version", "2023-06-01"),
            )
            self.model = llm_cfg["model"]
            self.temperature = float(llm_cfg.get("temperature", 0.2))
            self.max_tokens = self.config.get("agent", {}).get("max_tokens", llm_cfg.get("max_tokens"))
        if "agent" in changed:
            self.max_steps = int(self.config.get("agent", {}).get("max_steps", 6))
            self.max_tokens = self.config.get("agent", {}).get("max_tokens", self.config.get("llm", {}).get("max_tokens"))
            self.system_prompt = self.config.get("agent", {}).get("system_prompt", DEFAULT_SYSTEM_PROMPT)
        if "atlas" in changed or "device_node" in changed:
            node_cfg = get_device_node_config(self.config)
            self.atlas = RemoteDeviceClient(
                host=node_cfg.get("host", "127.0.0.1"),
                port=int(node_cfg.get("port", 8765)),
                timeout_sec=float(node_cfg.get("timeout_sec", 10)),
            )
            self.refresh_tools()
            self.device_context = self._load_device_context()
        if "boards" in changed:
            self.board_knowledge = BoardKnowledgeBase.from_config(self.config)
            self.board_context = self.board_knowledge.build_context()
        if "memory" in changed:
            self.memory = MiciusMemory.from_config(self.config)
            self.memory_context = self.memory.build_context()
        if "self_management" in changed:
            self.tools = self._combined_tools()
        if reset:
            self.reset()

    def capture_camera_frame(
        self,
        device: str = "/dev/video0",
        width: int = 640,
        height: int = 480,
        timeout_sec: float = 12,
        include_base64: bool = True,
    ) -> Dict[str, Any]:
        result = self.atlas.call_tool(
            "capture_camera_frame",
            {
                "device": device,
                "width": width,
                "height": height,
                "timeout_sec": timeout_sec,
                "include_base64": include_base64,
            },
        )
        data = result.get("data")
        if isinstance(data, dict) and data.get("image_base64"):
            data["local_path"] = str(self._save_camera_image(data["image_base64"], data.get("mime_type", "image/jpeg")))
        self.memory.record_usage("camera", device, {"action": "capture", "width": width, "height": height})
        self.memory.log_event("camera.capture", device, {"session_id": self.session_id, "result": _compact_json(result)})
        return result

    def describe_camera_frame(
        self,
        device: str = "/dev/video0",
        width: int = 640,
        height: int = 480,
        prompt: str | None = None,
    ) -> Dict[str, Any]:
        capture = self.capture_camera_frame(device=device, width=width, height=height, include_base64=True)
        data = capture.get("data")
        if not isinstance(data, dict) or data.get("status") != "ok":
            return {"status": "error", "capture": capture, "description": "Camera capture failed; cannot describe the image."}
        image_base64 = data.get("image_base64")
        if not image_base64:
            return {"status": "error", "capture": capture, "description": "Camera tool returned no image data; cannot describe the image."}
        image_mime = str(data.get("mime_type") or "image/jpeg")
        user_prompt = prompt or "Describe the latest embedded device camera frame. Mention main objects, environment, and obvious anomalies."
        messages = [
            {"role": "system", "content": self.system_prompt},
            {"role": "system", "content": "Embedded device context, resources, and saved scripts: " + self.device_context},
            {"role": "system", "content": "Board manual skills and port map knowledge: " + self.board_context},
            {"role": "system", "content": "Persistent Micius memory, user preferences, reflections, and usage signals: " + self.memory_context},
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": user_prompt},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{image_mime};base64,{image_base64}"},
                    },
                ],
            },
        ]
        response = self.llm.chat_completions(
            model=self.model,
            messages=messages,
            tools=None,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
        )
        choice = (response.get("choices") or [{}])[0]
        message = choice.get("message") or {}
        description = str(message.get("content") or "")
        safe_capture = copy.deepcopy(capture)
        safe_data = safe_capture.get("data")
        if isinstance(safe_data, dict):
            safe_data.pop("image_base64", None)
        self.memory.log_event("camera.describe", description, {"session_id": self.session_id, "capture": _compact_json(safe_capture)})
        return {"status": "ok", "capture": safe_capture, "description": description}

    def _save_camera_image(self, image_base64: str, mime_type: str) -> Path:
        suffix = ".jpg" if mime_type == "image/jpeg" else ".img"
        output_dir = Path(__file__).resolve().parents[1] / "data" / "camera_captures"
        output_dir.mkdir(parents=True, exist_ok=True)
        output = output_dir / f"camera_{int(time.time() * 1000)}{suffix}"
        output.write_bytes(base64.b64decode(image_base64))
        return output


def load_config(path: str) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8-sig") as f:
        return json.load(f)


def _compact_json(value: Any, max_chars: int = 6000) -> Any:
    safe_value = copy.deepcopy(value)
    _strip_large_images(safe_value)
    text = json.dumps(safe_value, ensure_ascii=False, default=str, separators=(",", ":"))
    if len(text) <= max_chars:
        return safe_value
    return text[: max_chars - 64] + "...<truncated>"


def _parse_tool_arguments(raw_args: str, tool_name: str) -> Dict[str, Any]:
    candidates = [raw_args]
    stripped = raw_args.strip()
    if stripped.startswith("```"):
        lines = stripped.splitlines()
        if len(lines) >= 3:
            candidates.append("\n".join(lines[1:-1]).strip())
    if stripped and not stripped.endswith("}"):
        repaired = stripped
        repaired += "}" * max(0, stripped.count("{") - stripped.count("}"))
        candidates.append(repaired)
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(parsed, dict):
            return parsed
        raise RuntimeError(f"tool arguments for {tool_name} must decode to an object")
    try:
        json.loads(raw_args)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"invalid tool arguments for {tool_name}: {exc}") from exc
    raise RuntimeError(f"invalid tool arguments for {tool_name}")


def _summarize_turn_without_llm(turn_messages: List[Dict[str, Any]], exc: Exception) -> str:
    tool_names_by_id: Dict[str, str] = {}
    tool_summaries: List[str] = []
    for message in turn_messages:
        if message.get("role") == "assistant":
            for tool_call in message.get("tool_calls") or []:
                function = tool_call.get("function") or {}
                tool_id = str(tool_call.get("id") or "")
                name = str(function.get("name") or "tool")
                if tool_id:
                    tool_names_by_id[tool_id] = name
        if message.get("role") != "tool":
            continue
        tool_id = str(message.get("tool_call_id") or "")
        tool_name = tool_names_by_id.get(tool_id, "tool")
        payload = _loads_json_object(message.get("content"))
        tool_summaries.append(_summarize_tool_payload(tool_name, payload))
    lines = [
        "模型服务在工具执行后暂时不可用，但本轮工具结果已经返回。",
        f"最终自然语言整理失败：{type(exc).__name__}: {exc}",
    ]
    if tool_summaries:
        lines.append("")
        lines.append("已收到的工具结果：")
        lines.extend(f"- {item}" for item in tool_summaries)
    lines.append("")
    lines.append("这通常不是配置写入失败。若结果包含 restart_recommended=true，请运行 `/restart` 后继续。")
    return "\n".join(lines)


def _loads_json_object(value: Any) -> Dict[str, Any]:
    if isinstance(value, dict):
        return value
    if not isinstance(value, str):
        return {"content": str(value)}
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError:
        return {"content": value}
    return parsed if isinstance(parsed, dict) else {"content": parsed}


def _summarize_tool_payload(tool_name: str, payload: Dict[str, Any]) -> str:
    parts = [tool_name]
    for key in ("status", "error_type", "error", "changed_sections", "persisted", "config_path", "restart_recommended", "artifact_path"):
        if key in payload:
            parts.append(f"{key}={payload[key]}")
    if len(parts) == 1:
        parts.append(_short_repr(payload))
    return ", ".join(parts)


def _short_repr(value: Any, max_chars: int = 500) -> str:
    text = json.dumps(value, ensure_ascii=False, default=str)
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 32] + "...<truncated>"


def _summarize_tool_result(name: str, result: Dict[str, Any]) -> str:
    status = str(result.get("status") or "done")
    data = result.get("data")
    details = []
    if isinstance(data, dict):
        for key in ("status", "resource_count", "script_count", "path", "local_path"):
            value = data.get(key)
            if value is not None:
                details.append(f"{key}={value}")
            if len(details) >= 2:
                break
    suffix = ", ".join(details) if details else status
    return f"{name}: {suffix}"


def _strip_large_images(value: Any) -> None:
    if isinstance(value, dict):
        for key in list(value):
            if key in {"image_base64"}:
                value[key] = "<omitted>"
            else:
                _strip_large_images(value[key])
    elif isinstance(value, list):
        for item in value:
            _strip_large_images(item)


def main() -> None:
    parser = argparse.ArgumentParser(description="Micius local controller for remote embedded devices.")
    parser.add_argument("--config", required=True)
    parser.add_argument("prompt", nargs="*", help="User task prompt.")
    args = parser.parse_args()

    config = load_config(args.config)
    agent = LocalAgent(config, config_path=args.config)
    prompt = " ".join(args.prompt).strip()
    if not prompt:
        raise SystemExit("prompt is required")
    result = agent.run(prompt)
    print(result)


if __name__ == "__main__":
    main()

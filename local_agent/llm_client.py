import json
import os
import re
import shutil
import subprocess
import tempfile
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional


class LLMClient:
    def __init__(
        self,
        base_url: str,
        api_key: Optional[str],
        timeout_sec: float = 60.0,
        transport: str = "auto",
        provider: str = "openai",
        anthropic_version: str = "2023-06-01",
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self.timeout_sec = timeout_sec
        self.transport = transport
        self.provider = _normalize_provider(provider, self.base_url)
        self.anthropic_version = anthropic_version

    def chat_completions(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        if self.provider == "anthropic":
            return self._anthropic_messages(
                model=model,
                messages=messages,
                tools=tools,
                temperature=temperature,
                max_tokens=max_tokens,
                tool_choice=tool_choice,
                stream=stream,
            )
        payload: Dict[str, Any] = {
            "model": model,
            "messages": messages,
            "temperature": temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if max_tokens is not None:
            payload["max_tokens"] = max_tokens
        if tool_choice is not None:
            payload["tool_choice"] = tool_choice
        payload_bytes = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        if self.transport == "curl":
            raw = self._post_with_curl("/chat/completions", payload_bytes)
        elif self.transport == "urllib":
            raw = self._post_with_urllib("/chat/completions", payload_bytes)
        else:
            try:
                raw = self._post_with_urllib("/chat/completions", payload_bytes)
            except RuntimeError:
                raw = self._post_with_curl("/chat/completions", payload_bytes)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid LLM JSON response: {exc}") from exc

    def list_models(self) -> List[str]:
        if self.provider == "anthropic":
            raw = self._request("GET", "/models")
            try:
                payload = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise RuntimeError(f"invalid Anthropic models JSON response: {exc}") from exc
            data = payload.get("data")
            if not isinstance(data, list):
                raise RuntimeError("Anthropic models response missing data list")
            return [item["id"] for item in data if isinstance(item, dict) and isinstance(item.get("id"), str)]
        if self.transport == "curl":
            raw = self._get_with_curl("/models")
        elif self.transport == "urllib":
            raw = self._get_with_urllib("/models")
        else:
            try:
                raw = self._get_with_urllib("/models")
            except RuntimeError:
                raw = self._get_with_curl("/models")
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid LLM models JSON response: {exc}") from exc
        data = payload.get("data")
        if not isinstance(data, list):
            raise RuntimeError("models response missing data list")
        models = []
        for item in data:
            if isinstance(item, dict) and isinstance(item.get("id"), str):
                models.append(item["id"])
        return models

    def _anthropic_messages(
        self,
        model: str,
        messages: List[Dict[str, Any]],
        tools: Optional[List[Dict[str, Any]]] = None,
        temperature: float = 0.2,
        max_tokens: Optional[int] = None,
        tool_choice: Optional[str] = None,
        stream: bool = False,
    ) -> Dict[str, Any]:
        if stream:
            raise RuntimeError("Anthropic native provider does not support streaming in this client yet")
        system, anthropic_messages = _to_anthropic_messages(messages)
        payload: Dict[str, Any] = {
            "model": model,
            "messages": anthropic_messages,
            "max_tokens": int(max_tokens or 1024),
        }
        if system:
            payload["system"] = system
        if temperature is not None:
            payload["temperature"] = temperature
        anthropic_tools = _to_anthropic_tools(tools or [])
        if anthropic_tools:
            payload["tools"] = anthropic_tools
            mapped_tool_choice = _to_anthropic_tool_choice(tool_choice)
            if mapped_tool_choice is not None:
                payload["tool_choice"] = mapped_tool_choice
        raw = self._request("POST", "/messages", json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        try:
            response = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"invalid Anthropic JSON response: {exc}") from exc
        return _anthropic_to_openai_response(response)

    def _request(self, method: str, path: str, payload: bytes | None = None) -> str:
        if self.transport == "curl":
            return self._request_with_curl(method, path, payload)
        if self.transport == "urllib":
            return self._request_with_urllib(method, path, payload)
        try:
            return self._request_with_urllib(method, path, payload)
        except RuntimeError:
            return self._request_with_curl(method, path, payload)

    def _post_with_urllib(self, path: str, payload: bytes) -> str:
        return self._request_with_urllib("POST", path, payload)

    def _request_with_urllib(self, method: str, path: str, payload: bytes | None = None) -> str:
        request = urllib.request.Request(url=f"{self.base_url}{path}", data=payload, headers=self._headers(), method=method)
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_sec) as response:
                raw = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"LLM HTTP {exc.code}: {body}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"LLM connection error: {exc.reason}") from exc
        return raw

    def _get_with_urllib(self, path: str) -> str:
        return self._request_with_urllib("GET", path)

    def _post_with_curl(self, path: str, payload: bytes) -> str:
        return self._request_with_curl("POST", path, payload)

    def _get_with_curl(self, path: str) -> str:
        return self._request_with_curl("GET", path)

    def _request_with_curl(self, method: str, path: str, payload: bytes | str | None = None) -> str:
        curl = shutil.which("curl") or shutil.which("curl.exe")
        if not curl:
            raise RuntimeError("curl transport requested, but curl was not found")
        config_lines = [
            f'url = "{self.base_url}{path}"',
            f'request = "{method}"',
        ]
        for key, value in self._headers().items():
            config_lines.append(f'header = "{key}: {value}"')
        if payload is not None:
            payload_path = payload
            remove_payload = False
            if isinstance(payload, bytes):
                tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
                try:
                    tmp.write(payload)
                    payload_path = tmp.name
                    remove_payload = True
                finally:
                    tmp.close()
            config_lines.append(f'data-binary = "@{Path(str(payload_path)).as_posix()}"')
        else:
            remove_payload = False
            payload_path = None
        completed = subprocess.run(
            [curl, "--silent", "--show-error", "--fail-with-body", "--config", "-"],
            input="\n".join(config_lines) + "\n",
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self.timeout_sec,
        )
        if remove_payload and payload_path:
            try:
                os.unlink(str(payload_path))
            except OSError:
                pass
        if completed.returncode != 0:
            body = completed.stdout.strip() or completed.stderr.strip()
            raise RuntimeError(f"LLM curl error {completed.returncode}: {body}")
        return completed.stdout

    def _headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "micius/0.1",
        }
        if self.provider == "anthropic":
            headers["anthropic-version"] = self.anthropic_version
            if self.api_key:
                headers["x-api-key"] = self.api_key
        elif self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers


def _normalize_provider(provider: str, base_url: str) -> str:
    clean = (provider or "openai").strip().lower()
    if clean in {"anthropic", "claude"}:
        return "anthropic"
    if clean == "auto" and "anthropic" in base_url.lower():
        return "anthropic"
    return "openai"


def _to_anthropic_messages(messages: List[Dict[str, Any]]) -> tuple[str, List[Dict[str, Any]]]:
    system_parts: List[str] = []
    output: List[Dict[str, Any]] = []
    for message in messages:
        role = message.get("role")
        content = message.get("content")
        if role == "system":
            text = _content_to_text(content)
            if text:
                system_parts.append(text)
            continue
        if role == "tool":
            output.append(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": str(message.get("tool_call_id") or ""),
                            "content": _content_to_text(content),
                        }
                    ],
                }
            )
            continue
        if role == "assistant":
            blocks = _openai_assistant_to_anthropic_blocks(message)
            output.append({"role": "assistant", "content": blocks or [{"type": "text", "text": ""}]})
            continue
        if role == "user":
            output.append({"role": "user", "content": _openai_user_content_to_anthropic_blocks(content)})
    return "\n\n".join(system_parts), _merge_anthropic_messages(output)


def _openai_assistant_to_anthropic_blocks(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    blocks: List[Dict[str, Any]] = []
    text = _content_to_text(message.get("content")).strip()
    if text:
        blocks.append({"type": "text", "text": text})
    for tool_call in message.get("tool_calls") or []:
        function = tool_call.get("function") or {}
        raw_args = function.get("arguments") or "{}"
        try:
            arguments = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
        except json.JSONDecodeError:
            arguments = {}
        blocks.append(
            {
                "type": "tool_use",
                "id": str(tool_call.get("id") or ""),
                "name": str(function.get("name") or ""),
                "input": arguments if isinstance(arguments, dict) else {},
            }
        )
    return blocks


def _openai_user_content_to_anthropic_blocks(content: Any) -> List[Dict[str, Any]]:
    if isinstance(content, list):
        blocks: List[Dict[str, Any]] = []
        for item in content:
            if not isinstance(item, dict):
                blocks.append({"type": "text", "text": str(item)})
                continue
            if item.get("type") == "text":
                blocks.append({"type": "text", "text": str(item.get("text") or "")})
            elif item.get("type") == "image_url":
                image = _anthropic_image_block(item.get("image_url"))
                if image:
                    blocks.append(image)
                else:
                    blocks.append({"type": "text", "text": "[unsupported image_url omitted]"})
            else:
                blocks.append({"type": "text", "text": json.dumps(item, ensure_ascii=False)})
        return blocks or [{"type": "text", "text": ""}]
    return [{"type": "text", "text": _content_to_text(content)}]


def _anthropic_image_block(image_url: Any) -> Dict[str, Any] | None:
    if not isinstance(image_url, dict):
        return None
    url = str(image_url.get("url") or "")
    match = re.fullmatch(r"data:([^;,]+);base64,(.+)", url, flags=re.DOTALL)
    if not match:
        return None
    return {
        "type": "image",
        "source": {
            "type": "base64",
            "media_type": match.group(1),
            "data": match.group(2),
        },
    }


def _merge_anthropic_messages(messages: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    merged: List[Dict[str, Any]] = []
    for message in messages:
        if not merged or merged[-1]["role"] != message["role"]:
            merged.append(message)
            continue
        previous = merged[-1].setdefault("content", [])
        current = message.get("content") or []
        if not isinstance(previous, list):
            previous = [{"type": "text", "text": str(previous)}]
            merged[-1]["content"] = previous
        previous.extend(current if isinstance(current, list) else [{"type": "text", "text": str(current)}])
    return merged


def _to_anthropic_tools(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    converted = []
    for tool in tools:
        function = tool.get("function") if isinstance(tool, dict) else None
        if not isinstance(function, dict):
            continue
        name = function.get("name")
        if not isinstance(name, str) or not name:
            continue
        converted.append(
            {
                "name": name,
                "description": str(function.get("description") or ""),
                "input_schema": function.get("parameters") or {"type": "object", "properties": {}},
            }
        )
    return converted


def _to_anthropic_tool_choice(tool_choice: Any) -> Dict[str, Any] | None:
    if tool_choice in {None, "auto"}:
        return None
    if tool_choice == "none":
        return {"type": "none"}
    if tool_choice == "required":
        return {"type": "any"}
    if isinstance(tool_choice, dict):
        function = tool_choice.get("function")
        if tool_choice.get("type") == "function" and isinstance(function, dict):
            name = function.get("name")
            if isinstance(name, str) and name:
                return {"type": "tool", "name": name}
    return None


def _anthropic_to_openai_response(response: Dict[str, Any]) -> Dict[str, Any]:
    text_parts = []
    tool_calls = []
    for block in response.get("content") or []:
        if not isinstance(block, dict):
            continue
        if block.get("type") == "text":
            text_parts.append(str(block.get("text") or ""))
        elif block.get("type") == "tool_use":
            tool_calls.append(
                {
                    "id": str(block.get("id") or ""),
                    "type": "function",
                    "function": {
                        "name": str(block.get("name") or ""),
                        "arguments": json.dumps(block.get("input") or {}, ensure_ascii=False),
                    },
                }
            )
    message: Dict[str, Any] = {"role": "assistant", "content": "\n".join(part for part in text_parts if part)}
    if tool_calls:
        message["tool_calls"] = tool_calls
    stop_reason = response.get("stop_reason")
    finish_reason = "tool_calls" if tool_calls or stop_reason == "tool_use" else "stop"
    return {
        "id": response.get("id"),
        "object": "chat.completion",
        "model": response.get("model"),
        "choices": [{"index": 0, "message": message, "finish_reason": finish_reason}],
        "usage": response.get("usage") or {},
        "provider_response": {"type": response.get("type"), "stop_reason": stop_reason},
    }


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text") or ""))
            else:
                parts.append(json.dumps(item, ensure_ascii=False))
        return "\n".join(parts)
    return str(content)

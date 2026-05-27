from typing import Any, Dict


class SafetyError(ValueError):
    pass


class SafetyGuard:
    def __init__(self, safe_mode: bool = True) -> None:
        self.safe_mode = safe_mode
        self.max_string_len = 4096
        self.max_dsl_lines = 32

    def check_tool_call(self, name: str, args: Dict[str, Any]) -> None:
        if not isinstance(args, dict):
            raise SafetyError("tool arguments must be an object")
        for key, value in args.items():
            if not isinstance(key, str):
                raise SafetyError("argument keys must be strings")
            if isinstance(value, str) and len(value) > self.max_string_len:
                raise SafetyError(f"argument {key!r} is too long")
        if name in {"execute_dsl_script", "write_dsl_script", "validate_dsl_script"}:
            script = args.get("script")
            if not isinstance(script, str):
                raise SafetyError("script must be a string")
            lines = [line for line in script.splitlines() if line.strip()]
            if len(lines) > self.max_dsl_lines:
                raise SafetyError(f"DSL script exceeds {self.max_dsl_lines} non-empty lines")
        if name in {"write_dsl_script", "get_dsl_script", "run_dsl_script", "delete_dsl_script"}:
            script_name = args.get("name")
            if not isinstance(script_name, str) or not script_name.strip():
                raise SafetyError("script name is required")
            if len(script_name) > 64:
                raise SafetyError("script name is too long")
        if name == "read_device_resource":
            uri = args.get("uri")
            if not isinstance(uri, str) or not uri.startswith("micius://"):
                raise SafetyError("resource uri must start with micius://")
            if len(uri) > 256:
                raise SafetyError("resource uri is too long")
        if name == "set_virtual_output":
            value = args.get("value")
            if isinstance(value, str) and len(value) > 512:
                raise SafetyError("virtual output value is too long")

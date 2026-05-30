import argparse
import atexit
import getpass
import json
import locale
import os
import shlex
import shutil
import subprocess
import sys
import threading
import textwrap
import time
from pathlib import Path
from typing import Any, Dict, Optional
import re

from local_agent.agent import LocalAgent, load_config
from local_agent.device_connect import (
    build_connection_report,
    get_device_node_config,
    is_local_host,
    tcp_probe,
)
from local_agent.device_research import DeviceResearchLog


APP_NAME = "Micius-Agent"
PROMPT = "micius> "
SIGIL_CANVAS_WIDTH = 32
SIGIL_CANVAS_HEIGHT = 8
CONFIG_DIR = Path(__file__).resolve().parents[1] / "configs"
PROJECT_ROOT = Path(__file__).resolve().parents[1]
LOCAL_CONFIG = CONFIG_DIR / "local_agent.json"
EXAMPLE_CONFIG = CONFIG_DIR / "local_agent.example.json"
ACCENT = "\033[36m"
MUTED = "\033[90m"
WARN = "\033[33m"
GOOD = "\033[32m"
BOLD = "\033[1m"
BLUE = "\033[34m"
BRIGHT_BLUE = "\033[94m"
BRIGHT_CYAN = "\033[96m"
RESET = "\033[0m"
TERMINAL_VT_ENABLED = os.name != "nt"


class RestartRequested(Exception):
    pass

COMMAND_DESCRIPTIONS = {
    "/help [topic]": "Show focused help for a command group.",
    "/commands": "Open the full workbench command palette.",
    "/status": "Read the connected device node hello/status payload.",
    "/doctor [api]": "Run connectivity checks; add api to test model listing.",
    "/connect [status|doctor|refresh|commands|ssh]": "Diagnose and refresh the configured embedded device-node connection.",
    "/context [device|board|memory|all|refresh]": "Inspect or refresh loaded model context.",
    "/cost": "Show estimated prompt tokens, provider usage, and compaction savings.",
    "/permissions [all-files on|off|status]": "Show permissions or grant/revoke full local filesystem access.",
    "/memory [show|add|user|search|path]": "Read or update long-term project memory.",
    "/skill [list|show|use|add|search|delete]": "Manage reusable Micius workflow skills.",
    "/learn fact <text>": "Persist a stable device or project fact.",
    "/learn user <text>": "Persist a user preference.",
    "/learn skill <name> | <workflow>": "Save a reusable workflow skill.",
    "/session [recent|search]": "Search recent prompt and tool-call history.",
    "/reflect [add|list|show]": "Capture task reviews and lessons.",
    "/curator [status|run]": "Promote useful history into stable memory.",
    "/research [new|list|show|scan|pio|serial|skill|finish]": "Run traceable device bring-up with evidence and skill curation.",
    "/research new <goal>": "Create a DeviceResearch task directory with plan.md and trace.jsonl.",
    "/research scan <task_id>": "Record USB and device-node diagnostics into a DeviceResearch trace.",
    "/research pio <task_id> <op> [project] [port]": "Run PlatformIO and attach the result to the task trace.",
    "/research serial <task_id> <port> [baud] [seconds]": "Read serial evidence and attach it to the task trace.",
    "/research skill <task_id> <name>": "Distill the task trace into a reusable workflow skill.",
    "/setup": "Run the LLM provider/API setup wizard and save local config.",
    "/report [email]": "Generate a redacted diagnostic report for feedback.",
    "/refresh": "Reload tools, manifest, board context, and memory.",
    "/reset": "Clear the current conversation state.",
    "/restart": "Restart the Micius CLI process and reload source/config.",
    "/exit": "Leave the interactive CLI.",
    "/resources": "List MCP-like device resources.",
    "/resource <micius://...>": "Read one structured device resource.",
    "/manifest": "Print the persistent capability manifest.",
    "/levels": "Show configured embedded device levels.",
    "/peripheral list": "List registered sensors and actuators.",
    "/peripheral read <name>": "Read one registered peripheral.",
    "/output list": "List virtual outputs exposed by the node.",
    "/output set <name> <json-value>": "Set a controlled virtual output.",
    "/note add <title> <body>": "Record a device note into manifest and memory.",
    "/board list": "List known board profiles.",
    "/board active": "Show active board profiles.",
    "/board use <board_id>": "Use a board profile in this session.",
    "/board save <board_id>": "Persist the active board profile.",
    "/board show <board_id>": "Show the full board profile.",
    "/board ports <board_id>": "Show connector and port knowledge.",
    "/board peripherals <board_id>": "Show known board peripherals.",
    "/board skill <board_id>": "Print the board-facing skill summary.",
    "/board manuals": "List imported board manuals.",
    "/script list": "List saved restricted DSL scripts.",
    "/script show <name>": "Show a saved DSL script.",
    "/script validate <dsl>": "Validate a DSL script before saving or running.",
    "/script write <name> <dsl>": "Save a reusable restricted DSL script.",
    "/script run <name>": "Run a saved restricted DSL script.",
    "/script delete <name>": "Delete a saved DSL script.",
    "/camera capture [device] [WxH]": "Capture one camera frame from the connected device node.",
    "/camera describe [device] [WxH]": "Capture and describe one camera frame.",
    "/serial monitor <port> [baud] [seconds]": "Read local serial output for a bounded duration.",
    "/model": "Show the active provider, model, and endpoint.",
    "/model list": "List models exposed by the configured API.",
    "/model use <model>": "Switch model for this session.",
    "/model save <model>": "Switch model and write config.",
    "/self": "Show Micius self-management status.",
    "/self tools": "List local self-management tool names.",
    "/usb [all]": "Scan local USB devices and serial ports visible to Micius.",
    "/deps [check|install] <name>": "Check or install allowlisted local dependencies.",
    "/pio [check|devices|build|upload|clean] [project] [port]": "Run controlled PlatformIO operations.",
    "/web <query>": "Search the public web and return titles, snippets, and URLs.",
    "/tools": "Print raw tool schemas.",
    "/tool call <name> <json-object>": "Call one raw device or self-management tool for debugging.",
    "/config [show|path]": "Inspect the loaded config path or redacted config.",
}

STARTER_COMMANDS = [
    ("/connect status", "link", "Check the configured device node."),
    ("/doctor", "health", "Run a quick health check."),
    ("/camera describe", "vision", "Ask the connected camera what it sees."),
    ("/board ports", "ports", "Inspect the active board I/O map."),
    ("/resources", "resources", "List model-readable device resources."),
    ("/script list", "scripts", "Review saved DSL behaviors."),
    ("/research new <goal>", "research", "Start a traceable hardware workflow."),
    ("/memory search camera", "memory", "Search remembered camera notes."),
]

COMMAND_GROUPS = [
    (
        "Core",
        [
            "/help [topic]",
            "/commands",
            "/status",
            "/doctor [api]",
            "/connect [status|doctor|refresh|commands|ssh]",
            "/context [device|board|memory|all|refresh]",
            "/cost",
            "/permissions [all-files on|off|status]",
            "/memory [show|add|user|search|path]",
            "/skill [list|show|use|add|search|delete]",
            "/learn fact <text>",
            "/learn user <text>",
            "/learn skill <name> | <workflow>",
            "/session [recent|search]",
            "/reflect [add|list|show]",
            "/curator [status|run]",
            "/research [new|list|show|scan|pio|serial|skill|finish]",
            "/setup",
            "/report [email]",
            "/refresh",
            "/reset",
            "/restart",
            "/exit",
        ],
    ),
    (
        "Device",
        [
            "/resources",
            "/resource <micius://...>",
            "/manifest",
            "/levels",
            "/peripheral list",
            "/peripheral read <name>",
            "/output list",
            "/output set <name> <json-value>",
            "/note add <title> <body>",
        ],
    ),
    (
        "Board",
        [
            "/board list",
            "/board active",
            "/board use <board_id>",
            "/board save <board_id>",
            "/board show <board_id>",
            "/board ports <board_id>",
            "/board peripherals <board_id>",
            "/board skill <board_id>",
            "/board manuals",
        ],
    ),
    (
        "Scripts",
        [
            "/script list",
            "/script show <name>",
            "/script validate <dsl>",
            "/script write <name> <dsl>",
            "/script run <name>",
            "/script delete <name>",
        ],
    ),
    (
        "Perception",
        [
            "/camera capture [device] [WxH]",
            "/camera describe [device] [WxH]",
            "/serial monitor <port> [baud] [seconds]",
        ],
    ),
    (
        "Device Research",
        [
            "/research new <goal>",
            "/research list",
            "/research show <task_id>",
            "/research scan <task_id>",
            "/research pio <task_id> <op> [project] [port]",
            "/research serial <task_id> <port> [baud] [seconds]",
            "/research skill <task_id> <name>",
            "/research finish <task_id>",
        ],
    ),
    (
        "Model And Tools",
        [
            "/model",
            "/model list",
            "/model use <model>",
            "/model save <model>",
            "/self",
            "/self tools",
            "/usb [all]",
            "/deps [check|install] <name>",
            "/pio [check|devices|build|upload|clean] [project] [port]",
            "/web <query>",
            "/tools",
            "/tool call <name> <json-object>",
            "/config [show|path]",
        ],
    ),
]


def main() -> None:
    _configure_terminal_encoding()
    parser = argparse.ArgumentParser(description="Micius interactive CLI for embedded Agent devices.")
    parser.add_argument(
        "--config",
        default=os.getenv("MICIUS_CONFIG")
        or os.getenv("ATLAS_CODEX_CONFIG")
        or str(LOCAL_CONFIG if LOCAL_CONFIG.exists() else EXAMPLE_CONFIG),
    )
    parser.add_argument("--once", help="Run one prompt and exit.")
    parser.add_argument("--setup", action="store_true", help="Run the LLM API setup wizard and exit.")
    parser.add_argument(
        "--no-auto-device",
        "--no-auto-atlas",
        action="store_true",
        help="Do not auto-start the local embedded device tool server when the configured host is localhost.",
    )
    parser.add_argument("shortcut", nargs="*", help="Optional shortcut: doctor [api], demo, or setup.")
    args = parser.parse_args()

    restart_requested = False
    config = load_config(args.config)
    if args.setup:
        _run_setup_wizard(config, Path(args.config), agent=None)
        return
    if args.shortcut:
        _run_shortcut_command(args.shortcut, config, Path(args.config))
        return
    device_process = None
    if not args.no_auto_device:
        device_process = _ensure_local_device_server(config)
    try:
        agent = _create_agent(config, args.config)

        if args.once:
            _print_response(agent.ask(args.once))
            return

        _print_banner(config, agent, device_process)
        while True:
            try:
                line = _clean_input_line(_read_prompt_line(agent)).strip()
            except (EOFError, KeyboardInterrupt):
                print()
                break
            if not line:
                continue
            if line.startswith("/"):
                if _handle_command(line, config, Path(args.config), agent):
                    break
                continue
            try:
                with _ThinkingSurface(agent):
                    response = agent.ask(line)
                _print_response(response)
            except Exception as exc:
                print(f"error: {type(exc).__name__}: {exc}", file=sys.stderr)
    except RestartRequested:
        restart_requested = True
    finally:
        _stop_process(device_process)
    if restart_requested:
        _restart_current_process()


def _create_agent(config: Dict[str, Any], config_path: str | None = None) -> LocalAgent:
    try:
        return LocalAgent(config, config_path=config_path)
    except Exception as exc:
        raise SystemExit(f"failed to initialize micius: {type(exc).__name__}: {exc}") from exc


def _run_shortcut_command(parts: list[str], config: Dict[str, Any], config_path: Path) -> None:
    command = parts[0].lstrip("/").lower()
    if command == "setup":
        _run_setup_wizard(config, config_path, agent=None)
        return
    if command in {"doctor", "check", "verify"}:
        action = " ".join(parts[1:]).strip()
        agent = _create_agent(config, str(config_path))
        _handle_doctor_command("/doctor" + (f" {action}" if action else ""), config, config_path, agent)
        return
    if command in {"demo", "try"}:
        agent = _create_agent(config, str(config_path))
        _print_demo_command(config, config_path, agent)
        return
    raise SystemExit(f"unknown micius shortcut: {parts[0]}\ntry: micius demo | micius doctor | micius --setup")


def _print_demo_command(config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> None:
    llm = config.get("llm", {})
    node = get_device_node_config(config)
    tool_names = {
        tool["function"]["name"]
        for tool in agent.tools
        if isinstance(tool.get("function"), dict) and isinstance(tool["function"].get("name"), str)
    }
    local_tools = sorted(name for name in tool_names if name.startswith("micius_"))
    print("Micius demo")
    print("-----------")
    print("Local mode: OK")
    print(f"Config: {config_path}")
    print(f"Provider: {llm.get('provider', 'openai')}")
    print(f"Model: {llm.get('model', '<not configured>')}")
    print(f"Device node: {node.get('host')}:{node.get('port')}")
    if getattr(agent, "remote_error", None):
        print("Device node status: not connected; this is fine for a first no-hardware test.")
    else:
        print("Device node status: connected")
    print(f"Local tools: {len(local_tools)} available")
    print()
    print("Expected interactive startup:")
    print("  micius")
    print("  ...")
    print("  micius>")
    print()
    print("No-hardware commands to try inside Micius:")
    print("  /doctor")
    print("  /usb")
    print("  /board list")
    print("  /model")
    print("  /commands")
    print()
    print('If you see "Welcome to Codex", you opened OpenAI Codex CLI, not Micius.')


def _restart_current_process() -> None:
    sys.stdout.flush()
    sys.stderr.flush()
    os.chdir(PROJECT_ROOT)
    argv = [sys.executable, "-m", "local_agent.cli", *sys.argv[1:]]
    try:
        os.execv(sys.executable, argv)
    except OSError as exc:
        raise SystemExit(f"failed to restart Micius: {type(exc).__name__}: {exc}") from exc


def _print_banner(config: Dict[str, Any], agent: LocalAgent, device_process: Optional[subprocess.Popen[str]]) -> None:
    llm = config["llm"]
    node = get_device_node_config(config)
    width = _panel_width()
    tool_names = {tool["function"]["name"] for tool in agent.tools if "function" in tool}
    host = node.get("host", "127.0.0.1")
    port = node.get("port", 8765)
    status_items = [
        ("provider", str(llm.get("provider", "openai"))),
        ("model", str(llm["model"])),
        ("endpoint", _compact_endpoint(str(llm["base_url"]))),
        ("node", f"{host}:{port}"),
        ("tools", f"{len(tool_names)} available"),
    ]
    if device_process is not None:
        status_items.append(("node", "auto-started local tool server"))
    if getattr(agent, "remote_error", None):
        status_items.append(("remote", "device node unavailable; local self-management only"))
    capabilities = [
        ("self", "micius_self_status" in tool_names),
        ("resources", "list_device_resources" in tool_names and "read_device_resource" in tool_names),
        ("manifest", "get_capability_manifest" in tool_names),
        ("scripts", {"list_dsl_scripts", "write_dsl_script", "run_dsl_script"}.issubset(tool_names)),
        ("camera", "capture_camera_frame" in tool_names),
        ("peripherals", "read_registered_peripheral" in tool_names),
        ("memory", True),
    ]

    if _rich_terminal_home_enabled(width):
        _print_rich_terminal_home(
            width=width,
            config=config,
            agent=agent,
            status_items=status_items,
            capabilities=capabilities,
            tool_names=tool_names,
        )
        _print_banner_warnings(agent, tool_names)
        return

    print()
    print(_line_top(width))
    for line in _brand_banner_lines(width):
        print(_panel_line(line, width))
    print(_panel_line(_muted("Embedded Agent Workbench for general embedded devices"), width))
    print(_line_mid(width))
    for label, value in status_items:
        print(_panel_line(f"{_muted(label.rjust(8))}  {value}", width))
    print(_line_mid(width))
    print(_panel_line(_capability_row(capabilities), width))
    print(_line_mid(width))
    print(_panel_line(_muted("try /connect status | /commands | /camera describe | /exit"), width))
    print(_line_bottom(width))
    _print_banner_warnings(agent, tool_names)
    print()


def _print_banner_warnings(agent: LocalAgent, tool_names: set[str]) -> None:
    if "get_capability_manifest" not in tool_names and not getattr(agent, "remote_error", None):
        print(_warning("connected device node lacks persistent manifest tools; restart the remote tool server to use growth memory"))
    if "list_device_resources" not in tool_names and not getattr(agent, "remote_error", None):
        print(_warning("connected device node lacks embedded resource tools; restart the remote tool server to use MCP-like resources"))
    if getattr(agent, "remote_error", None):
        print(_warning(f"device node connection unavailable: {agent.remote_error}"))


def _rich_terminal_home_enabled(width: int) -> bool:
    if os.getenv("MICIUS_SIMPLE_BANNER"):
        return False
    return width >= 118


def _print_rich_terminal_home(
    *,
    width: int,
    config: Dict[str, Any],
    agent: LocalAgent,
    status_items: list[tuple[str, str]],
    capabilities: list[tuple[str, bool]],
    tool_names: set[str],
) -> None:
    inner = width - 4
    print()
    for index, line in enumerate(_pixel_wordmark("MICIUS-AGENT")):
        print(_wordmark_style(line, index))
    title = f"{APP_NAME} v0.1"
    right = _home_summary_lines(config, agent, status_items, tool_names)
    split = max(30, min(46, inner // 3))
    if _terminal_sigil_animation_enabled():
        frame_lines: list[str] = []
        for frame_index, frame in enumerate(_terminal_sigil_frames()):
            lines = _rich_terminal_home_panel_lines(
                width=width,
                title=title,
                left=_terminal_sigils(frame),
                right=right,
                split=split,
                capabilities=capabilities,
                tool_count=len(tool_names),
                skill_count=len(_banner_skill_names(agent)),
            )
            if frame_index:
                sys.stdout.write(f"\x1b[{len(frame_lines)}A\r")
            sys.stdout.write("\n".join(lines) + "\n")
            sys.stdout.flush()
            frame_lines = lines
            time.sleep(_terminal_sigil_frame_delay())
        return
    for line in _rich_terminal_home_panel_lines(
        width=width,
        title=title,
        left=_terminal_sigils(),
        right=right,
        split=split,
        capabilities=capabilities,
        tool_count=len(tool_names),
        skill_count=len(_banner_skill_names(agent)),
    ):
        print(line)


def _rich_terminal_home_panel_lines(
    *,
    width: int,
    title: str,
    left: list[str],
    right: list[str],
    split: int,
    capabilities: list[tuple[str, bool]],
    tool_count: int,
    skill_count: int,
) -> list[str]:
    inner = width - 4
    lines = [_titled_line_top(width, title)]
    for row in _zip_columns(left, right, split, inner - split - 3):
        lines.append(_panel_line(row, width))
    lines.append(_line_mid(width))
    lines.append(_panel_line(_capability_row(capabilities), width))
    lines.append(_panel_line(_muted(f"{tool_count} tools | {skill_count} skills | /help for commands"), width))
    lines.append(_line_bottom(width))
    return lines


def _terminal_sigil_animation_enabled() -> bool:
    if os.getenv("MICIUS_NO_MASCOT_ANIMATION"):
        return False
    if os.getenv("MICIUS_FORCE_MASCOT_ANIMATION"):
        return True
    return sys.stdout.isatty() and _unicode_enabled() and _ansi_cursor_enabled()


def _terminal_sigil_frame_delay() -> float:
    raw = os.getenv("MICIUS_MASCOT_FRAME_DELAY")
    if raw:
        try:
            return max(0.04, min(float(raw), 1.5))
        except ValueError:
            pass
    return 0.22


def _handle_command(line: str, config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> bool:
    command = line.split(maxsplit=1)[0].lower()
    if command in {"/exit", "/quit", "/q"}:
        return True
    if command in {"/help", "/h", "/?"}:
        _handle_help_command(line)
        return False
    if command in {"/commands", "/cmds"}:
        _print_command_palette()
        return False
    if command in {"/reset", "/clear"}:
        agent.reset()
        print("conversation reset")
        return False
    if command in {"/restart", "/reboot"}:
        print("restarting Micius...")
        raise RestartRequested()
    if command in {"/connect", "/node"}:
        _handle_connect_command(line, config, agent)
        return False
    if command == "/doctor":
        _handle_doctor_command(line, config, config_path, agent)
        return False
    if command in {"/context", "/ctx"}:
        _handle_context_command(line, agent)
        return False
    if command == "/cost":
        _handle_cost_command(agent)
        return False
    if command in {"/permissions", "/perms"}:
        _handle_permissions_command(line, config, config_path, agent)
        return False
    if command == "/memory":
        _handle_memory_command(line, agent)
        return False
    if command == "/skill":
        _handle_workflow_skill_command(line, agent)
        return False
    if command == "/learn":
        _handle_learn_command(line, agent)
        return False
    if command == "/session":
        _handle_session_command(line, agent)
        return False
    if command == "/reflect":
        _handle_reflect_command(line, agent)
        return False
    if command == "/curator":
        _handle_curator_command(line, agent)
        return False
    if command in {"/research", "/bringup"}:
        _handle_research_command(line, agent)
        return False
    if command == "/setup":
        _run_setup_wizard(config, config_path, agent=agent)
        return False
    if command == "/report":
        _handle_report_command(line, agent)
        return False
    if command == "/config":
        _handle_config_command(line, config, config_path)
        return False
    if command == "/tools":
        print(json.dumps(agent.tools, ensure_ascii=False, indent=2))
        return False
    if command == "/self":
        _handle_self_command(line, agent)
        return False
    if command == "/usb":
        _handle_usb_command(line, agent)
        return False
    if command in {"/deps", "/dependency", "/dependencies"}:
        _handle_dependency_command(line, agent)
        return False
    if command in {"/pio", "/platformio"}:
        _handle_platformio_command(line, agent)
        return False
    if command in {"/web", "/search"}:
        _handle_web_command(line, agent)
        return False
    if command == "/tool":
        _handle_tool_command(line, agent)
        return False
    if command in {"/model", "/models"}:
        _handle_model_command(line, config, config_path, agent)
        return False
    if command == "/camera":
        _handle_camera_command(line, agent)
        return False
    if command in {"/serial", "/uart"}:
        _handle_serial_command(line, agent)
        return False
    if command == "/board":
        _handle_board_command(line, config, config_path, agent)
        return False
    if command in {"/resources", "/resource", "/mcp", "/res"}:
        _handle_resource_command(line, agent)
        return False
    if command in {"/peripheral", "/peripherals"}:
        _handle_peripheral_command(line, agent)
        return False
    if command == "/output":
        _handle_output_command(line, agent)
        return False
    if command == "/note":
        _handle_note_command(line, agent)
        return False
    if command == "/script":
        _handle_script_command(line, agent)
        return False
    if command == "/manifest":
        try:
            manifest = agent.get_capability_manifest(include_notes=True)
            print(json.dumps(manifest, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"manifest error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    if command == "/levels":
        try:
            levels = agent.get_device_levels()
            print(json.dumps(levels, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"levels error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    if command == "/refresh":
        try:
            agent.refresh_tools()
            agent.device_context = agent._load_device_context()
            agent.board_context = agent.board_knowledge.build_context()
            agent.memory_context = agent.memory.build_context()
            agent.reset()
            print("remote tools, capability manifest, board context, and memory context refreshed")
            if getattr(agent, "remote_error", None):
                print(_warning(f"device node connection unavailable: {agent.remote_error}"))
        except Exception as exc:
            print(f"refresh error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    if command == "/status":
        try:
            status = agent.get_device_status(str(get_device_node_config(config).get("device_id") or "embedded_node"))
            print(json.dumps(status, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"status error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return False
    print(f"unknown command: {line}")
    return False


def _handle_self_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else "status"
    if action in {"status", "show"}:
        try:
            print(json.dumps(agent.call_tool("micius_self_status", {}), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"self status error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"tools", "list"}:
        tools = [
            tool["function"]["name"]
            for tool in agent.tools
            if tool.get("function", {}).get("name", "").startswith("micius_")
        ]
        print(json.dumps({"status": "ok", "tools": tools}, ensure_ascii=False, indent=2))
        return
    print("usage: /self [status|tools]")


def _handle_usb_command(line: str, agent: LocalAgent) -> None:
    parts = line.split()
    include_all = len(parts) > 1 and parts[1].strip().lower() in {"all", "full", "--all"}
    try:
        print(json.dumps(agent.call_tool("micius_usb_scan", {"include_all": include_all}), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"usb scan error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_serial_command(line: str, agent: LocalAgent) -> None:
    parts = line.split()
    if len(parts) < 2 or parts[1].lower() in {"help", "-h", "--help"}:
        print("/serial monitor COM6 115200 5")
        print("/serial COM6 115200 5")
        return
    offset = 2 if parts[1].lower() in {"monitor", "read"} else 1
    if len(parts) <= offset:
        print("usage: /serial monitor <port> [baud] [seconds]", file=sys.stderr)
        return
    args: Dict[str, Any] = {"port": parts[offset]}
    if len(parts) > offset + 1:
        args["baud"] = int(parts[offset + 1])
    if len(parts) > offset + 2:
        args["duration_sec"] = float(parts[offset + 2])
    try:
        result = agent.call_tool("micius_serial_monitor", args)
        print(json.dumps(result, ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"serial monitor error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_dependency_command(line: str, agent: LocalAgent) -> None:
    parts = line.split()
    if len(parts) < 2 or parts[1].lower() in {"help", "-h", "--help"}:
        print("/deps check esptool")
        print("/deps install esptool")
        print("/deps install platformio")
        return
    operation = "check"
    dependency = parts[1]
    if parts[1].lower() in {"check", "install"}:
        if len(parts) < 3:
            print("usage: /deps [check|install] <name>", file=sys.stderr)
            return
        operation = parts[1].lower()
        dependency = parts[2]
    try:
        print(
            json.dumps(
                agent.call_tool("micius_dependency_install", {"dependency": dependency, "operation": operation}),
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        print(f"dependency error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_platformio_command(line: str, agent: LocalAgent) -> None:
    parts = line.split()
    if len(parts) < 2 or parts[1].lower() in {"help", "-h", "--help"}:
        print("/pio check")
        print("/pio devices")
        print("/pio build local_agent/esp32_blink")
        print("/pio upload local_agent/esp32_blink COM6")
        print("/pio clean local_agent/esp32_blink")
        return
    operation = parts[1].lower()
    args: Dict[str, Any] = {"operation": operation}
    if len(parts) > 2:
        args["project_dir"] = parts[2]
    if len(parts) > 3:
        args["port"] = parts[3]
    try:
        print(json.dumps(agent.call_tool("micius_platformio", args), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"platformio error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_web_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    if len(parts) < 2 or not parts[1].strip():
        print("usage: /web <query>", file=sys.stderr)
        return
    try:
        print(
            json.dumps(
                agent.call_tool("micius_web_search", {"query": parts[1].strip(), "max_results": 5}),
                ensure_ascii=False,
                indent=2,
            )
        )
    except Exception as exc:
        print(f"web search error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_research_command(line: str, agent: LocalAgent) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(f"research parse error: {exc}", file=sys.stderr)
        return
    if len(parts) < 2 or parts[1].lower() in {"help", "-h", "--help"}:
        print("/research new <goal>")
        print("/research list")
        print("/research show <task_id>")
        print("/research scan <task_id>")
        print("/research pio <task_id> <check|devices|build|upload|clean> [project] [port]")
        print("/research serial <task_id> <port> [baud] [seconds]")
        print("/research skill <task_id> <skill_name>")
        print("/research finish <task_id>")
        return
    action = parts[1].lower()
    log = DeviceResearchLog.from_config(agent.config)
    try:
        if action == "new":
            description = " ".join(parts[2:]).strip()
            result = agent.call_tool("micius_device_research", {"operation": "create", "description": description})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if action == "list":
            result = agent.call_tool("micius_device_research", {"operation": "list"})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if len(parts) < 3:
            print("usage: /research <action> <task_id> ...", file=sys.stderr)
            return
        task_id = parts[2]
        if action == "show":
            result = agent.call_tool("micius_device_research", {"operation": "show", "task_id": task_id})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if action == "scan":
            usb = agent.call_tool("micius_usb_scan", {"include_all": False})
            usb_record = log.record_tool_result(task_id, "usb.scan", usb, stage="hardware_designer")
            connection = agent.call_tool("micius_connection_check", {"include_ssh": False})
            connection_record = log.record_tool_result(task_id, "connection.check", connection, stage="hardware_designer")
            print(
                json.dumps(
                    {
                        "status": "ok",
                        "task_id": task_id,
                        "usb": usb,
                        "connection": connection,
                        "research": [usb_record, connection_record],
                    },
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return
        if action == "pio":
            if len(parts) < 4:
                print("usage: /research pio <task_id> <check|devices|build|upload|clean> [project] [port]", file=sys.stderr)
                return
            operation = parts[3].lower()
            args: Dict[str, Any] = {"operation": operation}
            if len(parts) > 4:
                args["project_dir"] = parts[4]
            if len(parts) > 5:
                args["port"] = parts[5]
            result = agent.call_tool("micius_platformio", args)
            stage = "firmware_coder" if operation in {"build", "clean"} else "hardware_verifier"
            record = log.record_tool_result(task_id, f"platformio.{operation}", result, stage=stage)
            print(json.dumps({"status": result.get("status"), "result": result, "research": record}, ensure_ascii=False, indent=2))
            return
        if action == "serial":
            if len(parts) < 4:
                print("usage: /research serial <task_id> <port> [baud] [seconds]", file=sys.stderr)
                return
            args = {"port": parts[3]}
            if len(parts) > 4:
                args["baud"] = int(parts[4])
            if len(parts) > 5:
                args["duration_sec"] = float(parts[5])
            result = agent.call_tool("micius_serial_monitor", args)
            record = log.record_tool_result(task_id, "serial.monitor", result, stage="hardware_verifier")
            print(json.dumps({"status": result.get("status"), "result": result, "research": record}, ensure_ascii=False, indent=2))
            return
        if action == "record":
            if len(parts) < 5:
                print("usage: /research record <task_id> <kind> <summary>", file=sys.stderr)
                return
            result = agent.call_tool(
                "micius_device_research",
                {
                    "operation": "record",
                    "task_id": task_id,
                    "kind": parts[3],
                    "summary": line.split(parts[3], 1)[1].strip(),
                    "stage": "hardware_verifier",
                },
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if action == "skill":
            if len(parts) < 4:
                print("usage: /research skill <task_id> <skill_name>", file=sys.stderr)
                return
            result = agent.call_tool("micius_device_research", {"operation": "skill", "task_id": task_id, "skill_name": parts[3]})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        if action == "finish":
            result = agent.call_tool("micius_device_research", {"operation": "finish", "task_id": task_id, "status": "done"})
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        print(f"unknown research action: {action}", file=sys.stderr)
    except Exception as exc:
        print(f"research error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_help_command(line: str) -> None:
    parts = line.split(maxsplit=1)
    if len(parts) == 1:
        print("Type a task and press Enter. Use slash commands for workbench operations.")
        print("Use /commands for the full command palette.")
        print("Common: /connect status, /status, /doctor, /resources, /board ports, /peripheral list, /script list, /camera describe")
        return
    topic = parts[1].strip().lower()
    for group, commands in COMMAND_GROUPS:
        if topic == group.lower().replace(" ", "-") or any(cmd.split()[0].lstrip("/") == topic for cmd in commands):
            print(group)
            for cmd in commands:
                print(f"  {cmd}")
            return
    print(f"unknown help topic: {topic}")
    print("available topics: " + ", ".join(group.lower().replace(" ", "-") for group, _ in COMMAND_GROUPS))


def _print_command_palette() -> None:
    width = _panel_width()
    print()
    print(_line_top(width))
    print(_panel_line(_brand_title("Command Palette"), width))
    print(_panel_line(_muted("Use typed commands for routine work; raw /tool calls are for debugging."), width))
    print(_line_mid(width))
    for group, commands in COMMAND_GROUPS:
        print(_panel_line(_accent(group), width))
        for cmd in commands:
            description = COMMAND_DESCRIPTIONS.get(cmd, "")
            cmd_text = _truncate_plain(cmd, 36)
            left = f"  {cmd_text.ljust(38)}"
            available = width - 4 - _visible_len(left)
            text = _shorten(description, available)
            print(_panel_line(f"{left}{_muted(text)}", width))
        if group != COMMAND_GROUPS[-1][0]:
            print(_panel_line("", width))
    print(_line_bottom(width))
    print()


def _read_prompt_line(agent: LocalAgent) -> str:
    if not _prompt_box_enabled():
        return input(PROMPT)
    width = _panel_width()
    if _live_prompt_box_enabled():
        return _read_live_prompt_box(width)
    print()
    print(_dialogue_rule(width))
    try:
        line = input(_input_prefix())
    finally:
        if not sys.stdin.isatty():
            print()
        print(_dialogue_rule(width))
        print(_muted("/commands 查看命令 | /exit 退出"))
    return line


def _prompt_box_enabled() -> bool:
    if os.getenv("MICIUS_SIMPLE_PROMPT"):
        return False
    if os.getenv("MICIUS_FORCE_PROMPT_BOX"):
        return True
    return sys.stdin.isatty() and sys.stdout.isatty()


def _live_prompt_box_enabled() -> bool:
    if os.getenv("MICIUS_NO_LIVE_PROMPT_BOX"):
        return False
    return sys.stdin.isatty() and sys.stdout.isatty() and _ansi_cursor_enabled()


def _read_live_prompt_box(width: int) -> str:
    prefix = _input_prefix()
    print()
    print(_dialogue_rule(width))
    sys.stdout.write(prefix + "\n")
    sys.stdout.write(_dialogue_rule(width) + "\n")
    sys.stdout.write(f"\x1b[2A\r\x1b[{_visible_len(prefix)}C")
    sys.stdout.flush()
    try:
        return input()
    finally:
        sys.stdout.write("\x1b[1B\r")
        sys.stdout.flush()


def _input_box_top(width: int) -> str:
    title = " Micius prompt "
    chars = _box_chars()
    line = chars["top_left"] + chars["h"] * 2 + title
    return line + chars["h"] * max(0, width - _visible_len(line) - 1) + chars["top_right"]


def _input_prefix() -> str:
    marker = "›" if _unicode_enabled() else ">"
    return f"{_accent(marker)} "


def _prompt_box_hint(agent: LocalAgent) -> str:
    remote = "local mode" if getattr(agent, "remote_error", None) else "device node online"
    return _muted(f"model {agent.model} | {remote} | type a task, /command, or /exit")


def _print_response(text: str) -> None:
    if not _response_renderer_enabled():
        print(text)
        return
    for line in _render_response_markdown(text):
        print(line)


def _response_renderer_enabled() -> bool:
    if os.getenv("MICIUS_RAW_MARKDOWN"):
        return False
    if os.getenv("MICIUS_FORCE_MARKDOWN_RENDER"):
        return True
    return sys.stdout.isatty()


def _render_response_markdown(text: str) -> list[str]:
    width = _panel_width()
    lines = text.splitlines()
    rendered: list[str] = []
    code_lines: list[str] = []
    code_lang = ""
    in_code = False
    for line in lines:
        fence = re.match(r"^\s*```([A-Za-z0-9_.+-]*)\s*$", line)
        if fence:
            if in_code:
                rendered.extend(_render_code_block(code_lines, code_lang, width))
                code_lines = []
                code_lang = ""
                in_code = False
            else:
                in_code = True
                code_lang = fence.group(1).strip()
            continue
        if in_code:
            code_lines.append(line.rstrip("\n"))
            continue
        rendered.append(_render_markdown_line(line))
    if in_code:
        rendered.extend(_render_code_block(code_lines, code_lang, width))
    return rendered or [""]


def _render_markdown_line(line: str) -> str:
    stripped = line.strip()
    if not stripped:
        return ""
    heading = re.match(r"^(#{1,4})\s+(.+)$", stripped)
    if heading:
        return _accent(_bold(heading.group(2).strip()))
    bullet = re.match(r"^(\s*)[-*]\s+(.+)$", line)
    if bullet:
        marker = "•" if _unicode_enabled() else "-"
        return f"{bullet.group(1)}{_accent(marker)} {_render_inline_markdown(bullet.group(2))}"
    numbered = re.match(r"^(\s*)(\d+)[.)]\s+(.+)$", line)
    if numbered:
        return f"{numbered.group(1)}{_accent(numbered.group(2) + '.') } {_render_inline_markdown(numbered.group(3))}"
    return _render_inline_markdown(line)


def _render_inline_markdown(text: str) -> str:
    text = re.sub(r"\*\*([^*]+)\*\*", lambda match: _bold(match.group(1)), text)
    return re.sub(r"`([^`]+)`", lambda match: _inline_code(match.group(1)), text)


def _inline_code(text: str) -> str:
    if not _color_enabled():
        return text
    return _rgb(text, 125, 211, 252)


def _render_code_block(code_lines: list[str], lang: str, terminal_width: int) -> list[str]:
    plain_lines = code_lines or [""]
    content_width = max((_visible_len(line) for line in plain_lines), default=0)
    label = f" {lang} " if lang else " code "
    min_width = max(24, _visible_len(label) + 8)
    block_width = min(max(min_width, content_width + 4), max(44, terminal_width - 6))
    chars = _box_chars()
    inner = block_width - 4
    title = label
    title_visible = _visible_len(title)
    top = chars["top_left"] + chars["h"] * 2 + title + chars["h"] * max(0, block_width - title_visible - 4) + chars["top_right"]
    bottom = chars["bottom_left"] + chars["h"] * (block_width - 2) + chars["bottom_right"]
    rendered = [_muted(top)]
    for line in plain_lines:
        fitted = _fit_visible(line, inner)
        rendered.append(_muted(chars["v"]) + " " + _bright_cyan(fitted) + " " * max(0, inner - _visible_len(fitted)) + " " + _muted(chars["v"]))
    rendered.append(_muted(bottom))
    return rendered


def _panel_width() -> int:
    columns = shutil.get_terminal_size((150, 24)).columns
    return max(72, min(columns, 170))


def _line_top(width: int) -> str:
    chars = _box_chars()
    return chars["top_left"] + chars["h"] * (width - 2) + chars["top_right"]


def _line_mid(width: int) -> str:
    chars = _box_chars()
    return chars["mid_left"] + chars["h"] * (width - 2) + chars["mid_right"]


def _line_bottom(width: int) -> str:
    chars = _box_chars()
    return chars["bottom_left"] + chars["h"] * (width - 2) + chars["bottom_right"]


def _panel_line(text: str, width: int) -> str:
    chars = _box_chars()
    inner_width = width - 4
    visible = _visible_len(text)
    if visible > inner_width:
        text = _shorten(text, inner_width)
        visible = _visible_len(text)
    return chars["v"] + " " + text + " " * (inner_width - visible) + " " + chars["v"]


def _dialogue_rule(width: int) -> str:
    chars = _box_chars()
    return _muted(chars["h"] * width)


def _box_chars() -> Dict[str, str]:
    if not _unicode_enabled():
        return {
            "top_left": "/",
            "top_right": "\\",
            "bottom_left": "\\",
            "bottom_right": "/",
            "mid_left": "|",
            "mid_right": "|",
            "h": "-",
            "v": "|",
        }
    return {
        "top_left": "╭",
        "top_right": "╮",
        "bottom_left": "╰",
        "bottom_right": "╯",
        "mid_left": "├",
        "mid_right": "┤",
        "h": "─",
        "v": "│",
    }


def _brand_title(suffix: str | None = None) -> str:
    title = f"{_accent('[M]')} {_bold(APP_NAME)} {_muted('v0.1')}"
    if suffix:
        title += f"  {_muted('/')}  {_bold(suffix)}"
    return title


def _brand_banner_lines(width: int) -> list[str]:
    if os.getenv("MICIUS_SIMPLE_BANNER") or width < 84:
        return [_brand_title()]
    art = _pixel_wordmark("MICIUS-AGENT")
    rail = "=" * max(12, width - 6)
    return [
        _bright_blue(rail),
        *[_wordmark_style(line, index) for index, line in enumerate(art)],
        _bright_cyan(rail),
        f"{_accent('[M]')} {_bold(APP_NAME)} {_muted('v0.1')}  {_muted('jsonl tools | device nodes | sensors | scripts | memory')}",
    ]


def _pixel_wordmark(text: str) -> list[str]:
    glyphs = {
        "M": ["██   ██", "███ ███", "███████", "██ █ ██", "██   ██", "██   ██", "██   ██"],
        "I": ["███████", "  ███  ", "  ███  ", "  ███  ", "  ███  ", "  ███  ", "███████"],
        "C": [" ██████", "███    ", "██     ", "██     ", "██     ", "███    ", " ██████"],
        "U": ["██   ██", "██   ██", "██   ██", "██   ██", "██   ██", "███ ███", " █████ "],
        "S": [" ██████", "███    ", "███    ", " █████ ", "    ███", "    ███", "██████ "],
        "-": ["       ", "       ", "       ", " █████ ", "       ", "       ", "       "],
        "A": [" █████ ", "███ ███", "██   ██", "███████", "██   ██", "██   ██", "██   ██"],
        "G": [" ██████", "███    ", "██     ", "██  ███", "██   ██", "███  ██", " ██████"],
        "E": ["███████", "██     ", "██     ", "██████ ", "██     ", "██     ", "███████"],
        "N": ["██   ██", "███  ██", "████ ██", "██ ████", "██  ███", "██   ██", "██   ██"],
        "T": ["███████", "  ███  ", "  ███  ", "  ███  ", "  ███  ", "  ███  ", "  ███  "],
    }
    rows = ["", "", "", "", "", "", ""]
    for index, char in enumerate(text):
        glyph = glyphs.get(char.upper(), ["███████", "██   ██", "   ██  ", "  ██   ", "       ", "  ██   ", "  ██   "])
        gap = " " if index < len(text) - 1 else ""
        for row_index, row in enumerate(glyph):
            rows[row_index] += row + gap
    if not _unicode_enabled():
        return [row.replace("█", "#") for row in rows]
    return rows


def _wordmark_style(line: str, index: int) -> str:
    if not _color_enabled():
        return line
    gradients = [
        (125, 249, 255),
        (74, 222, 255),
        (56, 189, 248),
        (14, 165, 233),
        (37, 99, 235),
        (29, 78, 216),
        (30, 64, 175),
    ]
    main = gradients[min(index, len(gradients) - 1)]
    shadow = (10, 31, 68)
    return _colorize_shadowed_block_line(line, main, shadow)


def _colorize_shadowed_block_line(line: str, main_rgb: tuple[int, int, int], shadow_rgb: tuple[int, int, int]) -> str:
    shadow_source = "  " + line
    width = max(len(line), len(shadow_source))
    parts = []
    for index in range(width):
        char = line[index] if index < len(line) else " "
        shadow_char = shadow_source[index] if index < len(shadow_source) else " "
        if char != " ":
            parts.append(_rgb(char, *main_rgb))
        elif shadow_char != " ":
            parts.append(_rgb(".", *shadow_rgb))
        else:
            parts.append(" ")
    return "".join(parts).rstrip()


def _titled_line_top(width: int, title: str) -> str:
    chars = _box_chars()
    label = f" {title} "
    left = 18
    right = max(0, width - 2 - left - _visible_len(label))
    return chars["top_left"] + chars["h"] * left + _accent(label) + chars["h"] * right + chars["top_right"]


def _terminal_sigils(art: list[str] | None = None) -> list[str]:
    art = _normalize_terminal_sigil_frame(art or _terminal_sigil_art())
    return [
        *[_terminal_sigil_style(line, index) for index, line in enumerate(art)],
        "",
        _muted("profile"),
        f"  {_bold('Micius Slime')}",
        f"  {_muted('agent')}    {_bold(APP_NAME)}",
        f"  {_muted('session')}  {time.strftime('%Y%m%d_%H%M%S')}",
        f"  {_muted('cwd')}      {_shorten(str(PROJECT_ROOT), 28)}",
    ]


def _terminal_sigil_art() -> list[str]:
    return _terminal_sigil_frames()[0]


def _normalize_terminal_sigil_frame(frame: list[str]) -> list[str]:
    rows = list(frame[:SIGIL_CANVAS_HEIGHT])
    rows += [""] * max(0, SIGIL_CANVAS_HEIGHT - len(rows))
    frame_width = max((_visible_len(row) for row in rows), default=0)
    frame_left = max(0, (SIGIL_CANVAS_WIDTH - frame_width) // 2)
    normalized = []
    for row in rows:
        visible = _visible_len(row)
        if visible + frame_left >= SIGIL_CANVAS_WIDTH:
            normalized.append(row)
            continue
        right = SIGIL_CANVAS_WIDTH - frame_left - visible
        normalized.append(" " * frame_left + row + " " * right)
    return normalized


def _terminal_sigil_frames() -> list[list[str]]:
    if not _unicode_enabled():
        resting = [
            "                          ",
            "        ##########        ",
            "     ################     ",
            "  ######  ######  ######  ",
            "########  ######  ########",
            "##########################",
            "  ######################  ",
            "                          ",
        ]
        return [
            resting,
            [
                "                          ",
                "        ##########        ",
                "     ################     ",
                "  ######################  ",
                "##########################",
                "##########################",
                "  ######################  ",
                "                          ",
            ],
            [
                "                          ",
                "                          ",
                "    ##################    ",
                " ######################## ",
                "   ####################   ",
                "                          ",
                "                          ",
                "                          ",
            ],
            [
                "          |  |",
                "      +---+--+---+",
                "------| .######. |------",
                "------| ######## |------",
                "------|  ######  |------",
                "      +---+--+---+",
                "          |  |",
                "",
            ],
            [
                "         |  |  |",
                "   +-----+--+--+-----+",
                "----|    ##########    |----",
                "----|   ############   |----",
                "----|   ############   |----",
                "----|    ##########    |----",
                "   +-----+--+--+-----+",
                "         |  |  |",
            ],
            [
                "      |  |  |  |",
                "  +---+--+--+--+---+",
                "----|   ##########   |----",
                "----| ####  ##  #### |----",
                "----|  ############  |----",
                "----|   ##########   |----",
                "  +---+--+--+--+---+",
                "      |  |  |  |",
            ],
            [
                "      |  |  |  |",
                "  +---+--+--+--+---+",
                "----|   .########.   |----",
                "----| ##   ####   ## |----",
                "----|  ############  |----",
                "----|  ############  |----",
                "  +---+--+--+--+---+",
                "      |  |  |  |",
            ],
            [
                "        |  |  |",
                "    +---+--+--+---+",
                "---|    .####.    |---",
                "---|  ##  ##  ##  |---",
                "---|   ########   |---",
                "    +---+--+--+---+",
                "        |  |  |",
                "",
            ],
            [
                "",
                "       |  |",
                "    +--+--+--+",
                "----| .##. |----",
                "----|######|----",
                "----| #### |----",
                "    +--+--+--+",
                "       |  |",
            ],
            resting,
        ]
    resting = [
        "                          ",
        "        ██████████        ",
        "     ████████████████     ",
        "  ██████  ██████  ██████  ",
        " ███████  ██████  ███████ ",
        "  ██████████████████████  ",
        "     ████████████████     ",
        "                          ",
    ]
    return [
        resting,
        [
            "                          ",
            "        ██████████        ",
            "     ████████████████     ",
            "  ██████████████████████  ",
            "██████████████████████████",
            "██████████████████████████",
            "  ██████████████████████  ",
            "                          ",
        ],
        [
            "                          ",
            "                          ",
            "    ██████████████████    ",
            " ████████████████████████ ",
            "   ████████████████████   ",
            "                          ",
            "                          ",
            "                          ",
        ],
        [
            "          │  │",
            "      ┌───┴──┴───┐",
            "──────┤ ▗▄████▄▖ ├──────",
            "──────┤▐████████▌├──────",
            "──────┤ ▜██████▛ ├──────",
            "      └───┬──┬───┘",
            "          │  │",
            "",
        ],
        [
            "         │  │  │",
            "   ┌─────┴──┴──┴─────┐",
            "────┤    ████████    ├────",
            "────┤  ████████████  ├────",
            "────┤  ████████████  ├────",
            "────┤    ████████    ├────",
            "   └─────┬──┬──┬─────┘",
            "         │  │  │",
        ],
        [
            "      │  │  │  │",
            "  ┌───┴──┴──┴──┴───┐",
            "────┤   ██████████   ├────",
            "────┤ ██████████████ ├────",
            "────┤ ██████████████ ├────",
            "────┤   ██████████   ├────",
            "  └───┬──┬──┬──┬───┘",
            "      │  │  │  │",
        ],
        [
            "      │  │  │  │",
            "  ┌───┴──┴──┴──┴───┐",
            "────┤   ██████████   ├────",
            "────┤ ████  ██  ████ ├────",
            "────┤  ████████████  ├────",
            "────┤   ██████████   ├────",
            "  └───┬──┬──┬──┬───┘",
            "      │  │  │  │",
        ],
        [
            "        │  │  │",
            "    ┌───┴──┴──┴───┐",
            "───┤    ██████    ├───",
            "───┤  ███  ██  ███ ├───",
            "───┤   ████████   ├───",
            "    └───┬──┬──┬───┘",
            "        │  │  │",
            "",
        ],
        [
            "",
            "       │  │",
            "    ┌──┴──┴──┐",
            "────┤ ▗▄██▄▖ ├────",
            "────┤▐██████▌├────",
            "────┤ ▜████▛ ├────",
            "    └──┬──┬──┘",
            "       │  │",
        ],
        resting,
    ]


def _terminal_sigil_style(line: str, index: int) -> str:
    if not _color_enabled():
        return line
    body_palette = [
        (191, 219, 254),
        (147, 197, 253),
        (125, 211, 252),
        (96, 165, 250),
        (59, 130, 246),
        (37, 99, 235),
        (29, 78, 216),
        (30, 64, 175),
    ]
    body = body_palette[min(index, len(body_palette) - 1)]
    wire = (219, 234, 254)
    eye = (248, 250, 252)
    line_chars = set("╭╮╰╯│─┬┴├┤┌┐└┘┼╱╲═/\\|+-='.")
    parts = []
    for char in line:
        if char in {"◉", "o"}:
            parts.append(_rgb(char, *eye))
        elif char in line_chars:
            parts.append(_rgb(char, *wire))
        elif char.isspace():
            parts.append(char)
        else:
            parts.append(_rgb(char, *body))
    return "".join(parts)


def _home_summary_lines(
    config: Dict[str, Any],
    agent: LocalAgent,
    status_items: list[tuple[str, str]],
    tool_names: set[str],
) -> list[str]:
    status_map = {label: value for label, value in status_items}
    endpoint = str(status_map.get("endpoint") or _compact_endpoint(str(config.get("llm", {}).get("base_url", ""))))
    node = str(status_map.get("node") or "")
    provider = str(status_map.get("provider") or config.get("llm", {}).get("provider", "openai"))
    model = str(status_map.get("model") or getattr(agent, "model", ""))
    remote_state = "local mode" if getattr(agent, "remote_error", None) else "device node online"
    return [
        f"{_accent('Runtime')}",
        f"{_muted('provider:')} {_bold(provider)}",
        f"{_muted('model:')} {_bold(model)}",
        f"{_muted('endpoint:')} {_bold(endpoint)}",
        f"{_muted('node:')} {_bold(node)}",
        f"{_muted('state:')} {_bold(remote_state)}",
        "",
        f"{_accent('Available Tools')}",
        _toolset_line("connection", ["/connect status", "/doctor api", "/status"]),
        _toolset_line("device", _available_names(tool_names, ["get_device_status", "list_device_resources", "read_device_resource"])),
        _toolset_line("perception", _available_names(tool_names, ["capture_camera_frame", "/camera describe"])),
        _toolset_line("scripts", _available_names(tool_names, ["write_dsl_script", "run_dsl_script", "validate_dsl_script"])),
        _toolset_line("self", _available_names(tool_names, ["micius_connection_check", "micius_usb_scan", "micius_serial_monitor", "micius_platformio", "micius_diagnostic_report"])),
        "",
        f"{_accent('Available Skills')}",
        _skillset_line("boards", getattr(agent.board_knowledge, "active_boards", [])),
        _skillset_line("workflow", _banner_skill_names(agent)),
        _skillset_line("memory", ["facts", "sessions", "reflections", "curator"]),
    ]


def _toolset_line(label: str, names: list[str]) -> str:
    visible_names = names[:4]
    suffix = ", ".join(_bold(name) for name in visible_names) if visible_names else _muted("none")
    if len(names) > len(visible_names):
        suffix += _muted(", ...")
    return f"{_muted(label + ':')} {suffix}"


def _skillset_line(label: str, names: list[str]) -> str:
    visible_names = [str(name) for name in names if str(name).strip()][:5]
    suffix = ", ".join(_bold(name) for name in visible_names) if visible_names else _muted("none")
    if len(names) > len(visible_names):
        suffix += _muted(", ...")
    return f"{_muted(label + ':')} {suffix}"


def _available_names(tool_names: set[str], candidates: list[str]) -> list[str]:
    result = []
    for name in candidates:
        if name.startswith("/") or name in tool_names:
            result.append(name)
    return result


def _banner_skill_names(agent: LocalAgent) -> list[str]:
    names: list[str] = []
    try:
        result = agent.memory.list_skills(limit=8)
        for skill in result.get("skills", []):
            name = skill.get("name")
            if name:
                names.append(str(name))
    except Exception:
        return []
    return names


def _zip_columns(left: list[str], right: list[str], left_width: int, right_width: int) -> list[str]:
    rows = []
    count = max(len(left), len(right))
    for index in range(count):
        left_text = _pad_visible(left[index] if index < len(left) else "", left_width)
        right_text = _fit_visible(right[index] if index < len(right) else "", right_width)
        rows.append(left_text + _muted(" | ") + right_text)
    return rows


def _fit_visible(text: str, width: int) -> str:
    if _visible_len(text) <= width:
        return text
    return _shorten(text, width)


def _pad_visible(text: str, width: int) -> str:
    text = _fit_visible(text, width)
    return text + " " * max(0, width - _visible_len(text))


def _capability_row(capabilities: list[tuple[str, bool]]) -> str:
    parts = []
    for name, ok in capabilities:
        marker = _good("ok") if ok else _warn("missing")
        parts.append(f"{name} {marker}")
    return " | ".join(parts)


def _compact_endpoint(url: str) -> str:
    return url.replace("https://", "").replace("http://", "")


def _shorten(text: str, width: int) -> str:
    if width <= 0:
        return ""
    if _visible_len(text) <= width:
        return text
    plain = _strip_ansi(text)
    return textwrap.shorten(plain, width=width, placeholder="...")


def _truncate_plain(text: str, width: int) -> str:
    if len(text) <= width:
        return text
    if width <= 3:
        return text[:width]
    return text[: width - 3] + "..."


def _visible_len(text: str) -> int:
    return len(_strip_ansi(text))


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def _configure_terminal_encoding() -> None:
    global TERMINAL_VT_ENABLED
    if os.getenv("MICIUS_ASCII_BORDERS") or os.getenv("MICIUS_NO_UTF8_CONSOLE"):
        return
    if os.name != "nt":
        return
    if not sys.stdout.isatty() and not os.getenv("MICIUS_FORCE_UTF8_CONSOLE"):
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        enable_virtual_terminal_processing = 0x0004
        enable_processed_output = 0x0001
        for handle_id in (-11, -12):
            handle = kernel32.GetStdHandle(handle_id)
            mode = ctypes.c_uint()
            if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
                next_mode = mode.value | enable_processed_output | enable_virtual_terminal_processing
                if kernel32.SetConsoleMode(handle, next_mode):
                    TERMINAL_VT_ENABLED = True
        kernel32.SetConsoleOutputCP(65001)
        kernel32.SetConsoleCP(65001)
        for stream in (sys.stdout, sys.stderr, sys.stdin):
            reconfigure = getattr(stream, "reconfigure", None)
            if reconfigure is not None:
                reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _ansi_cursor_enabled() -> bool:
    if os.getenv("MICIUS_FORCE_LIVE_PROMPT_BOX"):
        return True
    if os.getenv("TERM") == "dumb":
        return False
    return TERMINAL_VT_ENABLED or os.getenv("WT_SESSION") is not None


def _unicode_enabled() -> bool:
    if os.getenv("MICIUS_FORCE_UNICODE"):
        return True
    if os.getenv("MICIUS_ASCII_BORDERS") or os.getenv("MICIUS_ASCII_ART"):
        return False
    encoding = (
        os.getenv("PYTHONIOENCODING")
        or getattr(sys.stdout, "encoding", None)
        or locale.getpreferredencoding(False)
        or ""
    )
    encoding = encoding.split(":", 1)[0].replace("_", "-").lower()
    return "utf" in encoding or encoding in {"cp65001", "65001"}


def _color_enabled() -> bool:
    if os.getenv("MICIUS_FORCE_COLOR"):
        return True
    if os.getenv("NO_COLOR") or os.getenv("MICIUS_NO_COLOR"):
        return False
    if os.getenv("TERM") == "dumb":
        return False
    return sys.stdout.isatty()


def _style(text: str, code: str) -> str:
    if not _color_enabled():
        return text
    return f"{code}{text}{RESET}"


def _rgb(text: str, red: int, green: int, blue: int) -> str:
    if not _color_enabled():
        return text
    return f"\033[38;2;{red};{green};{blue}m{text}{RESET}"


def _accent(text: str) -> str:
    return _style(text, ACCENT)


def _blue(text: str) -> str:
    return _style(text, BLUE)


def _bright_blue(text: str) -> str:
    return _style(text, BRIGHT_BLUE)


def _bright_cyan(text: str) -> str:
    return _style(text, BRIGHT_CYAN)


def _muted(text: str) -> str:
    return _style(text, MUTED)


def _good(text: str) -> str:
    return _style(text, GOOD)


def _warn(text: str) -> str:
    return _style(text, WARN)


def _bold(text: str) -> str:
    return _style(text, BOLD)


def _warning(text: str) -> str:
    return f"{_warn('warning')}: {text}"


class _ThinkingSurface:
    def __init__(self, agent: LocalAgent) -> None:
        self.agent = agent
        self.enabled = _thinking_enabled()
        self._current = "正在思考"
        self._stop = threading.Event()
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None
        self._last_width = 0
        self._frames = ("◆", "◇") if _unicode_enabled() else ("*", ".")

    def __enter__(self) -> "_ThinkingSurface":
        if not self.enabled:
            return self
        self.agent.set_status_callback(self.update)
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        self.agent.set_status_callback(None)
        if not self.enabled:
            return
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=0.4)
        self._clear()

    def update(self, event: str, detail: str) -> None:
        with self._lock:
            self._current = self._format_event(event, detail)

    def _run(self) -> None:
        frame_index = 0
        while not self._stop.is_set():
            with self._lock:
                message = self._current
            frame = self._frames[frame_index % len(self._frames)]
            frame_index += 1
            self._render(f"{frame} Micius :: {message}")
            self._stop.wait(0.12)

    def _render(self, message: str) -> None:
        columns = shutil.get_terminal_size((92, 24)).columns
        line = _truncate_plain(message, max(20, columns - 1))
        if _ansi_cursor_enabled():
            sys.stdout.write("\r\x1b[2K" + _muted(line))
        else:
            padding = " " * max(0, self._last_width - len(line))
            sys.stdout.write("\r" + _muted(line) + padding)
        sys.stdout.flush()
        self._last_width = len(line)

    def _clear(self) -> None:
        if _ansi_cursor_enabled():
            sys.stdout.write("\r\x1b[2K")
        else:
            sys.stdout.write("\r" + " " * self._last_width + "\r")
        sys.stdout.flush()

    @staticmethod
    def _format_event(event: str, detail: str) -> str:
        show_steps = bool(os.getenv("MICIUS_SHOW_STEPS") or os.getenv("MICIUS_DEBUG_THINKING"))
        if event == "thinking":
            if show_steps and detail:
                return f"正在思考 [{detail}]"
            return "正在思考"
        if event == "tool_plan":
            if show_steps and detail:
                return f"正在读取设备上下文 [{detail}]"
            return "正在读取设备上下文"
        if event == "tool_call":
            return f"正在调用工具 {detail}" if detail else "正在调用工具"
        if event == "tool_result":
            if show_steps and detail:
                return f"已读取工具结果 [{detail}]"
            return "正在读取设备上下文"
        if event == "tool_error":
            return f"工具不可用 {detail}" if detail else "工具不可用"
        if event == "context":
            return "正在读取设备上下文"
        if event == "final":
            return "正在整理回答"
        return detail or event


def _thinking_enabled() -> bool:
    if os.getenv("MICIUS_NO_THINKING") or os.getenv("NO_COLOR"):
        return False
    return sys.stdout.isatty()


def _handle_connect_command(line: str, config: Dict[str, Any], agent: LocalAgent) -> None:
    parts = line.split()
    action = parts[1].lower() if len(parts) > 1 else "status"
    if action in {"help", "-h", "--help"}:
        print("/connect status")
        print("/connect doctor")
        print("/connect refresh")
        print("/connect commands")
        print("/connect ssh [user]")
        return
    if action in {"status", "check", "doctor", "diag", "diagnose"}:
        report = agent.connection_report(include_ssh=action in {"doctor", "diag", "diagnose"})
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if action == "refresh":
        agent.refresh_tools()
        agent.device_context = agent._load_device_context()
        agent.board_context = agent.board_knowledge.build_context()
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        report = agent.connection_report(include_ssh=False)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    if action in {"commands", "cmd", "cmds"}:
        print(json.dumps(build_connection_report(config, include_ssh=False)["commands"], ensure_ascii=False, indent=2))
        return
    if action == "ssh":
        user = parts[2] if len(parts) > 2 else None
        report = agent.connection_report(include_ssh=True, ssh_user=user)
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return
    print("usage: /connect [status|doctor|refresh|commands|ssh [user]]")


def _handle_doctor_command(line: str, config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> None:
    action = line.split(maxsplit=1)[1].strip().lower() if len(line.split(maxsplit=1)) > 1 else "quick"
    report: Dict[str, Any] = {
        "project_root": str(PROJECT_ROOT),
        "config": str(config_path),
        "python": sys.version.split()[0],
        "model": agent.model,
        "device_node": get_device_node_config(config),
        "checks": {},
    }
    checks = report["checks"]
    connection = agent.connection_report(include_ssh=action in {"full", "ssh", "connect", "connection"})
    checks["connection"] = connection
    if connection.get("status") == "online":
        try:
            checks["device_status"] = {
                "ok": True,
                "result": agent.get_device_status(str(get_device_node_config(config).get("device_id") or "embedded_node")),
            }
        except Exception as exc:
            checks["device_status"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
        try:
            checks["resources"] = {"ok": True, "count": agent.list_device_resources().get("data", {}).get("resource_count")}
        except Exception as exc:
            checks["resources"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    else:
        checks["device_status"] = {"ok": False, "skipped": True, "reason": "configured device node is not online"}
        checks["resources"] = {"ok": False, "skipped": True, "reason": "configured device node is not online"}
    try:
        board_list = agent.list_boards()
        checks["board_knowledge"] = {
            "ok": True,
            "active_boards": board_list.get("active_boards"),
            "board_count": len(board_list.get("boards", [])),
        }
    except Exception as exc:
        checks["board_knowledge"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    if action == "api":
        try:
            checks["llm_models"] = {"ok": True, "models": agent.llm.list_models()[:20]}
        except Exception as exc:
            checks["llm_models"] = {"ok": False, "error": f"{type(exc).__name__}: {exc}"}
    print(json.dumps(report, ensure_ascii=False, indent=2))


def _handle_context_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else "summary"
    if action == "refresh":
        agent.device_context = agent._load_device_context()
        agent.board_context = agent.board_knowledge.build_context()
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print("context refreshed")
        return
    if action == "device":
        print(agent.device_context)
        return
    if action == "board":
        print(agent.board_context)
        return
    if action == "all":
        print(json.dumps({"device": agent.device_context, "board": agent.board_context, "memory": agent.memory_context}, ensure_ascii=False, indent=2))
        return
    if action == "memory":
        print(agent.memory_context)
        return
    if action in {"budget", "ledger", "tokens"}:
        print(json.dumps(agent.context_status(), ensure_ascii=False, indent=2))
        return
    print(
        json.dumps(
            {
                "messages": len(agent.messages),
                "tools": len(agent.tools),
                "device_context_chars": len(agent.device_context),
                "board_context_chars": len(agent.board_context),
                "memory_context_chars": len(agent.memory_context),
                "active_boards": agent.board_knowledge.active_boards,
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def _handle_cost_command(agent: LocalAgent) -> None:
    print(json.dumps(agent.cost_status(), ensure_ascii=False, indent=2))


def _handle_permissions_command(line: str, config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> None:
    parts = line.split()
    if len(parts) >= 2 and parts[1].lower() in {"all-files", "filesystem", "fs"}:
        action = parts[2].lower() if len(parts) >= 3 else "status"
        if action in {"status", "show"}:
            print(json.dumps(_full_filesystem_permission_status(config, config_path), ensure_ascii=False, indent=2))
            return
        if action in {"on", "enable", "enabled", "true", "yes"}:
            print(json.dumps(_set_full_filesystem_access(config, config_path, agent, True), ensure_ascii=False, indent=2))
            return
        if action in {"off", "disable", "disabled", "false", "no"}:
            print(json.dumps(_set_full_filesystem_access(config, config_path, agent, False), ensure_ascii=False, indent=2))
            return
        print("usage: /permissions all-files [on|off|status]", file=sys.stderr)
        return
    print(json.dumps(agent.permissions_status(), ensure_ascii=False, indent=2))


def _full_filesystem_permission_status(config: Dict[str, Any], config_path: Path) -> Dict[str, Any]:
    self_cfg = config.get("self_management", {})
    return {
        "status": "ok",
        "full_filesystem_access": bool(self_cfg.get("full_filesystem_access") or self_cfg.get("allow_all_files")),
        "config_path": str(config_path),
        "note": "When enabled, Micius local file tools can read/write outside the project allowlist, except blocked cache/repo internals.",
    }


def _set_full_filesystem_access(
    config: Dict[str, Any],
    config_path: Path,
    agent: LocalAgent,
    enabled: bool,
) -> Dict[str, Any]:
    self_cfg = config.setdefault("self_management", {})
    self_cfg["full_filesystem_access"] = bool(enabled)
    self_cfg.pop("allow_all_files", None)
    agent.apply_runtime_config(["self_management"], reset=False)
    _save_config(config_path, config)
    return {
        "status": "updated",
        "full_filesystem_access": bool(enabled),
        "config_path": str(config_path),
        "restart_recommended": False,
        "warning": (
            "Full filesystem access is powerful. Keep secrets redacted when sharing logs."
            if enabled
            else "Micius is back to the configured self-management allowlist."
        ),
    }


def _handle_memory_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "show"
    if action in {"help", "-h", "--help"}:
        print("/memory show")
        print("/memory user")
        print("/memory add <text>")
        print("/memory user-add <text>")
        print("/memory search <query>")
        print("/memory path")
        return
    if action in {"show", "all"}:
        print(json.dumps(agent.memory.read("all"), ensure_ascii=False, indent=2))
        return
    if action == "user":
        print(json.dumps(agent.memory.read("user"), ensure_ascii=False, indent=2))
        return
    if action == "path":
        print(str(agent.memory.root))
        return
    if action in {"add", "fact"}:
        if len(parts) < 3:
            print("usage: /memory add <text>", file=sys.stderr)
            return
        result = agent.memory.add_fact(parts[2], target="memory", source="cli")
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if action in {"user-add", "preference"}:
        if len(parts) < 3:
            print("usage: /memory user-add <text>", file=sys.stderr)
            return
        result = agent.memory.add_fact(parts[2], target="user", source="cli")
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if action == "search":
        if len(parts) < 3:
            print("usage: /memory search <query>", file=sys.stderr)
            return
        print(json.dumps(agent.memory.search_events(parts[2]), ensure_ascii=False, indent=2))
        return
    print("usage: /memory [show|user|add <text>|user-add <text>|search <query>|path]")


def _handle_workflow_skill_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "list"
    if action in {"help", "-h", "--help"}:
        print("/skill list")
        print("/skill show <name>")
        print("/skill use <name>")
        print("/skill search <query>")
        print("/skill add <name> | <workflow markdown>")
        print("/skill delete <name>")
        return
    if action in {"list", "ls"}:
        print(json.dumps(agent.memory.list_skills(), ensure_ascii=False, indent=2))
        return
    if action in {"show", "cat"}:
        if len(parts) < 3:
            print("usage: /skill show <name>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.memory.read_skill(parts[2].strip()), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"skill show error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action == "use":
        if len(parts) < 3:
            print("usage: /skill use <name>", file=sys.stderr)
            return
        try:
            result = agent.memory.use_skill(parts[2].strip())
            agent.memory_context = agent.memory.build_context()
            agent.reset()
            print(result["content"])
        except Exception as exc:
            print(f"skill use error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action == "search":
        if len(parts) < 3:
            print("usage: /skill search <query>", file=sys.stderr)
            return
        print(json.dumps(agent.memory.search_skills(parts[2]), ensure_ascii=False, indent=2))
        return
    if action in {"add", "save"}:
        if len(parts) < 3:
            print("usage: /skill add <name> | <workflow markdown>", file=sys.stderr)
            return
        try:
            name, body = _split_name_body(parts[2])
            result = agent.memory.add_skill(name, body)
            agent.memory_context = agent.memory.build_context()
            agent.reset()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"skill add error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"delete", "rm", "remove"}:
        if len(parts) < 3:
            print("usage: /skill delete <name>", file=sys.stderr)
            return
        try:
            result = agent.memory.delete_skill(parts[2].strip())
            agent.memory_context = agent.memory.build_context()
            agent.reset()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"skill delete error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /skill [list|show <name>|use <name>|search <query>|add <name> | <workflow>|delete <name>]")


def _handle_learn_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print("/learn fact <text>")
        print("/learn user <text>")
        print("/learn skill <name> | <workflow markdown>")
        print("/learn reflection <title> | <body>")
        return
    if len(parts) < 3:
        print(f"usage: /learn {action} <text>", file=sys.stderr)
        return
    if action in {"fact", "memory"}:
        result = agent.memory.add_fact(parts[2], target="memory", source="learn")
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if action in {"user", "preference"}:
        result = agent.memory.add_fact(parts[2], target="user", source="learn")
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if action in {"reflection", "reflect"}:
        title, body = _split_title_body(parts[2])
        result = agent.memory.add_reflection(title, body, tags=["learn"])
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    if action == "skill":
        try:
            name, body = _split_name_body(parts[2])
            result = agent.memory.add_skill(name, body, tags=["learn"])
            agent.memory_context = agent.memory.build_context()
            agent.reset()
            print(json.dumps(result, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"learn skill error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /learn [fact|user|skill|reflection] <text>")


def _handle_session_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "recent"
    if action in {"help", "-h", "--help"}:
        print("/session recent [limit]")
        print("/session search <query>")
        return
    if action == "recent":
        limit = 20
        if len(parts) > 2:
            try:
                limit = int(parts[2])
            except ValueError:
                print("limit must be an integer", file=sys.stderr)
                return
        print(json.dumps(agent.memory.recent_events(limit=limit), ensure_ascii=False, indent=2))
        return
    if action == "search":
        if len(parts) < 3:
            print("usage: /session search <query>", file=sys.stderr)
            return
        print(json.dumps(agent.memory.search_events(parts[2]), ensure_ascii=False, indent=2))
        return
    print("usage: /session [recent [limit]|search <query>]")


def _handle_reflect_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "list"
    if action in {"help", "-h", "--help"}:
        print("/reflect list")
        print('/reflect add "<title>" "<body>"')
        print("/reflect show <name-or-path>")
        return
    if action in {"list", "ls"}:
        print(json.dumps(agent.memory.list_reflections(), ensure_ascii=False, indent=2))
        return
    if action == "show":
        if len(parts) < 3:
            print("usage: /reflect show <name-or-path>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.memory.read_reflection(parts[2]), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"reflect show error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action == "add":
        try:
            parsed = shlex.split(line)
        except ValueError as exc:
            print(f"reflect parse error: {exc}", file=sys.stderr)
            return
        if len(parsed) < 4:
            print('/reflect add "<title>" "<body>"', file=sys.stderr)
            return
        result = agent.memory.add_reflection(parsed[2], parsed[3], tags=parsed[4:])
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("usage: /reflect [list|add|show]")


def _handle_curator_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    action = parts[1].strip().lower() if len(parts) > 1 else "status"
    if action in {"help", "-h", "--help"}:
        print("/curator status")
        print("/curator run")
        return
    if action == "status":
        print(json.dumps(agent.memory.curator_status(), ensure_ascii=False, indent=2))
        return
    if action == "run":
        result = agent.memory.curator_run()
        agent.memory_context = agent.memory.build_context()
        agent.reset()
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print("usage: /curator [status|run]")


def _handle_report_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    contact_email = parts[1].strip() if len(parts) > 1 else ""
    try:
        result = agent.call_tool("micius_diagnostic_report", {"contact_email": contact_email, "write_file": True})
        path = result.get("path")
        summary = result.get("summary", {})
        print(json.dumps({"status": result.get("status"), "path": path, "summary": summary}, ensure_ascii=False, indent=2))
        if path:
            print(f"report written: {path}")
        if result.get("contact_email"):
            print(f"feedback email: {result['contact_email']}")
    except Exception as exc:
        print(f"report error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _run_setup_wizard(config: Dict[str, Any], config_path: Path, agent: LocalAgent | None = None) -> None:
    target_path = _setup_target_config_path(config_path)
    llm = config.setdefault("llm", {})
    print()
    print(_accent("Micius LLM Setup"))
    print(_muted("Press Enter to keep the current value. API key input is hidden."))
    print()

    current_provider = _normalize_provider_name(str(llm.get("provider") or "openai"))
    provider = _prompt_provider(current_provider)

    default_base_url = "https://api.anthropic.com/v1" if provider == "anthropic" else "https://api.openai.com/v1"
    saved_base_url = str(llm.get("base_url") or "")
    current_base_url = saved_base_url if saved_base_url and provider == current_provider else default_base_url
    base_url = _prompt_with_default("API base URL", current_base_url)
    default_model = "claude-sonnet-4-5" if provider == "anthropic" else "gpt-5.4-mini"
    saved_model = str(llm.get("model") or "")
    current_model = saved_model if saved_model and provider == current_provider else default_model
    model = _prompt_with_default("Model name", current_model)
    current_key = str(llm.get("api_key") or "")
    current_key_note = _redact_secret_for_display(current_key) if current_key else "not set"
    key_prompt = f"API key [{current_key_note}; hidden, Enter keeps current]: "
    api_key = _prompt_secret(key_prompt).strip()
    if not api_key:
        api_key = current_key

    llm["provider"] = provider
    llm["base_url"] = base_url.rstrip("/")
    llm["model"] = model.strip()
    if provider == "anthropic":
        current_version = str(llm.get("anthropic_version") or "2023-06-01")
        llm["anthropic_version"] = _prompt_with_default("Anthropic API version", current_version)
    if api_key:
        llm["api_key"] = api_key
    llm.setdefault("api_key_env", "LLM_API_KEY")
    llm.setdefault("timeout_sec", 60)
    llm.setdefault("transport", "auto")
    llm.setdefault("temperature", 0.2)
    llm.setdefault("max_tokens", 512)

    _save_config(target_path, config)
    if agent is not None:
        agent.config_path = str(target_path)
        agent.self_tools.config_path = target_path.resolve()
        agent.apply_runtime_config(["llm"], reset=True)
    print()
    print(f"config saved: {target_path}")
    print(f"provider: {llm['provider']}")
    print(f"endpoint: {llm['base_url']}")
    print(f"model: {llm['model']}")
    print(f"api_key: {_redact_secret_for_display(api_key) if api_key else '<not set>'}")

    if _prompt_yes_no("Test model list now?", default=True):
        _test_llm_config(config)
    print(_muted("Use /restart after setup if this session was started from the example config."))


def _setup_target_config_path(config_path: Path) -> Path:
    try:
        resolved = config_path.resolve()
    except OSError:
        resolved = config_path
    if resolved.name == EXAMPLE_CONFIG.name:
        return LOCAL_CONFIG
    return resolved


def _prompt_with_default(label: str, default: str) -> str:
    value = _clean_input_line(input(f"{label} [{default}]: ")).strip()
    return value or default


def _prompt_provider(default: str) -> str:
    value = _clean_input_line(input(f"Provider [openai/anthropic] [{default}]: ")).strip()
    return _normalize_provider_name(value or default)


def _normalize_provider_name(value: str) -> str:
    provider = (value or "openai").strip().lower()
    if provider in {"anthropic", "claude"}:
        return "anthropic"
    if provider in {"openai", "openai-compatible", "compatible", "api"}:
        return "openai"
    print(_warning(f"unknown provider '{value}', using openai-compatible mode"))
    return "openai"


def _prompt_yes_no(label: str, default: bool = True) -> bool:
    suffix = "Y/n" if default else "y/N"
    value = _clean_input_line(input(f"{label} [{suffix}]: ")).strip().lower()
    if not value:
        return default
    return value in {"y", "yes", "1", "true", "是", "好"}


def _prompt_secret(prompt: str) -> str:
    if sys.stdin.isatty() and sys.stdout.isatty():
        return _clean_input_line(getpass.getpass(prompt))
    return _clean_input_line(input(prompt))


def _test_llm_config(config: Dict[str, Any]) -> None:
    try:
        from local_agent.llm_client import LLMClient

        llm = config["llm"]
        client = LLMClient(
            base_url=llm["base_url"],
            api_key=llm.get("api_key") or os.getenv(llm.get("api_key_env", "LLM_API_KEY")),
            timeout_sec=float(llm.get("timeout_sec", 60)),
            transport=llm.get("transport", "auto"),
            provider=llm.get("provider", "openai"),
            anthropic_version=llm.get("anthropic_version", "2023-06-01"),
        )
        models = client.list_models()
        print(_good(f"model list ok: {len(models)} model(s)"))
        for name in models[:10]:
            marker = "*" if name == llm.get("model") else " "
            print(f"{marker} {name}")
    except Exception as exc:
        print(f"{_warn('model list failed')}: {type(exc).__name__}: {exc}")


def _handle_config_command(line: str, config: Dict[str, Any], config_path: Path) -> None:
    action = line.split(maxsplit=1)[1].strip().lower() if len(line.split(maxsplit=1)) > 1 else "show"
    if action == "path":
        print(str(config_path))
        return
    if action in {"show", "current"}:
        print(json.dumps(_redact_config(config), ensure_ascii=False, indent=2))
        return
    print("usage: /config [show|path]")


def _handle_tool_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=3)
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print("/tool call <name> <json-object>")
        print("example: /tool call read_registered_peripheral {\"name\":\"front_distance\"}")
        return
    if action == "call":
        if len(parts) < 4:
            print("usage: /tool call <name> <json-object>", file=sys.stderr)
            return
        try:
            args = json.loads(parts[3])
            if not isinstance(args, dict):
                raise ValueError("json-object must decode to an object")
            print(json.dumps(agent.call_remote_tool(parts[2], args), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"tool call error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /tool call <name> <json-object>")


def _handle_resource_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=1)
    if parts[0].lower() in {"/resources", "/mcp", "/res"} and len(parts) == 1:
        try:
            print(json.dumps(agent.list_device_resources(), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"resources error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if len(parts) < 2:
        print("usage: /resource <micius://...>", file=sys.stderr)
        return
    try:
        print(json.dumps(agent.read_device_resource(parts[1].strip()), ensure_ascii=False, indent=2))
    except Exception as exc:
        print(f"resource error: {type(exc).__name__}: {exc}", file=sys.stderr)


def _handle_peripheral_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print("/peripheral list")
        print("/peripheral read <name>")
        return
    if action in {"list", "ls"}:
        try:
            manifest = agent.get_capability_manifest(include_notes=False)
            peripherals = manifest.get("data", {}).get("manifest", {}).get("peripherals", {})
            print(json.dumps({"peripherals": peripherals}, ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"peripheral list error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"read", "get"}:
        if len(parts) < 3:
            print("usage: /peripheral read <name>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.read_registered_peripheral(parts[2].strip()), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"peripheral read error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /peripheral [list|read <name>]")


def _handle_output_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=3)
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print("/output list")
        print("/output set <name> <json-value>")
        return
    if action in {"list", "ls"}:
        try:
            print(json.dumps(agent.read_device_resource("micius://device/outputs"), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"output list error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action == "set":
        if len(parts) < 4:
            print("usage: /output set <name> <json-value>", file=sys.stderr)
            return
        try:
            value = _parse_json_scalar(parts[3])
            print(json.dumps(agent.set_virtual_output(parts[2], value), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"output set error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /output [list|set <name> <json-value>]")


def _handle_note_command(line: str, agent: LocalAgent) -> None:
    try:
        parts = shlex.split(line)
    except ValueError as exc:
        print(f"note parse error: {exc}", file=sys.stderr)
        return
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print('/note add "<title>" "<body>" [scope]')
        return
    if action == "add":
        if len(parts) < 4:
            print('/note add "<title>" "<body>" [scope]', file=sys.stderr)
            return
        scope = parts[4] if len(parts) > 4 else "device"
        try:
            print(json.dumps(agent.record_device_note(parts[2], parts[3], scope), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"note add error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print('usage: /note add "<title>" "<body>" [scope]')


def _handle_board_command(line: str, config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=2)
    action = parts[1].lower() if len(parts) > 1 else "help"
    board_id = parts[2].strip() if len(parts) > 2 else None
    if action in {"help", "-h", "--help"}:
        print("/board list")
        print("/board active")
        print("/board use <board_id>")
        print("/board save <board_id>")
        print("/board show <board_id>")
        print("/board ports <board_id>")
        print("/board peripherals <board_id>")
        print("/board skill <board_id>")
        print("/board manuals")
        return
    if action in {"list", "ls"}:
        print(json.dumps(agent.list_boards(), ensure_ascii=False, indent=2))
        return
    if action == "manuals":
        print(json.dumps(agent.list_board_manuals(), ensure_ascii=False, indent=2))
        return
    if action == "active":
        print(json.dumps({"active_boards": agent.board_knowledge.active_boards}, ensure_ascii=False, indent=2))
        return
    if action in {"use", "save"}:
        if not board_id:
            print(f"usage: /board {action} <board_id>", file=sys.stderr)
            return
        try:
            agent.set_active_boards([board_id])
        except Exception as exc:
            print(f"board {action} error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        if action == "save":
            config.setdefault("boards", {})["active"] = [board_id]
            _save_config(config_path, config)
            print(f"active board saved: {board_id}")
        else:
            print(f"active board for current session: {board_id}")
        return
    if action in {"show", "profile"}:
        try:
            print(json.dumps(agent.get_board_profile(board_id or agent.board_knowledge.active_boards[0]), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"board show error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"ports", "port"}:
        try:
            print(json.dumps(agent.get_board_ports(board_id), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"board ports error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"peripherals", "peripheral"}:
        try:
            print(json.dumps(agent.get_board_peripherals(board_id), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"board peripherals error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action == "skill":
        try:
            result = agent.get_board_skill(board_id)
            print(result["content"])
        except Exception as exc:
            print(f"board skill error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /board [list|active|use <board_id>|save <board_id>|show <board_id>|ports <board_id>|peripherals <board_id>|skill <board_id>|manuals]")


def _handle_script_command(line: str, agent: LocalAgent) -> None:
    parts = line.split(maxsplit=3)
    action = parts[1].lower() if len(parts) > 1 else "help"
    if action in {"help", "-h", "--help"}:
        print("/script list")
        print("/script show <name>")
        print("/script validate <dsl>")
        print("/script run <name>")
        print("/script write <name> <dsl>")
        print("/script delete <name>")
        print("example:")
        print("/script write avoid_front_obstacle READ_SENSOR front_distance AS d; IF d < 0.35 THEN SET action=stop ELSE SET action=go")
        return
    if action in {"list", "ls"}:
        try:
            print(json.dumps(agent.list_dsl_scripts(include_script=False), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script list error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"show", "cat", "get"}:
        if len(parts) < 3:
            print("usage: /script show <name>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.get_dsl_script(parts[2]), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script show error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"validate", "check"}:
        if len(parts) < 3:
            print("usage: /script validate <dsl>", file=sys.stderr)
            return
        script = _normalize_inline_dsl(" ".join(parts[2:]) if len(parts) > 3 else parts[2])
        try:
            print(json.dumps(agent.validate_dsl_script(script), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script validate error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"run", "exec"}:
        if len(parts) < 3:
            print("usage: /script run <name>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.run_dsl_script(parts[2]), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script run error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"write", "save"}:
        if len(parts) < 4:
            print("usage: /script write <name> <dsl>", file=sys.stderr)
            return
        script = _normalize_inline_dsl(parts[3])
        try:
            print(json.dumps(agent.write_dsl_script(parts[2], script), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script write error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    if action in {"delete", "rm", "remove"}:
        if len(parts) < 3:
            print("usage: /script delete <name>", file=sys.stderr)
            return
        try:
            print(json.dumps(agent.delete_dsl_script(parts[2]), ensure_ascii=False, indent=2))
        except Exception as exc:
            print(f"script delete error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return
    print("usage: /script [list|show <name>|validate <dsl>|run <name>|write <name> <dsl>|delete <name>]")


def _normalize_inline_dsl(text: str) -> str:
    text = text.replace("\\n", "\n").strip()
    if "\n" not in text and ";" in text:
        text = "\n".join(part.strip() for part in text.split(";") if part.strip())
    return text


def _parse_json_scalar(text: str) -> Any:
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


def _split_title_body(text: str) -> tuple[str, str]:
    if "|" in text:
        title, body = text.split("|", 1)
        return title.strip(), body.strip()
    return text.strip()[:80] or "Reflection", text.strip()


def _split_name_body(text: str) -> tuple[str, str]:
    if "|" not in text:
        raise ValueError("expected '<name> | <body>'")
    name, body = text.split("|", 1)
    return name.strip(), body.strip()


def _redact_config(config: Dict[str, Any]) -> Dict[str, Any]:
    redacted = json.loads(json.dumps(config))
    llm = redacted.get("llm")
    if isinstance(llm, dict) and llm.get("api_key"):
        llm["api_key"] = _redact_secret_for_display(str(llm["api_key"]))
    return redacted


def _redact_secret_for_display(value: str) -> str:
    if len(value) > 12:
        return value[:6] + "..." + value[-4:]
    return "<redacted>"


def _handle_model_command(line: str, config: Dict[str, Any], config_path: Path, agent: LocalAgent) -> None:
    parts = line.split()
    if parts[0].lower() == "/models":
        parts = ["/model", "list"]
    action = parts[1].lower() if len(parts) > 1 else "show"
    if action in {"show", "current"}:
        llm = config["llm"]
        print(
            json.dumps(
                {
                    "provider": llm.get("provider", "openai"),
                    "model": agent.model,
                    "base_url": llm["base_url"],
                    "anthropic_version": llm.get("anthropic_version") if llm.get("provider") == "anthropic" else None,
                    "config": str(config_path),
                },
                ensure_ascii=False,
                indent=2,
            )
        )
        return
    if action in {"list", "ls"}:
        try:
            models = agent.llm.list_models()
        except Exception as exc:
            print(f"model list error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        for model in models:
            marker = "*" if model == agent.model else " "
            print(f"{marker} {model}")
        return
    if action in {"use", "switch", "set", "save"}:
        if len(parts) < 3:
            print(f"usage: /model {action} <model>", file=sys.stderr)
            return
        model = parts[2]
        try:
            agent.set_model(model)
        except Exception as exc:
            print(f"model switch error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        if action == "save":
            _save_config(config_path, config)
            print(f"model switched and saved: {model}")
        else:
            print(f"model switched for current session: {model}")
        return
    print("usage: /model [show|list|use <model>|save <model>]")


def _handle_camera_command(line: str, agent: LocalAgent) -> None:
    parts = line.split()
    if len(parts) == 1 or parts[1].lower() in {"help", "-h", "--help"}:
        print("/camera capture [device] [WxH]")
        print("/camera describe [device] [WxH]")
        print("examples:")
        print("/camera capture /dev/video0 640x480")
        print("/camera describe /dev/video0 640x480")
        return
    action = parts[1].lower()
    device, width, height = _parse_camera_args(parts[2:])
    if action in {"capture", "cap", "shot"}:
        try:
            result = agent.capture_camera_frame(device=device, width=width, height=height, include_base64=True)
        except Exception as exc:
            print(f"camera capture error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        data = result.get("data") if isinstance(result, dict) else None
        if not isinstance(data, dict):
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return
        data = dict(data)
        data.pop("image_base64", None)
        print(json.dumps(data, ensure_ascii=False, indent=2))
        return
    if action in {"describe", "desc", "look"}:
        try:
            result = agent.describe_camera_frame(device=device, width=width, height=height)
        except Exception as exc:
            print(f"camera describe error: {type(exc).__name__}: {exc}", file=sys.stderr)
            return
        capture = result.get("capture", {})
        data = capture.get("data") if isinstance(capture, dict) else {}
        if isinstance(data, dict):
            print(f"remote: {data.get('path')}")
            print(f"local: {data.get('local_path')}")
        print(result.get("description", ""))
        return
    print("usage: /camera [capture|describe] [device] [WxH]")


def _parse_camera_args(args: list[str]) -> tuple[str, int, int]:
    device = "/dev/video0"
    width = 640
    height = 480
    for arg in args:
        if arg.startswith("/dev/video"):
            device = arg
            continue
        match = re.fullmatch(r"(\d+)x(\d+)", arg.lower())
        if match:
            width = int(match.group(1))
            height = int(match.group(2))
            continue
    return device, width, height


def _save_config(path: Path, config: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)
        f.write("\n")


def _clean_input_line(line: str) -> str:
    prefix_noise = {0xFEFF, 0x00EF, 0x00BB, 0x00BF, 0x010F, 0x0165, 0x017C, 0x9518}
    cleaned = "".join(ch for ch in line if ch != "\x00" and not 0xDC80 <= ord(ch) <= 0xDCFF)
    while cleaned and ord(cleaned[0]) in prefix_noise:
        cleaned = cleaned[1:]
    slash = cleaned.find("/")
    if slash > 0 and all(ord(ch) in prefix_noise for ch in cleaned[:slash]):
        cleaned = cleaned[slash:]
    return cleaned


def _ensure_local_device_server(config: Dict[str, Any]) -> Optional[subprocess.Popen[str]]:
    node = get_device_node_config(config)
    host = str(node.get("host", "127.0.0.1"))
    port = int(node.get("port", 8765))
    if tcp_probe(host, port, timeout_sec=0.25).get("ok"):
        return None
    if not is_local_host(host):
        return None

    project_root = Path(__file__).resolve().parents[1]
    device_id = str(node.get("device_id") or "embedded_node")
    command = [
        sys.executable,
        "-m",
        "micius_device_node.server",
        "--host",
        host,
        "--port",
        str(port),
        "--device-id",
        device_id,
        "--manifest",
        str(project_root / "data" / "device_manifest.json"),
    ]
    creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    process = subprocess.Popen(
        command,
        cwd=str(project_root),
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        text=True,
        creationflags=creationflags,
    )
    atexit.register(_stop_process, process)
    deadline = time.time() + 8.0
    while time.time() < deadline:
        if process.poll() is not None:
            raise SystemExit("failed to auto-start local embedded device tool server")
        if tcp_probe(host, port, timeout_sec=0.25).get("ok"):
            return process
        time.sleep(0.1)
    _stop_process(process)
    raise SystemExit(f"local embedded device tool server did not open {host}:{port}")


def _stop_process(process: Optional[subprocess.Popen[str]]) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=3)


if __name__ == "__main__":
    main()

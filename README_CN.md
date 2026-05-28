# Micius-Agent

[![License: Apache-2.0](https://img.shields.io/badge/License-Apache--2.0-blue.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-blue.svg)](https://www.python.org/)
[![Status](https://img.shields.io/badge/status-prototype-orange.svg)](#项目状态)

语言：[English](README.md) | 中文

Micius-Agent 是一个面向嵌入式开发的终端 Agent 工作台。它支持 OpenAI 兼容 API 和原生 Anthropic Claude，并连接本地开发工具、串口设备、摄像头、ESP32 类开发板、Linux 边缘板和轻量级设备节点。

**Micius** 对应中文里的 **墨子**。墨子与逻辑、工程、光学和实践技艺相关，这也对应本项目希望把大模型能力连接到真实硬件和工程实践中的方向。

Micius-Agent 的设计思路是把主 Agent 保留在本地电脑上，再让连接的硬件通过受控工具和轻量设备节点暴露能力。这样即使开发板本身无法运行完整 coding agent，也可以参与到 Agent 工作流中。

## 核心特性

- **灵活模型配置**：支持 OpenAI 兼容 API，也支持原生 Anthropic Claude Messages API。
- **终端优先体验**：通过 `micius` 启动，可以使用自然语言或斜杠命令。
- **本地硬件工具**：支持 USB 扫描、串口监视、受限依赖安装、PlatformIO 编译和上传。
- **设备节点桥接**：Linux 类开发板可以通过轻量 JSONL TCP 工具服务器接入。
- **DeviceResearch 轨迹**：把硬件调试任务记录为 `task.json`、`plan.md` 和 `trace.jsonl`。
- **持久化技能和板卡知识**：保存可复用工作流、端口映射、手册摘要和设备经验。
- **保守工具边界**：默认不开放无限制 shell 执行。

## 项目状态

Micius-Agent 目前仍是早期原型。API、命令名、文件结构和硬件工作流在稳定版本前可能继续变化。

当前重点：

- 本地 CLI 体验
- ESP32 与 PlatformIO 工作流
- Linux 嵌入式设备节点
- 板卡知识和技能沉淀
- 可追踪的硬件接入流程

## 安装

要求：

- Python 3.10+
- 支持 UTF-8 的终端
- OpenAI 兼容 API endpoint，或 Anthropic Claude API key
- ESP32 工作流可选依赖：`pyserial`、`esptool`、`platformio`

```bash
git clone https://github.com/Dryoung95/micius.git
cd micius
python -m pip install -e .
```

配置模型：

```bash
micius --setup
```

也可以使用命令式快捷入口：

```bash
micius setup
```

配置向导支持：

- `provider: "openai"`：用于 OpenAI 兼容服务，例如 OpenAI、DeepSeek 兼容网关或其他 `/v1/chat/completions` endpoint。
- `provider: "anthropic"`：用于原生 Claude，通过 Anthropic `/v1/messages` API 接入，并支持 Claude tool use。

原生 Claude 配置示例：

```json
{
  "llm": {
    "provider": "anthropic",
    "base_url": "https://api.anthropic.com/v1",
    "model": "claude-sonnet-4-5",
    "api_key_env": "ANTHROPIC_API_KEY",
    "anthropic_version": "2023-06-01"
  }
}
```

启动 CLI：

```bash
micius
```

正常启动后应该看到类似：

```text
Micius-Agent v0.1
Embedded Agent Workbench for general embedded devices
micius>
```

如果你看到的是 `Welcome to Codex, OpenAI's command-line coding agent`，说明启动的是 OpenAI Codex CLI，不是 Micius。请回到本仓库目录后运行 `micius` 或 `python -m local_agent.cli`。

也可以复制 `configs/local_agent.example.json` 为 `configs/local_agent.json` 后手动编辑。不要把 `configs/local_agent.json` 提交到 git。

## 无硬件检查

即使还没有连接开发板，Micius 也应该可以完成基础自检：

```bash
micius demo
micius doctor
```

`micius demo` 用来确认 CLI、配置、本地工具和无硬件路径已经安装成功。`micius doctor` 会输出 JSON 诊断报告。如果还想测试模型 endpoint，可以运行 `micius doctor api`。

## 快速开始

在 Micius 终端中输入：

```text
/usb
/deps install platformio
/pio devices
/pio build local_agent/esp32_blink
/pio upload local_agent/esp32_blink COM6
/serial monitor COM6 115200 5
```

请把 `COM6` 替换成 `/usb` 或 `/pio devices` 显示的实际端口。

## 常用命令

| 命令 | 用途 |
|---|---|
| `micius demo` | 运行无硬件安装演示。 |
| `micius doctor [api]` | 运行非交互式本地诊断。 |
| `/setup` | 配置 provider、API URL、模型名和 API key。 |
| `/model` | 查看当前 provider、模型和 endpoint。 |
| `/model list` | 列出当前 API 暴露的模型。 |
| `/usb` | 扫描 USB 设备和串口。 |
| `/serial monitor <port> [baud] [seconds]` | 限时读取串口输出。 |
| `/deps install platformio` | 安装允许列表中的本地依赖。 |
| `/pio devices` | 列出 PlatformIO 可见设备。 |
| `/pio build <project>` | 编译 PlatformIO 工程。 |
| `/pio upload <project> <port>` | 使用 PlatformIO 上传固件。 |
| `/research new <goal>` | 创建可追踪硬件工作流。 |
| `/research scan <task_id>` | 记录 USB 和设备节点证据。 |
| `/research skill <task_id> <name>` | 将任务轨迹沉淀为可复用技能。 |
| `/report [email]` | 生成已脱敏诊断报告。 |
| `/restart` | 重启 CLI 并重新加载源码和配置。 |

在 Micius 中运行 `/commands` 可以查看完整命令面板。

## 模型配置模板

Provider 示例位于 `configs/providers/`：

| 模板 | 用途 |
|---|---|
| `openai-compatible.example.json` | OpenAI 兼容的 `/v1/chat/completions` 网关。 |
| `anthropic-claude.example.json` | 原生 Anthropic Claude `/v1/messages` API。 |
| `deepseek-compatible.example.json` | DeepSeek 的 OpenAI 兼容 API。 |

可以把需要的 `llm` 片段复制到 `configs/local_agent.json`，也可以直接运行 `micius --setup`。

## DeviceResearch

DeviceResearch 会把硬件接入过程变成可恢复的工作流：

```text
/research new bring up an ESP32 board and verify serial output
/research scan <task_id>
/research pio <task_id> build local_agent/esp32_blink
/research pio <task_id> upload local_agent/esp32_blink COM6
/research serial <task_id> COM6 115200 5
/research skill <task_id> esp32_blink_bringup
```

每个任务会写入：

```text
data/device_research/<task_id>/
|- task.json
|- plan.md
\- trace.jsonl
```

设计说明见 [docs/DeviceResearch.md](docs/DeviceResearch.md)。

## 设备节点

对于 Linux 类开发板，可以在开发板上运行轻量设备节点服务器：

```bash
python -m micius_device_node.server --host 0.0.0.0 --port 8765
```

然后配置 `configs/local_agent.json`，让 `device_node.host` 指向开发板 IP，再运行：

```text
/connect doctor
/resources
/peripheral list
/script list
```

包中仍保留 `atlas_agent` 这个历史模块名，因为最早的原型面向 Atlas 类硬件。新的文档和自动生成命令会使用通用的 `micius_device_node` 模块，以及公开命令 `micius-device-node`。

## 板卡知识

板卡知识位于 `board_knowledge/`：

| 目录 | 用途 |
|---|---|
| `boards/` | 结构化板卡 profile、端口别名和外设事实。 |
| `skills/` | 面向 Agent 的简明板卡技能。 |
| `manuals/` | 导入的手册摘要。 |
| `templates/` | 板卡 profile 模板。 |
| `schemas/` | profile 校验 JSON schema。 |

目标是形成长期设备记忆：已连接外设、端口名、安全操作、可复用脚本，以及过往硬件调试经验。

## 项目结构

```text
local_agent/        CLI、模型客户端、本地工具、记忆、DeviceResearch
micius_device_node/ 通用嵌入式设备节点入口
atlas_agent/        为兼容保留的历史实现
shared/             JSONL RPC 协议辅助代码
board_knowledge/    板卡 profiles、手册摘要和技能
configs/            示例本地配置
docs/               设计文档
micius_memory/      运行时记忆模板和工作流技能存储
data/               运行时报告和轨迹，除 .gitkeep 外默认忽略
```

## 安全模型

Micius-Agent 会把高风险操作放在显式工具边界之后：

- API key 存在本地配置或环境变量中，并被 git 忽略。
- 依赖安装使用允许列表。
- PlatformIO 操作限制在带 `platformio.ini` 的工程目录中。
- 串口监视有读取时长和字节数限制。
- DeviceResearch 先记录证据，再声明硬件任务成功。
- 运行时报告会在写入支持包前脱敏常见秘密。

## 路线图

- 扩展 ESP32 和 MCU 模板
- 更安全的固件生成工作流
- 板卡手册导入和 profile 校验
- 常见 Linux 开发板的设备节点安装器
- 从重复硬件工作流中沉淀更强的技能
- 增加包元数据和示例工程 CI 检查

## 社区与反馈

Micius-Agent 正在寻找早期试用者和贡献者。

欢迎你：

- 使用自己的开发板和外设尝试 Micius-Agent
- 提交 issue，附带硬件接入日志或使用体验反馈
- 提交 PR，改进工具、板卡 profile、示例、文档和 bug
- 提出 Logo、吉祥物、终端 UI 或品牌视觉设计方案

反馈 bug 时，建议先运行 `/report` 并附上已脱敏输出。

反馈、合作或 Logo 设计方案可以联系：

```text
3241347200@qq.com
```

提交 PR 前建议运行：

```bash
python -m py_compile local_agent/agent.py local_agent/cli.py local_agent/self_tools.py local_agent/device_research.py
```

## 许可证

Micius-Agent 使用 [Apache License 2.0](LICENSE)。

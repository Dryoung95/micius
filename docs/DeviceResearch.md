# DeviceResearch

DeviceResearch is Micius-Agent's structured workflow layer for physical-device work.

It uses a structured loop: construct the task, design a strategy, generate code or scripts, verify correctness with physical evidence, profile behavior, and distill reusable skills.

## Why This Exists

Raw chat is too weak for hardware bring-up. A useful embedded agent must remember:

- what the user asked for
- which board, port, project, and toolchain were involved
- what it scanned, compiled, uploaded, and observed
- which failures occurred and which fixes worked
- what should become a reusable workflow skill

DeviceResearch turns each bring-up into a small, resumable task directory.

## Stage Model

| Stage | Role |
|---|---|
| `task_constructor` | Convert user intent into a structured hardware task. |
| `hardware_designer` | Identify device class, ports, protocols, and safety constraints. |
| `firmware_coder` | Create or edit firmware, scripts, or device-node resources. |
| `hardware_verifier` | Compile, upload, read serial/device evidence, and compare behavior. |
| `profiler` | Record stability, latency, data quality, or throughput metrics. |
| `skill_curator` | Distill reusable board, port, error-fix, and workflow knowledge. |

The verifier is physical evidence: USB scans, firmware builds, uploads, serial logs, camera frames, sensor readings, and device-node resources.

## CLI

Create a task:

```text
/research new bring up an ESP32 board and verify serial output
```

Attach environment evidence:

```text
/research scan <task_id>
```

Run PlatformIO and attach results:

```text
/research pio <task_id> build local_agent/esp32_blink
/research pio <task_id> upload local_agent/esp32_blink COM6
```

Read serial evidence:

```text
/research serial <task_id> COM6 115200 5
```

Distill a reusable skill:

```text
/research skill <task_id> esp32_blink_bringup
```

## Files

Each task lives under:

```text
data/device_research/<task_id>/
├─ task.json
├─ plan.md
└─ trace.jsonl
```

`task.json` is the structured state. `plan.md` is the human-readable recovery point. `trace.jsonl` is the append-only evidence stream.

## Agent Tool

The same workflow is exposed to the model as `micius_device_research`, so the agent can create tasks, record observations, finish tasks, and curate skills without relying only on slash commands.

## Design Rules

- Do not claim success without verifier evidence.
- Treat port names as observations, not stable facts, until re-scanned.
- Keep generated skills general enough to transfer to similar boards.
- Preserve raw tool outputs in the trace, but redact secrets before writing.
- Use `plan.md` as the recovery point after context loss or terminal restart.

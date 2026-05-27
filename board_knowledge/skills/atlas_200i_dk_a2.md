# Atlas 200I DK A2 Board Skill

Use this skill when Micius needs board-specific hardware context for the
connected Atlas 200I DK A2 node.

## Current Coverage

The official board manual has not been imported into `board_knowledge/manuals`
yet. The entries below are verified from the currently connected runtime and
must be expanded with manual-backed connector labels, pin names, voltage limits,
and current limits before hardware-critical control.

## Runtime Access Template

- SSH: `<board-ip>:22`
- Micius remote tool server: `<board-ip>:8765`
- Micius device id: `atlas_200i`
- Remote manifest path: `~/micius-agent/data/atlas_manifest.json`

## Observed Ports

- `ethernet_lan`: wired LAN connection from the PC to Atlas. Use it for SSH and
  JSONL RPC to the Micius remote tool server.
- `usb_host_camera`: USB host connection for a UVC camera. The primary Linux
  video node is `/dev/video0`; `/dev/video1` is also present.
- `manifest_mock`: software-backed manifest source used for safe sensor tests
  before real GPIO/I2C/SPI/CAN/serial wiring is recorded.

## Observed Peripherals

- `usb_camera0`: camera on `usb_host_camera`, protocol `camera/v4l2`, primary
  path `/dev/video0`, accessed through `capture_camera_frame`.
- `front_distance`: mock distance sensor in the Micius manifest, unit `m`,
  accessed through `read_registered_peripheral`. Current safety threshold used
  for demos: `0.35`.

## Skill Rules

- Prefer exact `port` and `os_path` names from the board profile when writing
  DSL scripts or registering peripherals.
- If a port, connector label, voltage, current limit, pin mode, bus id, or
  alternate function is missing, say it is not recorded yet and ask to import or
  extract the manual section.
- Do not infer hardware limits from similar boards.
- For real sensors, register the peripheral in the Atlas manifest with the same
  `name`, `port`, protocol, unit, and safety limits used in the board profile.

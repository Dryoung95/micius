# Raw Board Manuals

Place original board manuals here before extraction.

Recommended naming:

```text
atlas_200i_dk_a2_user_manual.pdf
atlas_200i_dk_a2_hardware_reference.md
esp32_devkitc_v4_pinout.pdf
```

After adding a manual, extract the stable facts into:

```text
board_knowledge/boards/<board_id>.json
board_knowledge/skills/<board_id>.md
```

Keep raw manuals as source material. Keep the board profile and skill files short,
structured, and explicit about ports, OS device names, connector labels, voltage
limits, and safety notes.

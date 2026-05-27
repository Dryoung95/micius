# ESP32 Blink

Minimal PlatformIO example for checking an ESP32-class board from Micius.

The default LED pin is `GPIO2`; change it in `platformio.ini` if your board uses a different LED pin.

```bash
pio run
pio run --target upload --upload-port COM6
```

Inside Micius, the same flow can be run with:

```text
/usb
/deps install platformio
/pio build local_agent/esp32_blink
/pio upload local_agent/esp32_blink COM6
/serial monitor COM6 115200 5
```

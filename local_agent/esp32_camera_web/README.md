# ESP32-S3 Camera Web

PlatformIO example for an ESP32-S3 camera board with OV2640-style pin mapping.

Before uploading, edit `src/main.cpp` and replace:

```text
YOUR_WIFI_SSID
YOUR_WIFI_PASSWORD
```

Build and upload from Micius:

```text
/deps install platformio
/pio build local_agent/esp32_camera_web
/pio upload local_agent/esp32_camera_web COM6
/serial monitor COM6 115200 10
```

Use the serial output to find the camera web URL. Replace `COM6` with your board port.

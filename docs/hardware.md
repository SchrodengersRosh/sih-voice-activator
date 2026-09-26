# ESP32-S3 Hardware & Firmware Guide (M4)

## Target Platform & Requirements
- **Microcontroller**: ESP32-S3 (Xtensa dual-core LX7, 240 MHz, 512 KB SRAM + optional PSRAM)
- **Toolchain**: ESP-IDF v5.1+ (recommended v5.2 / v5.3)
- **Microphone**: I2S Digital MEMS Microphone (e.g. INMP441, ICS-43434, SPH0645)

## Pinout & Wiring

| Microphone Pin | ESP32-S3 GPIO | Description |
|---|---|---|
| **SCK / BCLK** | **GPIO 4** | I2S Bit Clock |
| **WS / LRCLK** | **GPIO 5** | I2S Word Select |
| **SD / DOUT** | **GPIO 6** | I2S Serial Data In |
| **VDD** | **3.3V** | Power |
| **GND** | **GND** | Ground |
| **L/R** | **GND** | Left channel select |

*Note on 24-bit in 32-bit slot*: Many I2S microphones (like INMP441) transmit 24-bit data inside 32-bit frames. The driver (`firmware/main/i2s_mic.c`) automatically converts and scales samples to canonical 16 kHz signed 16-bit mono little-endian PCM.

## Wi-Fi Power Constraint
Per `DEC-ESP32-009`, Wi-Fi power saving is disabled during initialization using:
```c
esp_wifi_set_ps(WIFI_PS_NONE);
```
This avoids periodic power-save sleep latencies that degrade streaming performance.

## Build, Flash & Monitor

### 1. Set Target
```sh
cd firmware
idf.py set-target esp32s3
```

### 2. Configure (Credentials & Backend Address)
```sh
idf.py menuconfig
```
Navigate to **SIH Voice Activator Configuration**:
- **Wi-Fi SSID & Password**: Network credentials
- **Backend WebSocket Server URI**: `ws://<server-ip>:8765`
- **Device ID**: Identifier string (e.g. `esp32s3-voice-01`)
- **I2S Microphone GPIOs**: Defaults to SCK: 4, WS: 5, DIN: 6
- **Energy Gate Threshold**: Default `-50` dBFS (DEC-INFER-011)
- **Pre-buffer Duration**: `800` ms (40 frames)
- **Stream Duration**: `3000` ms (live audio stream after wake-word)

### 3. Build Firmware
```sh
idf.py build
```

### 4. Flash to ESP32-S3
```sh
idf.py -p /dev/ttyUSB0 flash
```
*(Replace `/dev/ttyUSB0` with your serial port, e.g. `/dev/cu.usbmodem*` on macOS).*

### 5. Monitor Output
```sh
idf.py -p /dev/ttyUSB0 monitor
```

## Host-Side Verification
If physical ESP32 hardware is not connected, the core C logic (protocol framing, circular pre-buffer, energy gating, and INT8 KWS temporal smoothing) can be compiled and verified natively on the host:
```sh
pytest tests/test_m4_firmware.py
```
Or directly compiling the native C test harness:
```sh
clang -Wall -Wextra -I firmware/main firmware/main/protocol_frames.c firmware/main/audio_ring_buffer.c firmware/main/energy_detector.c firmware/main/kws_engine.c firmware/tests/test_firmware_native.c -o /tmp/test_firmware_native && /tmp/test_firmware_native
```

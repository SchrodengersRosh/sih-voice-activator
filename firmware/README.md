# ESP32-S3 Edge Voice Activator Firmware

Edge firmware for SIH 26172 (M4).

## Architecture
- **I2S Microphone Input**: 16 kHz, 16-bit mono signed LE PCM (`i2s_mic.c`).
- **Circular Pre-Buffer**: 800 ms (40 frames, 25,600 bytes) ring buffer (`audio_ring_buffer.c`).
- **Energy Gating**: Gated inference below -50 dBFS to minimize power consumption (`energy_detector.c`).
- **INT8 KWS Engine**: Spectral feature extraction + INT8 inference + temporal moving average smoothing (`kws_engine.c`).
- **Protocol Framing**: Frozen v1.0 binary header generation (`protocol_frames.c`).
- **Wi-Fi & WebSocket**: Persistent WebSocket client with `WIFI_PS_NONE` power mode (`wifi_connect.c`, `ws_client.c`).

## Build Instructions
Requirements: ESP-IDF v5.1+

```sh
idf.py set-target esp32s3
idf.py menuconfig
idf.py build
idf.py -p <PORT> flash monitor
```

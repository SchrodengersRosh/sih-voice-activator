# Roadmap

**CURRENT MILESTONE: M4**

- **M1 (completed):** validate PCM WAV round trip and frozen transport. (298ceda)
- **M2 (completed):** simulated ESP32 client with synthetic PCM audio, acceptance tests for PCM transport layer. (febc0c5)
- **M3 (completed):** streaming ASR + telemetry
- **M4 (completed):** ESP32-S3 I2S microphone input, local KWS, pre-buffer + live PCM streaming over frozen v1.0 protocol.
**Definition of Done:**
- Complete ESP32-S3 firmware project (ESP-IDF v5.1+) targeting Xtensa dual-core
- Continuous I2S audio capture: 16 kHz mono signed 16-bit LE PCM with 24-in-32bit slot conversion
- Low-power energy gating detector (> -50 dBFS) per DEC-INFER-011
- Local INT8 quantized KWS with temporal moving average smoothing per DEC-KWS-010
- 800 ms (40 frames / 25,600 bytes) circular pre-buffer preservation and upload upon trigger
- Live PCM streaming with continuous sequence and sample offsets
- Native host-side C unit tests and Python end-to-end integration tests pass
- **M5:** 100+ trial latency harness with p50/p95/p99.
- **M6:** IMA ADPCM conformance and live transport.
- **M7:** timeouts, disconnects, backpressure, condition matrices, reliability/resource reports.
- Competition additions only after M7: evaluate faster-whisper, then conditional Opus, then cloud experiment.

**Laptop Simulator (development tool):**
A laptop microphone client that captures audio, performs keyword detection, and streams to the backend using the frozen v1.0 protocol. Used to validate end-to-end behavior before ESP32-S3 hardware is available. Not a substitute for the final embedded implementation.

# Roadmap

**CURRENT MILESTONE: M3**

- **M1 (completed):** validate PCM WAV round trip and frozen transport. (298ceda)
- **M2 (completed):** simulated ESP32 client with synthetic PCM audio, acceptance tests for PCM transport layer. (febc0c5)
- **M3 (completed):** streaming ASR + telemetry
**Definition of Done:**
- Backend integrates non-blocking ASR worker per stream
- ASR worker runs in a separate thread to avoid blocking asyncio event loop
- Each stream has its own ASRWorker and KaldiRecognizer
- Telemetry includes ASR results count, keywords detected, and processing time
- All M1-M2 tests remain green; no protocol changes
- **M4:** ESP32-S3 I2S microphone input, local KWS, pre-buffer + live PCM streaming over frozen v1.0 protocol.
- **M5:** 100+ trial latency harness with p50/p95/p99.
- **M6:** IMA ADPCM conformance and live transport.
- **M7:** timeouts, disconnects, backpressure, condition matrices, reliability/resource reports.
- Competition additions only after M7: evaluate faster-whisper, then conditional Opus, then cloud experiment.

**Laptop Simulator (development tool):**
A laptop microphone client that captures audio, performs keyword detection, and streams to the backend using the frozen v1.0 protocol. Used to validate end-to-end behavior before ESP32-S3 hardware is available. Not a substitute for the final embedded implementation.

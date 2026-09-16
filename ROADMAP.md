# Roadmap

**CURRENT MILESTONE: M3**

- **M1 (completed):** validate PCM WAV round trip and frozen transport. (298ceda)
- **M2 (completed):** simulated ESP32 client with synthetic PCM audio, acceptance tests for PCM transport layer. (febc0c5)
- **M3 (in progress):** ESP32-S3 I2S microphone input, local KWS, pre-buffer + live PCM streaming over frozen v1.0 protocol.
**Definition of Done:**
- Device captures audio via I2S, runs MFCC/features, executes tiny int8 KWS
- Upon detection, streams pre-buffer (800ms) + live PCM via WebSocket binary frames
- Backend receives and reconstructs WAV (zero-padded to frame boundary) per stream_id
- End-to-end latency measured (T3-T0) with synthetic audio test harness
- All M1-M2 tests remain green; no protocol changes
- **M4:** 100+ trial latency harness with p50/p95/p99.
- **M5:** IMA ADPCM conformance and live transport.
- **M6:** timeouts, disconnects, backpressure, condition matrices, reliability/resource reports.
- Competition additions only after M6: evaluate faster-whisper, then conditional Opus, then cloud experiment.

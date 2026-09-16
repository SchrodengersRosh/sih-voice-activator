# SIH — Edge Voice Activator

An ESP32-S3-oriented voice activator: local keyword spotting triggers a short, buffered audio upload to a Python streaming-ASR backend. The device does not continuously upload microphone audio.

## Architecture Overview
Production flow: `I2S microphone → ring buffer → MFCC/features → tiny int8 KWS → temporal decision → pre-buffer + live audio → WebSocket → decoder → streaming ASR`

KWS and ASR answer different questions. KWS runs locally on the ESP32-S3; ASR is backend-side and begins only after activation. The pre-buffer preserves audio that precedes detection latency.

## Quick Start
Use Python 3.11 or 3.12 (the CI targets those versions):

```sh
python -m venv .venv
. .venv/bin/activate
pip install -e '.[dev]'
python -m backend.server --output-dir received
```

In another terminal, send a compatible WAV:

```sh
python tools/sim_esp32.py path/to/input.wav --stream-id 1
python tools/compare.py path/to/input.wav received/stream-1.wav
```

Expected comparison output is `IDENTICAL`; the received final frame is zero-padded to the 20 ms boundary.

## Tests
```sh
pytest
```

The integration suite creates three deterministic WAVs, including non-20-ms-aligned audio, and verifies simultaneous simulated clients and protocol ordering.

## Canonical Problem Statement & Constraints
**Core Problem**: Detect a custom wake word on ESP32-S3 using local KWS, then stream only the relevant audio (pre-buffer + live) to a backend for ASR processing, minimizing wireless transmission.

**Key Constraints**:
- Audio format: 16 kHz, mono, signed 16-bit little-endian PCM
- Frame size: 20 ms / 320 samples / 640 PCM bytes
- Wire protocol: Frozen v1.0 (see PROTOCOL.md)
- Power: Local KWS must run continuously on ESP32-S3 with minimal power
- Latency: Measure T3 − T0 (keyword end to first server frame) - target <100ms p95
- Reliability: Handle disconnects, timeouts, backpressure gracefully
- Scope: PCM transport (M1-2), ASR integration (M3), ADPCM codec (M5)

## Key Metrics
- **M4 Latency Harness**: T3 − T0 with p50/p95/p99 across 100+ trials
- **M5 Codec Conformance**: IMA ADPCM bit-exact reference implementation
- **M6 Reliability**: Timeouts, disconnects, backpressure, condition matrices

## Document References
- [AGENTS.md](AGENTS.md) - AI-agent constitution (procedures only)
- [ARCHITECTURE.md](ARCHITECTURE.md) - System boundaries and ownership
- [PROTOCOL.md](PROTOCOL.md) - Frozen v1.0 wire/audio contract
- [ROADMAP.md](ROADMAP.md) - Milestone status and progression
- [DECISIONS.md](DECISIONS.md) - Frozen/proposed/rejected decisions
# SIH — Edge Voice Activator

An ESP32-S3-oriented voice activator: local keyword spotting triggers a short, buffered audio upload to a Python streaming-ASR backend. The device does not continuously upload microphone audio.

**Current milestone: M1 — PCM round trip.** This repository implements WAV → simulated ESP32 → WebSocket → Python → WAV. ASR, ADPCM, Opus, and ESP32 firmware are deliberately deferred.

## Architecture

`I2S mic → local KWS → pre-buffer + live PCM → WebSocket → backend → WAV / future ASR`

The binary protocol is frozen in [PROTOCOL.md](PROTOCOL.md). Audio is 16 kHz, mono, signed 16-bit little-endian PCM in 20 ms / 640-byte frames.

## Quick start

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

Read [AGENTS.md](AGENTS.md), [ARCHITECTURE.md](ARCHITECTURE.md), [PROTOCOL.md](PROTOCOL.md), and [ROADMAP.md](ROADMAP.md) before substantial changes.

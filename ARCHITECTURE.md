# Architecture

## System Boundaries & Data Flow
**Edge (ESP32-S3)**: I2S microphone → ring buffer → MFCC/features → tiny int8 KWS → temporal decision → pre-buffer + live audio → WebSocket client
**Backend**: WebSocket server → decoder → streaming ASR → telemetry

## Ownership
- **Backend**: Protocol, stream lifecycle, decoding, ASR integration, latency instrumentation
- **Firmware (ESP32-S3)**: I2S conversion, pre-buffer management, trigger logic, transport
- **ML**: Keyword model, MFCC configuration, ASR model selection

## Directory/Module Map
```
backend/         # Python WebSocket server (M1-M2 implemented)
  server.py      # WebSocket handler, stream management, WAV output
  protocol.py    # Frozen v1.0 binary audio wire protocol
  __init__.py    # Package marker

tools/           # Simulation and test utilities
  sim_esp32.py   # ESP32 client simulator (M1-M2 implemented)
  compare.py     # WAV comparison utility

tests/           # Test suite
  test_roundtrip.py   # M1-M2 WAV round-trip tests
  test_m2.py          # M2 ASR integration tests
  test_protocol.py    # Protocol validation tests

docs/            # Additional documentation (if any)
```

## Concurrency Model
- **Backend**: Single-threaded asyncio event loop (must not be blocked by ASR)
- **WebSocket**: One connection handler per client, multiple streams per connection
- **Streams**: Independent per (connection, stream_id) tuple
- **Audio Processing**: Sequential frame handling within each stream

## Entry Points & Inspection Targets
- **Backend entry**: `python -m backend.server --output-dir received`
- **Simulation entry**: `python tools/sim_esp32.py path/to/input.wav --stream-id 1`
- **Test suite**: `pytest`
- **Key files to inspect**:
  - `backend/server.py`: Stream lifecycle, gap handling, finalization
  - `backend/protocol.py`: Frame packing/unpacking, validation
  - `tools/sim_esp32.py`: Frame generation, control messages
  - `tests/test_roundtrip.py`: End-to-end WAV round-trip validation

## Implemented vs Planned Components
**Implemented (M1-M2)**:
- ✅ Frozen v1.0 wire protocol (PCM only)
- ✅ WebSocket binary frames + JSON control messages
- ✅ Stream lifecycle (hello → hello_ack → start → frames → stop)
- ✅ Gap detection and bounded silence insertion
- ✅ Stream finalization to WAV (zero-padded to frame boundary)
- ✅ Simultaneous multi-client support
- ✅ Malformed frame defensive handling
- ✅ ASR integration hooks (M2: worker threads, partial/final text)

**Planned (M3+)**:
- 🔲 ESP32-S3 I2S microphone input (M3)
- 🔲 Persistent PCM connection on ESP32-S3 (M3)
- 🔲 100+ trial latency harness with p50/p95/p99 (M4)
- 🔲 IMA ADPCM conformance and live transport (M5)
- 🔲 Timeouts, disconnects, backpressure, condition matrices (M6)
- 🔲 ML keyword model and MFCC computation (M3+)
- 🔲 Backend ASR integration (M3+)

**Explicitly Out of Scope Unless Measurements Force Reconsideration**:
- Opus, μ-law codecs (PCM and IMA ADPCM only unless proven necessary)
- Frameworks, services, databases, or security layers without concrete need
- Continuous audio transmission (only post-activation streaming)
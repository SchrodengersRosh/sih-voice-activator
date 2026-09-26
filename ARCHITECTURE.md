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
firmware/        # ESP32-S3 edge firmware (ESP-IDF v5.1+)
  CMakeLists.txt # Root project CMake configuration
  sdkconfig.defaults # Low-latency Wi-Fi & I2S configuration
  main/
    main.c             # Edge activation loop and state machine
    protocol_frames.c  # Wire protocol binary frame packing (frozen v1.0)
    audio_ring_buffer.c# 800ms circular pre-buffer
    energy_detector.c  # Low-power energy gating (> -50 dBFS)
    kws_engine.c       # INT8 KWS with temporal moving average smoothing
    i2s_mic.c          # 16 kHz 16-bit mono PCM I2S driver (handles 24-in-32bit slots)
    ws_client.c        # Persistent WebSocket client
    wifi_connect.c     # Wi-Fi station manager with WIFI_PS_NONE

backend/         # Python WebSocket server (M1-M3 implemented)
  server.py      # WebSocket handler, stream management, WAV output, telemetry
  protocol.py    # Frozen v1.0 binary audio wire protocol
  asr/worker.py  # Non-blocking per-stream Vosk ASR worker threads

tools/           # Simulation and test utilities
  sim_esp32.py   # ESP32 client simulator (M1-M2 implemented)
  compare.py     # WAV comparison utility
  laptop_client.py   # Laptop microphone client for end-to-end validation (development tool)

tests/           # Test suite
  test_roundtrip.py   # M1-M2 WAV round-trip tests
  test_m2.py          # M2 ASR integration tests
  test_protocol.py    # Protocol validation tests
  test_m4_firmware.py # M4 ESP32-S3 firmware validation tests (native C + integration)
  test_laptop_client.py # Laptop microphone client tests

docs/            # Additional documentation
  hardware.md    # ESP32-S3 wiring, pinout, flashing, and build instructions
  latency.md     # Latency measurement definitions (T3 - T0)
```

## Concurrency Model
- **Backend**: Single-threaded asyncio event loop (must not be blocked by ASR)
- **WebSocket**: One connection handler per client, multiple streams per connection
- **Streams**: Independent per (connection, stream_id) tuple
- **Audio Processing**: Sequential frame handling within each stream
- **Firmware**: FreeRTOS tasks; audio acquisition pinned to Core 1

## Entry Points & Inspection Targets
- **Backend entry**: `python -m backend.server --output-dir received`
- **Firmware build**: `cd firmware && idf.py build`
- **Simulation entry**: `python tools/sim_esp32.py path/to/input.wav --stream-id 1`
- **Test suite**: `pytest`
- **Key files to inspect**:
  - `firmware/main/main.c`: Edge state machine, wake-word trigger, pre-buffer streaming
  - `backend/server.py`: Stream lifecycle, gap handling, finalization
  - `backend/protocol.py`: Frame packing/unpacking, validation
  - `tools/sim_esp32.py`: Frame generation, control messages
  - `tests/test_m4_firmware.py`: Host-side native C and integration validation

## Implemented vs Planned Components
**Implemented (M1-M4)**:
- ✅ Frozen v1.0 wire protocol (PCM only)
- ✅ WebSocket binary frames + JSON control messages
- ✅ Stream lifecycle (hello → hello_ack → start → frames → stop)
- ✅ Gap detection and bounded silence insertion
- ✅ Stream finalization to WAV (zero-padded to frame boundary)
- ✅ Simultaneous multi-client support
- ✅ Malformed frame defensive handling
- ✅ ASR integration (M3: per-stream non-blocking worker threads, partial/final text, telemetry)
- ✅ ESP32-S3 I2S microphone input (M4: 16 kHz mono 16-bit LE PCM, 24-in-32bit slot conversion)
- ✅ Local energy-gated INT8 KWS with temporal moving average smoothing (M4)
- ✅ Persistent Wi-Fi with WIFI_PS_NONE & persistent WebSocket connection (M4)
- ✅ 800 ms circular pre-buffer + live audio streaming over frozen v1.0 protocol (M4)

**Planned (M5+)**:
- 🔲 100+ trial latency harness with p50/p95/p99 (M5)
- 🔲 IMA ADPCM conformance and live transport (M6)
- 🔲 Timeouts, disconnects, backpressure, condition matrices (M7)
- 🔲 ML keyword model and MFCC computation (M4+)
- 🔲 Backend ASR integration (M3 implemented)

**Explicitly Out of Scope Unless Measurements Force Reconsideration**:
- Opus, μ-law codecs (PCM and IMA ADPCM only unless proven necessary)
- Frameworks, services, databases, or security layers without concrete need
- Continuous audio transmission (only post-activation streaming)
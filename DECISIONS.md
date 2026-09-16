# Decisions Log

## FROZEN DECISIONS

**DEC-PCM-001: Canonical Audio Format**
- **Decision**: 16 kHz, mono, signed 16-bit little-endian PCM is the canonical audio format for all live wire transmission.
- **Why**: Chosen for simplicity, deterministic behavior, and compatibility with constrained ESP32-S3 processing.
- **How to apply**: All WebSocket binary frames must use this format unless explicitly negotiating alternate codecs (not implemented in M1-M2). Reference: PROTOCOL.md, backend/protocol.py.

**DEC-WAV-002: WAV as Test/Input Container**
- **Decision**: WAV format is strictly for test input and validation output only. Live wire format is WebSocket binary frames per PROTOCOL.md.
- **Why**: Avoids unnecessary encapsulation overhead on wireless link; keeps live protocol minimal.
- **How to apply**: Tools like sim_esp32.py and compare.py use WAV for testing; backend/server.py writes WAV only for validation output. Never transmit WAV over WebSocket.

**DEC-PROTO-003: Frozen Protocol v1.0**
- **Decision**: The v1.0 wire protocol (header format, frame sizes, control message flow) is frozen and must not be silently altered.
- **Why**: Ensures interoperability between edge and backend implementations across milestones.
- **How to apply**: Any protocol change requires: documented implementation failure, interface identification, evidence/impact assessment, and new version number. See AGENTS.md contradiction procedure.

**DEC-CODEC-004: PCM Reference Codec**
- **Decision**: PCM s16le is the reference and only mandated codec for M1-M2. Implementation must reject non-PCM frames defensively.
- **Why**: Establishes baseline for timing, power, and correctness measurements before introducing complexity.
- **How to apply**: backend/protocol.py validates codec==0 (PCM) and raises ProtocolError for others. sim_esp32.py only sends PCM frames.

**DEC-ADPCM-005: IMA ADPCM Intended Final Codec**
- **Decision**: IMA ADPCM (codec=2) is the intended final codec for live transport in M5, pending conformance testing.
- **Why**: 2:1 compression reduces wireless transmission by 50% while maintaining acceptable audio quality for ASR.
- **How to apply**: M5 will implement IMA ADPCM encoding/decoding and negotiate via hello/hello_ack. Until then, treat as reserved for future use.

**DEC-OUT-SCOPE-006: Opus/μ-law Explicitly Out of Scope**
- **Decision**: Opus (codec=3) and μ-law (codec=1) are explicitly out of scope unless latency/power measurements force reconsideration.
- **Why**: Avoids scope creep; PCM and IMA ADPCM sufficient for project goals.
- **How to apply**: Reject frames with codec=1 or 3 in M1-M4. Measurements in M4-M5 may trigger re-evaluation per DEC-PROTO-003.

**DEC-PYTHON-007: Python Version Constraint**
- **Decision**: Backend targets Python 3.11 and 3.12 only (CI validates these versions).
- **Why**: Balances language feature access with broad compatibility; avoids bleeding-edge instability.
- **How to apply**: Use only language features available in 3.11+. CI configuration in pyproject.toml enforces this.

**DEC-ASIO-008: Asyncio Event Loop Constraint**
- **Decision**: The backend asyncio event loop must not be blocked by ASR processing; use worker threads or subprocesses for CPU-intensive tasks.
- **Why**: Prevents dropped audio frames and connection timeouts during ASR workload spikes.
- **How to apply**: M2 implemented worker threads for Vosk ASR. M3+ must maintain this pattern for any backend ML inference.

**DEC-ESP32-009: ESP32-S3 Target Platform**
- **Decision**: ESP32-S3 is the target hardware platform for all edge implementation.
- **Why**: Provides sufficient RAM, dual-core Xtensa, and AI accelerator for intended KWS+feature extraction workload.
- **How to apply**: All firmware-specific decisions (I2S, DMA, buffer sizes) assume ESP32-S3 capabilities. Porting to other ESP32 variants requires re-evaluation.

**DEC-KWS-010: Custom INT8 KWS**
- **Decision**: Keyword spotting uses a custom integer-8 quantized model trained for the target phrase.
- **Why**: Maximizes detection accuracy while fitting within ESP32-S3 SRAM constraints (<100KB typical).
- **How to apply**: MFCC features → INT8 model → temporal smoothing → trigger threshold. Model must be convertible via TensorFlow Lite Micro or equivalent.

**DEC-INFER-011: Energy-Gated Inference**
- **Decision**: KWS runs continuously but MFCC/model inference is gated by energy detector to reduce average power.
- **Why**: Extends battery life by avoiding compute during silence or noise.
- **How to apply**: Simple energy threshold (e.g., > -50 dBFS) enables MFCC windowing and model inference. Implement in firmware.

**DEC-OUT-SCOPE-012: Explicit Out-of-Scope Technologies/Features**
- **Decision**: The following are explicitly out of scope for M1-M6 unless measurements prove essential:
  - Noise suppression / echo cancellation
  - Beamforming / microphone arrays
  - Cloud-side wake word verification
  - Over-the-air firmware updates
  - Secure WebSocket (WSS) / encryption
  - Multi-stream multiplexing beyond current stream_id
  - Audio codecs beyond PCM/IMA ADPCM/Opus/μ-law
  - Non-Windows/Linux/macOS backend deployment
## PROPOSED DECISIONS

**(None currently proposed)**

## REJECTED DECISIONS

**DEC-REJ-001: Continuous Audio Transmission**
- **Decision**: Rejected continuous microphone audio transmission regardless of detection state.
- **Why**: Violates power and privacy constraints; defeats purpose of local KWS gating.
- **When**: Considered during initial architecture exploration.

**DEC-REJ-002: Mandatory Opus Support**
- **Decision**: Rejected making Opus a mandatory codec for baseline implementation.
- **Why**: Premature optimization; PCM provides sufficient baseline for measurement-driven codec selection in M5.
- **When**: During M1 protocol design discussions.

**DEC-REJ-003: HTTP/2 for Audio Transport**
- **Decision**: Rejected HTTP/2 in favor of WebSocket for low-latency, bidirectional audio streaming.
- **Why**: WebSocket provides lower framing overhead and true full-duplex with minimal latency.
- **When**: Evaluated during transport layer selection.
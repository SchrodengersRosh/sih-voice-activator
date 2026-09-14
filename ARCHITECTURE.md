# Architecture

Production flow is `I2S microphone → ring buffer → MFCC/features → tiny int8 KWS → temporal decision → pre-buffer + live audio → WebSocket → decoder → streaming ASR`.

KWS and ASR answer different questions. KWS runs locally on the ESP32-S3; ASR is backend-side and begins only after activation. The pre-buffer preserves audio that precedes detection latency.

M1 validates the transport and format only. The simulator reads a strict 16 kHz mono s16le WAV, pads it into 320-sample frames, sends control text plus binary audio, and the server reconstructs a WAV per `(connection, stream_id)`. The server detects gaps using decoded sample offsets, may insert bounded silence for a small forward gap, and finalizes on stop, inactivity, or close.

Latency must be measured, not invented. The headline M4 metric is `T3 − T0`: keyword end to first server frame. ASR endpoint/final time is a distinct metric.

# Agent Constitution

This is an edge voice activator: ESP32-S3 local KWS decides whether a custom phrase was spoken; only then are pre-buffered and live command samples sent to the backend for ASR.

Before meaningful work, read `AGENTS.md`, `ARCHITECTURE.md`, `PROTOCOL.md`, `ROADMAP.md`, the relevant implementation, and its tests. Inspect git state first. Make the smallest coherent change, run focused tests plus the practical full suite, update docs for changed behavior, and report uncertainty or failures honestly.

The v1.0 wire protocol in `PROTOCOL.md` is frozen. Do not silently alter its layout, control lifecycle, timings, codecs, or semantics. Document a real implementation failure, the affected interface, evidence, alternatives, impact, and tests before proposing an architectural change.

Milestones are ordered M1 PCM, M2 streaming ASR, M3 ESP32 PCM, M4 measurement, M5 ADPCM, M6 hardening. Do not implement future milestones while the active one is unproven. Prefer standard library and small explicit modules; do not add frameworks, services, databases, security layers, or dependencies without a concrete need. Never delete or weaken tests to make CI pass, fabricate measurements, or commit credentials/recordings/models.

Backend owns protocol, stream lifecycle, decoding, ASR integration, and latency instrumentation. Embedded owns I2S conversion, pre-buffer, trigger logic, and transport. ML owns keyword model and MFCC configuration. Interfaces must remain documented and explicit.

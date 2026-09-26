"""M3 WebSocket PCM receiver with ASR integration and telemetry.
Run with: python -m backend.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional

import wave

from websockets.asyncio.server import ServerConnection, serve
from websockets.exceptions import ConnectionClosed

from .protocol import (
    CODEC_PCM,
    CODEC_NAMES,
    FLAG_FIRST,
    FLAG_LAST,
    FLAG_PREBUF,
    PCM_PAYLOAD_LEN,
    SAMPLES_PER_FRAME,
    AudioFrame,
    ProtocolError,
    unpack,
    SAMPLE_RATE,
)
from .asr.worker import ASRResult, ASRWorker

# Vosk import for shared model resources
try:
    import vosk
except ImportError:
    vosk = None

# Debug counters for Vosk model loading
_vosk_model_construction_count = 0
_vosk_recognizer_construction_count = 0

LOG = logging.getLogger(__name__)
MAX_STREAM_SECONDS = 10
MAX_GAP_SAMPLES = SAMPLE_RATE * 2


@dataclass(slots=True)
class Stream:
    stream_id: int
    device_id: str
    pcm: bytearray = field(default_factory=bytearray)
    frames: int = 0
    gaps: int = 0
    bad: int = 0
    expected_offset: int = 0
    started_ns: int = field(default_factory=time.perf_counter_ns)
    first_frame_ns: int | None = None
    # ASR integration
    asr_worker: Optional[ASRWorker] = None
    asr_thread_started: bool = False
    last_asr_result: Optional[ASRResult] = None
    asr_result_count: int = 0
    partial_result_count: int = 0
    final_result_count: int = 0
    last_partial_text: str = ""
    # Telemetry
    keywords_detected: int = 0
    asr_processing_time_ms: float = 0.0
    # Reference to parent server for shared resources
    _server: Optional[Any] = None

    def _ensure_asr_worker(self) -> None:
        """Create and start the ASR worker for this stream if not already started."""
        if self.asr_thread_started:
            return
        try:
            # Use shared Vosk model if available
            if self._server and self._server._shared_asr_model is not None:
                self.asr_worker = ASRWorker(model=self._server._shared_asr_model)
            else:
                self.asr_worker = ASRWorker()
            self.asr_worker.start()
            self.asr_thread_started = True
        except Exception as e:
            # If worker creation fails, leave asr_worker as None and log.
            LOG.warning("Failed to create ASR worker for stream %d: %s", self.stream_id, e)
            self.asr_worker = None
            self.asr_thread_started = True  # Prevent repeated attempts

    def stop_asr_worker(self) -> None:
        """Stop the ASR worker for this stream."""
        if self.asr_worker is not None:
            self.asr_worker.stop()
            self.asr_worker = None


class VoiceServer:
    def __init__(self, output_dir: Path, *, inactivity_s: float = 1.5) -> None:
        self.output_dir = output_dir
        self.inactivity_s = inactivity_s
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Shared ASR model resource - loaded once per VoiceServer
        self._shared_asr_model: Optional[vosk.Model] = None
        self._shared_asr_recognizer: Optional[vosk.KaldiRecognizer] = None
        self._initialize_shared_asr_resources()

    def _initialize_shared_asr_resources(self) -> None:
        """Initialize shared Vosk model resources for all streams in this server."""
        if vosk is None:
            LOG.warning("Vosk not available, ASR functionality disabled")
            return

        try:
            # Determine model directory path (same logic as ASRWorker)
            model_dir = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"
            if not model_dir.is_dir():
                # Try local path
                model_dir = Path("vosk-model-small-en-us-0.15")
                if not model_dir.is_dir():
                    LOG.warning("Vosk model not found, ASR functionality disabled")
                    return

            # Load the model and recognizer once
            self._shared_asr_model = vosk.Model(str(model_dir))
            self._shared_asr_recognizer = vosk.KaldiRecognizer(self._shared_asr_model, SAMPLE_RATE)
            self._shared_asr_recognizer.SetWords(True)
            LOG.info("Shared Vosk model resources initialized")
        except Exception as e:
            LOG.warning("Failed to initialize shared Vosk resources: %s", e)
            self._shared_asr_model = None
            self._shared_asr_recognizer = None

    async def handler(self, ws: ServerConnection) -> None:
        hello: dict[str, Any] | None = None
        streams: dict[int, Stream] = {}
        try:
            while True:
                try:
                    message = await asyncio.wait_for(
                        ws.recv(), timeout=self.inactivity_s if streams else None
                    )
                except TimeoutError:
                    for stream in list(streams.values()):
                        self.finalize(stream, "inactivity")
                        del streams[stream.stream_id]
                    continue
                if isinstance(message, str):
                    hello = await self._control(ws, message, hello, streams)
                else:
                    self._audio(message, streams)
        except ConnectionClosed:
            LOG.info("connection closed; finalizing %d stream(s)", len(streams))
        finally:
            for stream in list(streams.values()):
                self.finalize(stream, "connection_closed")

    async def _control(
        self,
        ws: ServerConnection,
        raw: str,
        hello: dict[str, Any] | None,
        streams: dict[int, Stream],
    ) -> dict[str, Any] | None:
        try:
            msg = json.loads(raw)
        except json.JSONDecodeError:
            await ws.send(json.dumps({"type": "error", "code": "BAD_JSON"}))
            return hello
        if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
            await ws.send(json.dumps({"type": "error", "code": "BAD_CONTROL"}))
            return hello
        kind = msg["type"]
        if kind == "hello":
            if msg.get("proto") != 1 or not isinstance(msg.get("device_id"), str):
                await ws.send(json.dumps({"type": "error", "code": "BAD_HELLO"}))
                return hello
            if msg.get("sample_rate") != SAMPLE_RATE or "pcm_s16le" not in msg.get(
                "codecs", []
            ):
                await ws.send(json.dumps({"type": "error", "code": "UNSUPPORTED_FORMAT"}))
                return hello
            await ws.send(json.dumps({"type": "hello_ack", "proto": 1}))
            LOG.info("hello device=%s", msg["device_id"])
            return msg
        if kind == "start":
            if hello is None:
                await ws.send(json.dumps({"type": "error", "code": "HELLO_REQUIRED"}))
                await ws.close(code=1002, reason="HELLO_REQUIRED")
                return hello
            stream_id = msg.get("stream_id")
            if not isinstance(stream_id, int) or not 0 <= stream_id <= 0xFFFFFFFF:
                await ws.send(json.dumps({"type": "error", "code": "BAD_START"}))
                return hello
            if (msg.get("codec"), msg.get("sample_rate"), msg.get("channels"), msg.get("frame_ms")) != (
                "pcm_s16le",
                16000,
                1,
                20,
            ):
                await ws.send(json.dumps({"type": "error", "code": "UNSUPPORTED_FORMAT"}))
                return hello
            if stream_id in streams:
                await ws.send(json.dumps({"type": "error", "code": "DUPLICATE_STREAM"}))
                return hello
            stream = Stream(stream_id, hello["device_id"])
            stream._server = self  # Set server reference for shared resources
            streams[stream_id] = stream
            LOG.info("start stream=%d device=%s", stream_id, hello["device_id"])
            return hello
        if kind == "stop":
            stream_id = msg.get("stream_id")
            stream = streams.pop(stream_id, None)
            if stream is None:
                LOG.warning("stop for unknown stream=%r", stream_id)
            else:
                self.finalize(stream, str(msg.get("reason", "stop")))
            return hello
        await ws.send(json.dumps({"type": "error", "code": "UNKNOWN_CONTROL"}))
        return hello

    def _audio(self, raw: bytes, streams: dict[int, Stream]) -> None:
        try:
            frame = unpack(raw)
        except ProtocolError as exc:
            LOG.warning("dropped malformed binary frame: %s", exc)
            return
        stream = streams.get(frame.stream_id)
        if stream is None:
            LOG.warning("dropped frame for unknown stream=%d", frame.stream_id)
            return
        if frame.codec != CODEC_PCM:
            stream.bad += 1
            LOG.warning("dropped non-PCM frame stream=%d", stream.stream_id)
            return
        if stream.frames == 0 and not (frame.flags & FLAG_FIRST):
            stream.bad += 1
            LOG.warning("first frame missing FIRST flag stream=%d", frame.stream_id)
            return
        max_pcm_bytes = MAX_STREAM_SECONDS * SAMPLE_RATE * 2
        if len(stream.pcm) + PCM_PAYLOAD_LEN > max_pcm_bytes:
            LOG.warning("max duration reached stream=%d", stream.stream_id)
            self.finalize(stream, "max_duration")
            del streams[stream.stream_id]
            return

        if frame.sample_offset != stream.expected_offset:
            stream.gaps += 1
            if stream.expected_offset < frame.sample_offset <= stream.expected_offset + MAX_GAP_SAMPLES:
                missing = frame.sample_offset - stream.expected_offset
                max_missing_samples = max(0, (max_pcm_bytes - len(stream.pcm) - PCM_PAYLOAD_LEN) // 2)
                inserted = min(missing, max_missing_samples)
                if inserted > 0:
                    stream.pcm.extend(b"\0" * (inserted * 2))
                    LOG.warning("inserted %d silence samples stream=%d", inserted, frame.stream_id)
            else:
                stream.bad += 1
                LOG.warning(
                    "dropped invalid offset=%d expected=%d stream=%d",
                    frame.sample_offset,
                    stream.expected_offset,
                    stream.stream_id,
                )
                return

        if len(stream.pcm) + PCM_PAYLOAD_LEN > max_pcm_bytes:
            LOG.warning("max duration reached stream=%d", stream.stream_id)
            self.finalize(stream, "max_duration")
            del streams[stream.stream_id]
            return
        if stream.first_frame_ns is None:
            stream.first_frame_ns = time.perf_counter_ns()
        stream.pcm.extend(frame.payload)
        stream.frames += 1
        stream.expected_offset = frame.sample_offset + SAMPLES_PER_FRAME

        # ASR processing: send audio to worker thread (non-blocking)
        asr_start_time = time.perf_counter()
        try:
            stream._ensure_asr_worker()
            asr_worker = stream.asr_worker
            if asr_worker is not None:
                asr_worker.add_audio(frame.payload)
        except Exception as e:
            LOG.warning("Failed to queue audio for ASR: %s", e)
        finally:
            # Track ASR processing time (approximate)
            asr_end_time = time.perf_counter()
            stream.asr_processing_time_ms += (asr_end_time - asr_start_time) * 1000

        # Check for ASR results (non-blocking)
        try:
            asr_worker = stream.asr_worker
            if asr_worker is not None:
                asr_result = asr_worker.get_result(timeout=0.001)  # 1ms timeout
                if asr_result:
                    stream.last_asr_result = asr_result
                    stream.asr_result_count += 1
                    if asr_result.text.strip():
                        stream.keywords_detected += 1
                    if asr_result.is_final:
                        stream.final_result_count += 1
                        LOG.info(
                            "ASR result for stream %d: '%s' (final=%s)",
                            stream.stream_id,
                            asr_result.text,
                            asr_result.is_final,
                        )
                    else:
                        # Partial result
                        stream.partial_result_count += 1
                        # Only log if partial text changed
                        if asr_result.text.strip() != stream.last_partial_text:
                            stream.last_partial_text = asr_result.text.strip()
                            LOG.debug(
                                "ASR partial for stream %d: '%s'",
                                stream.stream_id,
                                asr_result.text,
                            )
        except Exception as e:
            LOG.warning("Error getting ASR result: %s", e)

    def finalize(self, stream: Stream, reason: str) -> Path:
        path = self.output_dir / f"stream-{stream.stream_id}.wav"
        with wave.open(str(path), "wb") as out:
            out.setnchannels(1)
            out.setsampwidth(2)
            out.setframerate(SAMPLE_RATE)
            out.writeframes(stream.pcm)
        LOG.info(
            "finalized stream=%d reason=%s frames=%d gaps=%d bad=%d asr_results=%d "
            "keywords_detected=%d asr_time=%.2fms path=%s",
            stream.stream_id,
            reason,
            stream.frames,
            stream.gaps,
            stream.bad,
            stream.asr_result_count,
            stream.keywords_detected,
            stream.asr_processing_time_ms,
            path,
        )
        stream.stop_asr_worker()
        return path

    def shutdown(self) -> None:
        """Clean up resources."""
        # No shared resources to clean up; per-stream workers are stopped when the stream ends.
        pass


async def run(host: str, port: int, output_dir: Path) -> None:
    app = VoiceServer(output_dir)
    try:
        async with serve(
            app.handler, host, port, max_size=2048, ping_interval=20, ping_timeout=20
        ):
            LOG.info("listening on ws://%s:%d", host, port)
            await asyncio.Future()
    finally:
        app.shutdown()


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--output-dir", type=Path, default=Path("received"))
    parser.add_argument("--debug", action='store_true')
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run(args.host, args.port, args.output_dir))


if __name__ == "__main__":
    main()

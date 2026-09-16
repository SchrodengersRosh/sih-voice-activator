"""M3 WebSocket PCM receiver with ASR integration and telemetry.
Run with: python -m backend.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import threading
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
    # Telemetry
    keywords_detected: int = 0
    asr_processing_time_ms: float = 0.0


class VoiceServer:
    def __init__(self, output_dir: Path, *, inactivity_s: float = 1.5) -> None:
        self.output_dir = output_dir
        self.inactivity_s = inactivity_s
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Global ASR worker pool (shared across streams for efficiency)
        self._asr_worker_pool: Optional[ASRWorker] = None
        self._asr_pool_lock = threading.Lock()

    def _get_asr_worker(self) -> ASRWorker:
        """Get or create ASR worker from pool."""
        with self._asr_pool_lock:
            if self._asr_worker_pool is None:
                self._asr_worker_pool = ASRWorker()
                self._asr_worker_pool.start()
            return self._asr_worker_pool

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
            streams[stream_id] = Stream(stream_id, hello["device_id"])
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
        if frame.sample_offset != stream.expected_offset:
            stream.gaps += 1
            if stream.expected_offset < frame.sample_offset <= stream.expected_offset + MAX_GAP_SAMPLES:
                missing = frame.sample_offset - stream.expected_offset
                stream.pcm.extend(b"\0" * (missing * 2))
                LOG.warning("inserted %d silence samples stream=%d", missing, frame.stream_id)
            else:
                stream.bad += 1
                LOG.warning(
                    "dropped invalid offset=%d expected=%d stream=%d",
                    frame.sample_offset,
                    stream.expected_offset,
                    stream.stream_id,
                )
                return
        if len(stream.pcm) + PCM_PAYLOAD_LEN > MAX_STREAM_SECONDS * SAMPLE_RATE * 2:
            LOG.warning("max duration reached stream=%d", stream.stream_id)
            return
        if stream.first_frame_ns is None:
            stream.first_frame_ns = time.perf_counter_ns()
        stream.pcm.extend(frame.payload)
        stream.frames += 1
        stream.expected_offset = frame.sample_offset + SAMPLES_PER_FRAME

        # ASR processing: send audio to worker thread (non-blocking)
        asr_start_time = time.perf_counter()
        try:
            asr_worker = self._get_asr_worker()
            asr_worker.add_audio(frame.payload)
        except Exception as e:
            LOG.warning("Failed to queue audio for ASR: %s", e)
        finally:
            # Track ASR processing time (approximate)
            asr_end_time = time.perf_counter()
            stream.asr_processing_time_ms += (asr_end_time - asr_start_time) * 1000

        # Check for ASR results (non-blocking)
        try:
            asr_result = asr_worker.get_result(timeout=0.001)  # 1ms timeout
            if asr_result:
                stream.last_asr_result = asr_result
                stream.asr_result_count += 1
                # Simple keyword detection: if we get any text, count it as detected
                # In a real implementation, this would use a proper keyword spotting model
                if asr_result.text.strip():
                    stream.keywords_detected += 1
                    LOG.info(
                        "ASR result for stream %d: '%s' (final=%s)",
                        stream.stream_id,
                        asr_result.text,
                        asr_result.is_final,
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
        return path

    def shutdown(self) -> None:
        """Clean up resources."""
        if self._asr_worker_pool:
            self._asr_worker_pool.stop()
            self._asr_worker_pool = None


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
    parser.add_argument("--debug", action=store_true)
    args = parser.parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    asyncio.run(run(args.host, args.port, args.output_dir))


if __name__ == "__main__":
    main()

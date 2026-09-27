"""M3 WebSocket PCM receiver with ASR integration and telemetry.
Run with: python -m backend.server
"""
from __future__ import annotations

import asyncio
import json
import logging
import struct
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
    pack,
    unpack,
    SAMPLE_RATE,
)
# Vosk import for shared model resources
try:
    import vosk
except ImportError:
    vosk = None

from .asr.worker import ASRResult, ASRWorker
from .llm.provider import LLMProvider
from .llm.ollama_provider import OllamaProvider
from .tts.provider import TTSProvider, get_default_tts_provider

# Debug counters for Vosk model loading
_vosk_model_construction_count = 0
_vosk_recognizer_construction_count = 0

LOG = logging.getLogger(__name__)
MAX_STREAM_SECONDS = 10
MAX_GAP_SAMPLES = SAMPLE_RATE * 2

# Endpointing constants
ENDPOINT_SILENCE_MS = 650  # Trailing silence to trigger endpointing
ENDPOINT_SILENCE_FRAMES = int((ENDPOINT_SILENCE_MS / 1000) * SAMPLE_RATE / SAMPLES_PER_FRAME)  # Convert ms to frames
ENERGY_THRESHOLD = 300     # RMS threshold for speech detection (empirical value)


class EndpointDetector:
    """Detects endpoint of speech based on trailing silence in PCM audio."""

    def __init__(self, silence_frames: int = ENDPOINT_SILENCE_FRAMES, energy_threshold: int = ENERGY_THRESHOLD):
        self.silence_frames = silence_frames
        self.energy_threshold = energy_threshold
        self.reset()

    def reset(self):
        """Reset the detector to initial state."""
        self.silence_frame_count = 0
        self.speech_active = False  # Track if we've seen significant audio
        self.endpoint_triggered = False

    def process_frame(self, payload: bytes) -> bool:
        """
        Process a PCM audio frame and return True if endpoint is detected.

        Args:
            payload: PCM audio frame bytes (16-bit signed little-endian)

        Returns:
            True if endpoint detected (trailing silence threshold reached)
        """
        # Calculate RMS energy of the frame
        energy = self._calculate_rms(payload)

        # Check if frame contains significant audio (speech)
        is_speech = energy > self.energy_threshold

        if is_speech:
            # Reset silence counter when speech is detected
            self.silence_frame_count = 0
            self.speech_active = True
        else:
            # Increment silence counter
            self.silence_frame_count += 1

        # Check for endpoint: trailing silence after speech activity
        if self.speech_active and not self.endpoint_triggered:
            if self.silence_frame_count >= self.silence_frames:
                self.endpoint_triggered = True
                return True

        return False

    def _calculate_rms(self, payload: bytes) -> float:
        """
        Calculate RMS (Root Mean Square) energy of 16-bit PCM audio.

        Args:
            payload: PCM audio frame bytes (16-bit signed little-endian)

        Returns:
            RMS energy value
        """
        if len(payload) < 2:
            return 0.0

        num_samples = len(payload) // 2
        samples = struct.unpack(f"<{num_samples}h", payload[: num_samples * 2])
        if not samples:
            return 0.0

        sum_squares = sum(s * s for s in samples)
        rms = (sum_squares / len(samples)) ** 0.5
        return rms

    def is_endpoint_triggered(self) -> bool:
        """Check if endpoint has been triggered."""
        return self.endpoint_triggered


class PCMFrameBuffer:
    """Buffers arbitrary PCM byte chunks and emits fixed 640-byte (20 ms @ 16 kHz mono s16le) frames."""

    FRAME_BYTES = PCM_PAYLOAD_LEN  # 640 bytes

    def __init__(self, frame_bytes: int = PCM_PAYLOAD_LEN) -> None:
        self.frame_bytes = frame_bytes
        self.buffer = bytearray()

    def feed(self, pcm_bytes: bytes) -> list[bytes]:
        """
        Accumulate incoming PCM bytes and extract all complete 640-byte frames in order.
        Any incomplete trailing bytes remain in the per-stream buffer for subsequent payloads.
        """
        if not pcm_bytes:
            return []
        self.buffer.extend(pcm_bytes)
        frames: list[bytes] = []
        while len(self.buffer) >= self.frame_bytes:
            frames.append(bytes(self.buffer[:self.frame_bytes]))
            del self.buffer[:self.frame_bytes]
        return frames

    @property
    def remaining_bytes(self) -> int:
        """Number of leftover unaligned bytes currently buffered."""
        return len(self.buffer)


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
    asr_worker: Optional['ASRWorker'] = None
    asr_thread_started: bool = False
    last_asr_result: Optional[ASRResult] = None
    asr_result_count: int = 0
    partial_result_count: int = 0
    final_result_count: int = 0
    last_partial_text: str = ""
    accumulated_text: list[str] = field(default_factory=list)
    asr_frame_buffer: 'PCMFrameBuffer' = field(default_factory=PCMFrameBuffer)
    # Endpointing & finalization tracking
    endpoint_detector: Optional['EndpointDetector'] = None
    endpoint_triggered: bool = False
    finalizing: bool = False
    finalized: bool = False
    processing_response: bool = False
    # Connection reference for downlink playback
    ws: Optional[ServerConnection] = None
    # Telemetry & LLM/TTS
    keywords_detected: int = 0
    asr_processing_time_ms: float = 0.0
    last_llm_response: Optional[str] = None
    last_llm_telemetry: Optional[Any] = None
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
    def __init__(
        self,
        output_dir: Path,
        *,
        inactivity_s: float = 1.5,
        llm_provider: Optional[LLMProvider] = None,
        tts_provider: Optional[TTSProvider] = None,
    ) -> None:
        self.output_dir = output_dir
        self.inactivity_s = inactivity_s
        self.output_dir.mkdir(parents=True, exist_ok=True)
        # Shared ASR model resource - loaded once per VoiceServer
        self._shared_asr_model: Optional[vosk.Model] = None
        self._shared_asr_recognizer: Optional[vosk.KaldiRecognizer] = None
        self._initialize_shared_asr_resources()
        self._llm_provider = llm_provider or OllamaProvider(model="qwen3:8b", think=False)
        self._tts_provider = tts_provider or get_default_tts_provider()
        self._next_audio_id = 42

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
            stream.ws = ws
            streams[stream_id] = stream
            LOG.info("start stream=%d device=%s", stream_id, hello["device_id"])
            return hello
        if kind == "stop":
            stream_id = msg.get("stream_id")
            stream = streams.get(stream_id)
            if stream is None:
                LOG.warning("stop for unknown stream=%r", stream_id)
            elif stream.finalized or stream.finalizing:
                LOG.info("stream %d already finalizing or finalized; ignoring stop", stream_id)
                streams.pop(stream_id, None)
            else:
                stream.finalizing = True
                asyncio.create_task(
                    self._finish_asr_worker_and_finalize(
                        stream, str(msg.get("reason", "stop")), ws, streams
                    )
                )
            return hello
        if kind == "play_stop":
            LOG.info("play_stop received: audio_id=%s reason=%s", msg.get("audio_id"), msg.get("reason"))
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

        # Stop accepting further audio if stream is finalizing or finalized
        if stream.finalizing or stream.finalized:
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
            if not (stream.finalizing or stream.finalized):
                stream.finalizing = True
                self.finalize(stream, "max_duration")
                streams.pop(stream.stream_id, None)
                if stream.ws is not None:
                    asyncio.create_task(
                        self._finish_asr_worker_and_finalize(stream, "max_duration", stream.ws, streams)
                    )
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

        if stream.first_frame_ns is None:
            stream.first_frame_ns = time.perf_counter_ns()
        stream.pcm.extend(frame.payload)
        stream.frames += 1
        stream.expected_offset = frame.sample_offset + SAMPLES_PER_FRAME

        # ASR processing: send audio to worker thread in normalized 640-byte frames (non-blocking)
        asr_start_time = time.perf_counter()
        try:
            stream._ensure_asr_worker()
            asr_worker = stream.asr_worker
            if asr_worker is not None:
                for chunk in stream.asr_frame_buffer.feed(frame.payload):
                    asr_worker.add_audio(chunk)
        except Exception as e:
            LOG.warning("Failed to queue audio for ASR: %s", e)
        finally:
            asr_end_time = time.perf_counter()
            stream.asr_processing_time_ms += (asr_end_time - asr_start_time) * 1000

        # Endpoint detection: process audio for endpointing
        try:
            if stream.endpoint_detector is None:
                stream.endpoint_detector = EndpointDetector(ENDPOINT_SILENCE_FRAMES, ENERGY_THRESHOLD)

            if not stream.endpoint_triggered and not stream.finalizing and not stream.finalized:
                if stream.endpoint_detector.process_frame(frame.payload):
                    stream.endpoint_triggered = True
                    stream.finalizing = True
                    LOG.info("Endpoint detected for stream %d, initiating finalization", stream.stream_id)
                    conn = stream.ws
                    asyncio.create_task(
                        self._finish_asr_worker_and_finalize(stream, "endpoint", conn, streams)
                    )
        except Exception as e:
            LOG.warning("Error in endpoint detection: %s", e)

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
                        if isinstance(asr_result.text, str) and asr_result.text.strip():
                            stream.accumulated_text.append(asr_result.text.strip())
                        LOG.info(
                            "ASR result for stream %d: '%s' (final=%s)",
                            stream.stream_id,
                            asr_result.text,
                            asr_result.is_final,
                        )
                    else:
                        stream.partial_result_count += 1
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
        # Prevent duplicate finalization
        if stream.finalized:
            return self.output_dir / f"stream-{stream.stream_id}.wav"
        stream.finalized = True

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
        pass

    async def _finish_asr_worker(self, stream: Stream) -> Optional[ASRResult]:
        """
        Finish ASR worker in a background task to avoid blocking asyncio event loop.
        This calls ASRWorker.finish() which may block on thread join.
        """
        try:
            if stream.asr_worker is not None:
                result = await asyncio.to_thread(stream.asr_worker.finish)
                stream.asr_worker = None
                if result is not None:
                    stream.last_asr_result = result
                    stream.asr_result_count += 1
                    stream.final_result_count += 1
                    LOG.info(
                        "ASR final result for stream %d: '%s' (final=%s)",
                        stream.stream_id,
                        result.text,
                        result.is_final,
                    )
                return result
            return stream.last_asr_result
        except Exception as e:
            LOG.warning("Error finishing ASR worker for stream %d: %s", stream.stream_id, e)
            return None

    async def send_playback(
        self,
        ws: ServerConnection,
        pcm_bytes: bytes,
        audio_id: Optional[int] = None,
        send_play_stop: bool = False,
    ) -> int:
        """Send play_start control message followed by binary PCM frames to device."""
        if audio_id is None:
            audio_id = self._next_audio_id
            self._next_audio_id += 1

        play_start_msg = {
            "type": "play_start",
            "audio_id": audio_id,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
            "frame_ms": 20,
        }
        LOG.info("[PLAYBACK] Sending play_start for audio_id=%d", audio_id)
        await ws.send(json.dumps(play_start_msg))

        # Pad to 640-byte frame boundary if needed
        if len(pcm_bytes) % PCM_PAYLOAD_LEN != 0:
            pad = PCM_PAYLOAD_LEN - (len(pcm_bytes) % PCM_PAYLOAD_LEN)
            pcm_bytes = pcm_bytes + (b"\x00" * pad)

        total_frames = len(pcm_bytes) // PCM_PAYLOAD_LEN
        if total_frames == 0:
            if send_play_stop:
                await ws.send(json.dumps({"type": "play_stop", "audio_id": audio_id, "reason": "eof"}))
                LOG.info("[PLAYBACK] Sent play_stop for audio_id=%d (empty audio)", audio_id)
            return 0

        for seq in range(total_frames):
            flags = 0
            if seq == 0:
                flags |= FLAG_FIRST
            if seq == total_frames - 1:
                flags |= FLAG_LAST

            payload = pcm_bytes[seq * PCM_PAYLOAD_LEN : (seq + 1) * PCM_PAYLOAD_LEN]
            sample_offset = seq * SAMPLES_PER_FRAME
            frame = AudioFrame(
                codec=CODEC_PCM,
                flags=flags,
                seq=seq,
                stream_id=audio_id,
                sample_offset=sample_offset,
                payload=payload,
            )
            await ws.send(pack(frame))

        LOG.info("[PLAYBACK] Sent %d playback frames for audio_id=%d", total_frames, audio_id)

        if send_play_stop:
            play_stop_msg = {
                "type": "play_stop",
                "audio_id": audio_id,
                "reason": "eof",
            }
            await ws.send(json.dumps(play_stop_msg))
            LOG.info("[PLAYBACK] Sent play_stop for audio_id=%d", audio_id)

        return total_frames

    async def _finish_asr_worker_and_finalize(
        self,
        stream: Stream,
        reason: str,
        ws: Optional[ServerConnection] = None,
        streams: Optional[dict[int, Stream]] = None,
    ) -> None:
        """
        Finish ASR worker, finalize the stream, and run LLM -> TTS -> Playback.
        """
        try:
            if stream.processing_response:
                return
            stream.processing_response = True

            # 1. Authoritative final ASR
            final_result = await self._finish_asr_worker(stream)
            if isinstance(final_result, ASRResult) and isinstance(final_result.text, str) and final_result.text.strip():
                stream.accumulated_text.append(final_result.text.strip())

            # 2. Finalize stream WAV file if not already finalized
            if not stream.finalized:
                self.finalize(stream, reason)
            if streams is not None:
                streams.pop(stream.stream_id, None)

            # 3. Check final transcript (accumulated, final result, or partial fallback)
            valid_accumulated = [t for t in stream.accumulated_text if isinstance(t, str)]
            final_text = " ".join(valid_accumulated).strip()
            if not final_text and stream.last_asr_result and isinstance(stream.last_asr_result.text, str) and stream.last_asr_result.text.strip():
                final_text = stream.last_asr_result.text.strip()
            if not final_text and stream.last_partial_text and isinstance(stream.last_partial_text, str):
                final_text = stream.last_partial_text.strip()

            if not final_text:
                LOG.info("[PIPELINE] Final transcript empty for stream %d; skipping LLM/TTS", stream.stream_id)
                return

            LOG.info("[PIPELINE] Final ASR text for stream %d: '%s'", stream.stream_id, final_text)

            # 4. LLM Generation
            if self._llm_provider is not None:
                try:
                    LOG.info("[LLM] Starting generation...")
                    LOG.info("[LLM] INPUT: %s", final_text)
                    llm_response, tel = await asyncio.to_thread(
                        self._llm_provider.generate_with_telemetry, final_text
                    )
                    stream.last_llm_response = llm_response
                    stream.last_llm_telemetry = tel
                    LOG.info("[LLM] RESPONSE:\n%s", llm_response)
                    LOG.info("[LLM] Generation complete for stream %d", stream.stream_id)
                except Exception as e:
                    LOG.error("[LLM] Generation failed for stream %d: %s", stream.stream_id, e)
                    return
            else:
                return

            if not stream.last_llm_response or not stream.last_llm_response.strip():
                return

            # 5. TTS Synthesis
            pcm_audio = b""
            if self._tts_provider is not None:
                try:
                    LOG.info("[TTS] Starting synthesis for stream %d: text='%s'", stream.stream_id, stream.last_llm_response.strip())
                    pcm_audio = await asyncio.to_thread(
                        self._tts_provider.synthesize_pcm, stream.last_llm_response.strip()
                    )
                    LOG.info("[TTS] Generated %d bytes PCM for stream %d", len(pcm_audio), stream.stream_id)
                except Exception as e:
                    LOG.error("[TTS] Synthesis failed for stream %d: %s", stream.stream_id, e)
                    return
            else:
                return

            if not pcm_audio:
                return

            # 6. Playback to ESP32 over WebSocket
            conn = ws or stream.ws
            if conn is not None:
                try:
                    LOG.info("[PLAYBACK] Initiating playback downlink for stream %d", stream.stream_id)
                    await self.send_playback(conn, pcm_audio, audio_id=stream.stream_id, send_play_stop=True)
                except Exception as e:
                    LOG.warning("[PLAYBACK] Playback send failed for stream %d: %s", stream.stream_id, e)
            else:
                LOG.warning("[PLAYBACK] No active connection available for stream %d", stream.stream_id)
        except Exception as e:
            LOG.warning("Error in _finish_asr_worker_and_finalize for stream %d: %s", stream.stream_id, e)


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

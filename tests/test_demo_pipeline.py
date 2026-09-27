"""Tests for Demo Sprint M6-B pipeline:
- Speech endpointing (~650 ms trailing silence)
- Final ASRWorker.finish() execution without event loop blocking
- Single LLM generation on authoritative final transcript
- TTS conversion to 16 kHz mono S16LE PCM
- Downlink playback protocol conforming to ESP32_BACKEND_DEVICE_CONTRACT.md
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.asr.worker import ASRResult, ASRWorker
from backend.llm.provider import LLMProvider, LLMTelemetry
from backend.protocol import (
    CODEC_PCM,
    FLAG_FIRST,
    FLAG_LAST,
    PCM_PAYLOAD_LEN,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    AudioFrame,
    pack,
    unpack,
)
from backend.server import (
    ENERGY_THRESHOLD,
    ENDPOINT_SILENCE_FRAMES,
    ENDPOINT_SILENCE_MS,
    EndpointDetector,
    Stream,
    VoiceServer,
)
from backend.tts.provider import FakeTTSProvider, TTSProvider


# ---------------------------------------------------------------------------
# Test Helpers
# ---------------------------------------------------------------------------

def create_loud_frame() -> bytes:
    """320 samples of max amplitude 16-bit PCM."""
    return b"\xff\x7f" * SAMPLES_PER_FRAME


def create_silence_frame() -> bytes:
    """320 samples of zero amplitude 16-bit PCM."""
    return b"\x00\x00" * SAMPLES_PER_FRAME


class FakeTestLLM(LLMProvider):
    def __init__(self, answer: str = "Dr. Rajendra Prasad was the first president.") -> None:
        self.answer = answer
        self.calls: list[str] = []

    def model_name(self) -> str:
        return "fake-qwen3:8b"

    def generate_stream(self, prompt: str, system: str = ""):
        self.calls.append(prompt)
        yield self.answer


# ---------------------------------------------------------------------------
# Part 1: Endpointing Tests
# ---------------------------------------------------------------------------

class TestEndpointing:
    """Requirements 1-9: Endpointing behavior and finalization safety."""

    def test_1_silence_before_speech_does_not_endpoint(self):
        """Silence before any speech activity never triggers an endpoint."""
        detector = EndpointDetector(silence_frames=ENDPOINT_SILENCE_FRAMES, energy_threshold=ENERGY_THRESHOLD)
        for _ in range(ENDPOINT_SILENCE_FRAMES * 3):
            assert not detector.process_frame(create_silence_frame())
        assert not detector.is_endpoint_triggered()
        assert not detector.speech_active

    def test_2_speech_followed_by_650ms_silence_endpoints(self):
        """Speech followed by ~650 ms silence triggers endpointing."""
        detector = EndpointDetector(silence_frames=ENDPOINT_SILENCE_FRAMES, energy_threshold=ENERGY_THRESHOLD)
        # Speech
        detector.process_frame(create_loud_frame())
        assert detector.speech_active

        # Trailing silence
        for i in range(ENDPOINT_SILENCE_FRAMES - 1):
            assert not detector.process_frame(create_silence_frame())
        # Final frame reaches threshold
        assert detector.process_frame(create_silence_frame())
        assert detector.is_endpoint_triggered()

    def test_3_speech_resets_silence_timer(self):
        """Speech in the middle of silence resets the silence accumulator."""
        detector = EndpointDetector(silence_frames=ENDPOINT_SILENCE_FRAMES, energy_threshold=ENERGY_THRESHOLD)
        detector.process_frame(create_loud_frame())

        # Partial silence
        for _ in range(ENDPOINT_SILENCE_FRAMES - 5):
            assert not detector.process_frame(create_silence_frame())

        # Speech burst resets timer
        assert not detector.process_frame(create_loud_frame())
        assert detector.silence_frame_count == 0

        # Further partial silence does not trigger
        for _ in range(ENDPOINT_SILENCE_FRAMES - 5):
            assert not detector.process_frame(create_silence_frame())
        assert not detector.is_endpoint_triggered()

    @pytest.mark.asyncio
    async def test_4_endpoint_calls_asr_worker_finish(self, tmp_path: Path):
        """When endpoint occurs, ASRWorker.finish() is called."""
        mock_llm = FakeTestLLM()
        mock_tts = FakeTTSProvider()
        server = VoiceServer(tmp_path, llm_provider=mock_llm, tts_provider=mock_tts)

        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="who was the first president", is_final=True)

        streams = {}
        stream = Stream(stream_id=1, device_id="dev-1")
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True
        stream._server = server
        streams[1] = stream

        # Send speech frame
        f_speech = AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, create_loud_frame())
        server._audio(pack(f_speech), streams)

        # Send trailing silence frames
        for seq in range(1, ENDPOINT_SILENCE_FRAMES + 1):
            f_silence = AudioFrame(CODEC_PCM, 0, seq, 1, seq * SAMPLES_PER_FRAME, create_silence_frame())
            server._audio(pack(f_silence), streams)

        # Allow async task to complete
        await asyncio.sleep(0.05)
        mock_worker.finish.assert_called_once()
        assert stream.finalized

    @pytest.mark.asyncio
    async def test_5_endpoint_occurs_only_once(self, tmp_path: Path):
        """Endpoint detection sets flags and only initiates finalization once."""
        mock_llm = FakeTestLLM()
        server = VoiceServer(tmp_path, llm_provider=mock_llm)

        streams = {}
        stream = Stream(stream_id=1, device_id="dev-1")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True
        stream._server = server
        streams[1] = stream

        # Speech
        server._audio(pack(AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, create_loud_frame())), streams)

        # Send silence frames past the threshold
        for seq in range(1, ENDPOINT_SILENCE_FRAMES + 10):
            server._audio(pack(AudioFrame(CODEC_PCM, 0, seq, 1, seq * SAMPLES_PER_FRAME, create_silence_frame())), streams)

        await asyncio.sleep(0.05)
        # Worker finish should only be called once
        assert mock_worker.finish.call_count == 1

    @pytest.mark.asyncio
    async def test_6_endpoint_plus_explicit_stop_does_not_double_finalize(self, tmp_path: Path):
        """If endpoint occurs and client sends stop, finalize is called only once."""
        server = VoiceServer(tmp_path)
        finalize_calls: list[str] = []
        orig_finalize = server.finalize

        def tracked_finalize(st, reason):
            finalize_calls.append(reason)
            return orig_finalize(st, reason)

        server.finalize = tracked_finalize

        streams = {}
        mock_ws = AsyncMock()
        hello = await server._control(mock_ws, json.dumps({
            "type": "hello", "proto": 1, "device_id": "dev-1", "codecs": ["pcm_s16le"], "sample_rate": 16000
        }), None, streams)
        await server._control(mock_ws, json.dumps({
            "type": "start", "stream_id": 1, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20
        }), hello, streams)

        stream = streams[1]
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        # Trigger endpointing
        server._audio(pack(AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, create_loud_frame())), streams)
        for seq in range(1, ENDPOINT_SILENCE_FRAMES + 1):
            server._audio(pack(AudioFrame(CODEC_PCM, 0, seq, 1, seq * SAMPLES_PER_FRAME, create_silence_frame())), streams)

        await asyncio.sleep(0.05)
        assert stream.finalized

        # Client sends stop message
        await server._control(mock_ws, json.dumps({"type": "stop", "stream_id": 1, "reason": "normal"}), hello, streams)

        # Finalize should only have been called once for endpoint
        assert finalize_calls == ["endpoint"]

    @pytest.mark.asyncio
    async def test_7_endpoint_plus_max_duration_does_not_double_finalize(self, tmp_path: Path):
        """If endpoint occurs, extra frames pushing to 10s max duration do not re-finalize."""
        server = VoiceServer(tmp_path)
        finalize_calls: list[str] = []
        orig_finalize = server.finalize

        def tracked_finalize(st, reason):
            finalize_calls.append(reason)
            return orig_finalize(st, reason)

        server.finalize = tracked_finalize

        streams = {}
        stream = Stream(stream_id=1, device_id="dev-1")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True
        stream._server = server
        streams[1] = stream

        # Trigger endpoint
        server._audio(pack(AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, create_loud_frame())), streams)
        for seq in range(1, ENDPOINT_SILENCE_FRAMES + 1):
            server._audio(pack(AudioFrame(CODEC_PCM, 0, seq, 1, seq * SAMPLES_PER_FRAME, create_silence_frame())), streams)

        await asyncio.sleep(0.05)
        assert stream.finalized
        assert finalize_calls == ["endpoint"]

        # Send overflow frames that would otherwise trigger max_duration
        stream.pcm = bytearray(b"\x00" * (10 * SAMPLE_RATE * 2))
        server._audio(pack(AudioFrame(CODEC_PCM, 0, 100, 1, 100 * SAMPLES_PER_FRAME, create_silence_frame())), streams)

        # Still only one finalization
        assert finalize_calls == ["endpoint"]

    @pytest.mark.asyncio
    async def test_8_empty_final_transcript_does_not_call_llm(self, tmp_path: Path):
        """If final ASR transcript is empty, LLM is never invoked."""
        mock_llm = FakeTestLLM()
        server = VoiceServer(tmp_path, llm_provider=mock_llm)

        stream = Stream(stream_id=1, device_id="dev-1")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="   ", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        await server._finish_asr_worker_and_finalize(stream, "endpoint")
        assert len(mock_llm.calls) == 0

    @pytest.mark.asyncio
    async def test_9_non_empty_final_transcript_calls_llm_exactly_once(self, tmp_path: Path):
        """If final ASR transcript is non-empty, LLM is invoked exactly once."""
        mock_llm = FakeTestLLM()
        mock_tts = FakeTTSProvider()
        server = VoiceServer(tmp_path, llm_provider=mock_llm, tts_provider=mock_tts)

        stream = Stream(stream_id=1, device_id="dev-1")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="who was the first president of india", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        await server._finish_asr_worker_and_finalize(stream, "endpoint")
        assert mock_llm.calls == ["who was the first president of india"]
        assert stream.last_llm_response == mock_llm.answer


# ---------------------------------------------------------------------------
# Part 2: LLM, TTS & Downlink Playback Tests
# ---------------------------------------------------------------------------

class TestPlaybackAndTTS:
    """Requirements 10-17: LLM -> TTS -> Downlink Playback protocol."""

    @pytest.mark.asyncio
    async def test_10_llm_response_is_passed_to_tts_exactly_once(self, tmp_path: Path):
        """LLM response text is passed directly to TTS exactly once."""
        mock_llm = FakeTestLLM(answer="The capital of India is New Delhi.")
        mock_tts = MagicMock(spec=TTSProvider)
        mock_tts.synthesize_pcm.return_value = b"\x00" * 640

        server = VoiceServer(tmp_path, llm_provider=mock_llm, tts_provider=mock_tts)
        stream = Stream(stream_id=1, device_id="dev-1")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="what is the capital of india", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        await server._finish_asr_worker_and_finalize(stream, "endpoint")
        mock_tts.synthesize_pcm.assert_called_once_with("The capital of India is New Delhi.")

    def test_11_tts_output_is_converted_into_16khz_mono_s16le_pcm(self):
        """FakeTTSProvider outputs valid 16 kHz mono S16LE PCM aligned to 640 bytes."""
        tts = FakeTTSProvider(frame_count=3)
        pcm = tts.synthesize_pcm("test prompt")
        assert len(pcm) == 3 * 640
        assert len(pcm) % 2 == 0  # 16-bit samples

    @pytest.mark.asyncio
    async def test_12_play_start_message_has_exact_contract_fields(self, tmp_path: Path):
        """Backend emits play_start matching ESP32_BACKEND_DEVICE_CONTRACT.md."""
        server = VoiceServer(tmp_path)
        mock_ws = AsyncMock()

        pcm_data = b"\x00\x00" * 320  # 1 frame = 640 bytes
        await server.send_playback(mock_ws, pcm_data, audio_id=42)

        # First message sent on WS must be play_start JSON
        sent_messages = [call.args[0] for call in mock_ws.send.call_args_list]
        first_msg = sent_messages[0]
        assert isinstance(first_msg, str)
        parsed = json.loads(first_msg)

        assert parsed == {
            "type": "play_start",
            "audio_id": 42,
            "codec": "pcm_s16le",
            "sample_rate": 16000,
            "channels": 1,
            "frame_ms": 20,
        }

    @pytest.mark.asyncio
    async def test_13_playback_pcm_split_into_640_byte_frames(self, tmp_path: Path):
        """Playback audio is chunked into 640-byte binary frame payloads."""
        server = VoiceServer(tmp_path)
        mock_ws = AsyncMock()

        # Send 1280 bytes = 2 frames
        pcm_data = b"\x01\x00" * 640
        await server.send_playback(mock_ws, pcm_data, audio_id=99)

        # Messages: [play_start JSON, frame_0, frame_1]
        sent = [call.args[0] for call in mock_ws.send.call_args_list]
        assert len(sent) == 3

        frame_0 = unpack(sent[1])
        frame_1 = unpack(sent[2])

        assert len(frame_0.payload) == PCM_PAYLOAD_LEN
        assert len(frame_1.payload) == PCM_PAYLOAD_LEN

    @pytest.mark.asyncio
    async def test_14_15_first_and_last_flags_on_playback_frames(self, tmp_path: Path):
        """FLAG_FIRST is set only on frame 0, FLAG_LAST is set only on the final frame."""
        server = VoiceServer(tmp_path)
        mock_ws = AsyncMock()

        # Send 3 frames of audio (1920 bytes)
        pcm_data = b"\x02\x00" * (320 * 3)
        await server.send_playback(mock_ws, pcm_data, audio_id=55)

        sent = [call.args[0] for call in mock_ws.send.call_args_list]
        f0 = unpack(sent[1])
        f1 = unpack(sent[2])
        f2 = unpack(sent[3])

        # Frame 0: FIRST
        assert (f0.flags & FLAG_FIRST) != 0
        assert (f0.flags & FLAG_LAST) == 0

        # Frame 1: Intermediate
        assert (f1.flags & FLAG_FIRST) == 0
        assert (f1.flags & FLAG_LAST) == 0

        # Frame 2: LAST
        assert (f2.flags & FLAG_FIRST) == 0
        assert (f2.flags & FLAG_LAST) != 0

    @pytest.mark.asyncio
    async def test_16_playback_audio_id_used_in_headers(self, tmp_path: Path):
        """Binary frame header stream_id field carries audio_id for downlink playback."""
        server = VoiceServer(tmp_path)
        mock_ws = AsyncMock()

        pcm_data = b"\x00" * 640
        await server.send_playback(mock_ws, pcm_data, audio_id=777)

        sent = [call.args[0] for call in mock_ws.send.call_args_list]
        frame = unpack(sent[1])
        assert frame.stream_id == 777
        assert frame.seq == 0
        assert frame.sample_offset == 0
        assert frame.codec == CODEC_PCM

    def test_17_no_microphone_protocol_regression(self):
        """Microphone binary framing remains strictly 16-byte header + 640-byte payload."""
        payload = b"\x12\x34" * 320
        frame = AudioFrame(
            codec=CODEC_PCM,
            flags=FLAG_FIRST,
            seq=0,
            stream_id=1,
            sample_offset=0,
            payload=payload,
        )
        packed = pack(frame)
        assert len(packed) == 16 + 640
        unpacked = unpack(packed)
        assert unpacked == frame

    @pytest.mark.asyncio
    async def test_18_client_stop_message_triggers_full_post_asr_pipeline(self, tmp_path: Path):
        """
        Regression test: When client sends stop (reason='silence'),
        the server MUST execute the post-ASR response pipeline:
        finish ASR -> LLM -> TTS -> play_start -> frames -> play_stop.
        """
        mock_llm = FakeTestLLM(answer="Dr. Rajendra Prasad was the first president of India.")
        mock_tts = FakeTTSProvider(frame_count=2)
        server = VoiceServer(tmp_path, llm_provider=mock_llm, tts_provider=mock_tts)

        mock_ws = AsyncMock()
        streams = {}

        # 1. Hello
        hello = await server._control(mock_ws, json.dumps({
            "type": "hello", "proto": 1, "device_id": "edgeai-esp32s3", "codecs": ["pcm_s16le"], "sample_rate": 16000
        }), None, streams)

        # 2. Start
        await server._control(mock_ws, json.dumps({
            "type": "start", "stream_id": 1, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20
        }), hello, streams)

        stream = streams[1]
        mock_worker = MagicMock(spec=ASRWorker)
        # Worker finish returns the final transcript
        mock_worker.finish.return_value = ASRResult(text="who was the president of india", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        # Send a couple of audio frames
        server._audio(pack(AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, create_loud_frame())), streams)
        server._audio(pack(AudioFrame(CODEC_PCM, 0, 1, 1, 320, create_loud_frame())), streams)

        # 3. Client sends stop with reason='silence' (exact physical ESP32 message)
        await server._control(mock_ws, json.dumps({
            "type": "stop", "stream_id": 1, "reason": "silence"
        }), hello, streams)

        # Allow async task to complete
        await asyncio.sleep(0.1)

        # Verify ASR worker was finished
        mock_worker.finish.assert_called_once()
        # Verify LLM was called with the transcript
        assert mock_llm.calls == ["who was the president of india"]
        assert stream.last_llm_response == "Dr. Rajendra Prasad was the first president of India."

        # Verify messages sent to WebSocket:
        # Expected: hello_ack (from hello) -> play_start -> 2 binary frames -> play_stop
        sent_messages = [call.args[0] for call in mock_ws.send.call_args_list]
        text_messages = [m for m in sent_messages if isinstance(m, str)]
        binary_messages = [m for m in sent_messages if isinstance(m, bytes)]

        # Verify play_start
        assert any("play_start" in m for m in text_messages)
        # Verify play_stop
        assert any("play_stop" in m for m in text_messages)
        # Verify binary playback frames (2 frames)
        assert len(binary_messages) == 2

    @pytest.mark.asyncio
    async def test_19_llm_input_and_response_are_logged(self, tmp_path: Path, caplog: pytest.LogCaptureFixture):
        """Verify LLM input and complete response are visibly logged."""
        import logging
        caplog.set_level(logging.INFO)

        expected_response = "The capital of France is Paris.\nIt is known for the Eiffel Tower."
        mock_llm = FakeTestLLM(answer=expected_response)
        mock_tts = FakeTTSProvider()
        server = VoiceServer(tmp_path, llm_provider=mock_llm, tts_provider=mock_tts)

        stream = Stream(stream_id=5, device_id="test-dev")
        mock_worker = MagicMock(spec=ASRWorker)
        mock_worker.finish.return_value = ASRResult(text="what is the capital of france", is_final=True)
        stream.asr_worker = mock_worker
        stream.asr_thread_started = True

        await server._finish_asr_worker_and_finalize(stream, "endpoint")

        # Verify input was logged
        assert any("[LLM] INPUT: what is the capital of france" in record.message for record in caplog.records)
        # Verify complete multi-line response was logged
        assert any(f"[LLM] RESPONSE:\n{expected_response}" in record.message for record in caplog.records)



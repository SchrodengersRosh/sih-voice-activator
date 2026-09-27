"""Tests for speech endpointing functionality."""

import asyncio
import struct
import time
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from backend.protocol import (
    CODEC_PCM,
    FLAG_FIRST,
    PCM_PAYLOAD_LEN,
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    AudioFrame,
    pack,
)
from backend.server import EndpointDetector, VoiceServer, Stream


def create_silence_frame() -> bytes:
    """Create a silence frame (all zeros)."""
    return b"\x00\x00" * SAMPLES_PER_FRAME


def create_loud_frame() -> bytes:
    """Create a loud frame (max amplitude)."""
    return b"\xff\x7f" * SAMPLES_PER_FRAME  # 32767 in little-endian


def create_medium_frame() -> bytes:
    """Create a medium frame (half amplitude)."""
    return b"\xbf\x7f" * SAMPLES_PER_FRAME  # 16383 in little-endian


class TestEndpointDetector:
    """Tests for the EndpointDetector class."""

    def test_speech_detected_resets_silence_counter(self):
        """Speech detection should reset silence counter."""
        detector = EndpointDetector(silence_frames=3, energy_threshold=100)

        # Process speech frame (should reset counter)
        detector.process_frame(create_loud_frame())
        assert detector.silence_frame_count == 0
        assert detector.speech_active is True
        assert detector.is_endpoint_triggered() is False

        # Process silence frames
        detector.process_frame(create_silence_frame())
        assert detector.silence_frame_count == 1
        detector.process_frame(create_silence_frame())
        assert detector.silence_frame_count == 2
        detector.process_frame(create_silence_frame())
        assert detector.silence_frame_count == 3
        assert detector.is_endpoint_triggered() is True

    def test_silence_before_speech_no_endpoint(self):
        """Silence before speech should not trigger endpoint."""
        detector = EndpointDetector(silence_frames=3, energy_threshold=100)

        # Process silence frames first
        detector.process_frame(create_silence_frame())
        detector.process_frame(create_silence_frame())
        detector.process_frame(create_silence_frame())
        assert detector.is_endpoint_triggered() is False
        assert detector.speech_active is False  # Still no speech detected

        # Now process speech
        detector.process_frame(create_loud_frame())
        assert detector.silence_frame_count == 0
        assert detector.speech_active is True
        assert detector.is_endpoint_triggered() is False

    def test_short_silence_does_not_trigger_endpoint(self):
        """Short silence periods should not trigger endpoint."""
        detector = EndpointDetector(silence_frames=5, energy_threshold=100)

        # Start with speech
        detector.process_frame(create_loud_frame())
        assert detector.speech_active is True

        # Add short silence (less than threshold)
        for _ in range(3):  # 3 < 5
            detector.process_frame(create_silence_frame())
            assert detector.is_endpoint_triggered() is False

        # Add more speech to reset
        detector.process_frame(create_loud_frame())
        assert detector.silence_frame_count == 0
        assert detector.is_endpoint_triggered() is False

    def test_exact_silence_threshold_triggers_endpoint(self):
        """Exact silence threshold should trigger endpoint."""
        detector = EndpointDetector(silence_frames=3, energy_threshold=100)

        # Start with speech
        detector.process_frame(create_loud_frame())
        assert detector.speech_active is True

        # Add exact threshold of silence
        for i in range(3):
            detector.process_frame(create_silence_frame())
            if i < 2:  # First two should not trigger
                assert detector.is_endpoint_triggered() is False
            else:  # Third should trigger
                assert detector.is_endpoint_triggered() is True

    def test_no_endpoint_without_speech_activity(self):
        """Endpoint should not trigger without prior speech activity."""
        detector = EndpointDetector(silence_frames=3, energy_threshold=100)

        # Process only silence frames
        for _ in range(10):
            detector.process_frame(create_silence_frame())
            assert detector.is_endpoint_triggered() is False
        assert detector.speech_active is False

    def test_energy_calculation(self):
        """Test RMS energy calculation."""
        detector = EndpointDetector(silence_frames=3, energy_threshold=100)

        # Silence should have zero energy
        silence_energy = detector._calculate_rms(create_silence_frame())
        assert silence_energy == 0.0

        # Loud frame should have high energy
        loud_energy = detector._calculate_rms(create_loud_frame())
        assert loud_energy > 1000  # Should be well above threshold

        # Medium frame should have medium energy
        medium_energy = detector._calculate_rms(create_medium_frame())
        assert 0 < medium_energy < loud_energy


class TestServerEndpointing:
    """Tests for endpointing integration in VoiceServer."""

    @pytest.fixture
    def server(self, tmp_path):
        """Create a VoiceServer instance for testing."""
        return VoiceServer(tmp_path)

    @pytest.mark.asyncio
    async def test_endpointing_triggers_after_silence(self, server):
        """Test that endpointing triggers after sufficient trailing silence."""
        # Mock the _finish_asr_worker method to track calls
        with patch.object(server, '_finish_asr_worker', new_callable=AsyncMock) as mock_finish:
            streams = {}
            stream_id = 1
            device_id = "test-device"

            # Process hello and start
            hello = await server._control(AsyncMock(), '{"type":"hello","proto":1,"device_id":"test-device","sample_rate":16000,"codecs":["pcm_s16le"]}', None, streams)
            await server._control(AsyncMock(), '{"type":"start","stream_id":1,"codec":"pcm_s16le","sample_rate":16000,"channels":1,"frame_ms":20}', hello, streams)

            stream = streams[stream_id]

            # Send some speech frames
            for _ in range(10):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=FLAG_FIRST if _ == 0 else 0,
                    seq=_,
                    stream_id=stream_id,
                    sample_offset=_ * SAMPLES_PER_FRAME,
                    payload=create_loud_frame()
                )
                server._audio(pack(frame), streams)

            # Verify endpoint detector was initialized and speech active
            assert stream.endpoint_detector is not None
            assert stream.endpoint_detector.speech_active is True
            assert stream.endpoint_triggered is False

            # Send silence frames to trigger endpoint
            # Need ENDPOINT_SILENCE_FRAMES consecutive silence frames
            silence_frames_needed = int((650 / 1000) * SAMPLE_RATE / SAMPLES_PER_FRAME)  # 650ms in frames

            for i in range(silence_frames_needed):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=0,
                    seq=10 + i,
                    stream_id=stream_id,
                    sample_offset=(10 + i) * SAMPLES_PER_FRAME,
                    payload=create_silence_frame()
                )
                server._audio(pack(frame), streams)

                # Check if endpoint triggered (should be on last frame)
                if i == silence_frames_needed - 1:
                    # Give async task time to run
                    await asyncio.sleep(0.02)
                    # Verify finish was called
                    mock_finish.assert_called_once_with(stream)
                else:
                    # Should not have triggered yet
                    mock_finish.assert_not_called()

    @pytest.mark.asyncio
    async def test_no_endpoint_before_speech(self, server):
        """Test that silence before speech does not trigger endpoint."""
        with patch.object(server, '_finish_asr_worker', new_callable=AsyncMock) as mock_finish:
            streams = {}
            stream_id = 1
            device_id = "test-device"

            # Process hello and start
            hello = await server._control(AsyncMock(), '{"type":"hello","proto":1,"device_id":"test-device","sample_rate":16000,"codecs":["pcm_s16le"]}', None, streams)
            await server._control(AsyncMock(), '{"type":"start","stream_id":1,"codec":"pcm_s16le","sample_rate":16000,"channels":1,"frame_ms":20}', hello, streams)

            stream = streams[stream_id]

            # Send only silence frames (more than endpoint threshold)
            silence_frames_needed = int((650 / 1000) * SAMPLE_RATE / SAMPLES_PER_FRAME) + 5

            for i in range(silence_frames_needed):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=FLAG_FIRST if i == 0 else 0,
                    seq=i,
                    stream_id=stream_id,
                    sample_offset=i * SAMPLES_PER_FRAME,
                    payload=create_silence_frame()
                )
                server._audio(pack(frame), streams)

            # Verify no endpoint triggered and no finish called
            assert stream.endpoint_triggered is False
            assert stream.endpoint_detector.speech_active is False
            mock_finish.assert_not_called()

    @pytest.mark.asyncio
    async def test_speech_after_silence_resets_timer(self, server):
        """Test that speech after short silence resets the silence timer."""
        with patch.object(server, '_finish_asr_worker', new_callable=AsyncMock) as mock_finish:
            streams = {}
            stream_id = 1
            device_id = "test-device"

            # Process hello and start
            hello = await server._control(AsyncMock(), '{"type":"hello","proto":1,"device_id":"test-device","sample_rate":16000,"codecs":["pcm_s16le"]}', None, streams)
            await server._control(AsyncMock(), '{"type":"start","stream_id":1,"codec":"pcm_s16le","sample_rate":16000,"channels":1,"frame_ms":20}', hello, streams)

            stream = streams[stream_id]

            # Start with speech
            for _ in range(5):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=FLAG_FIRST if _ == 0 else 0,
                    seq=_,
                    stream_id=stream_id,
                    sample_offset=_ * SAMPLES_PER_FRAME,
                    payload=create_loud_frame()
                )
                server._audio(pack(frame), streams)

            # Send short silence (less than endpoint threshold)
            short_silence = 10  # Less than 650ms worth of frames
            for i in range(short_silence):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=0,
                    seq=5 + i,
                    stream_id=stream_id,
                    sample_offset=(5 + i) * SAMPLES_PER_FRAME,
                    payload=create_silence_frame()
                )
                server._audio(pack(frame), streams)

            # Verify still active, not triggered
            assert stream.endpoint_detector.speech_active is True
            assert stream.endpoint_triggered is False

            # Send more speech - should reset silence counter
            for i in range(5):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=0,
                    seq=5 + short_silence + i,
                    stream_id=stream_id,
                    sample_offset=(5 + short_silence + i) * SAMPLES_PER_FRAME,
                    payload=create_loud_frame()
                )
                server._audio(pack(frame), streams)

            # Verify silence counter reset
            assert stream.endpoint_detector.silence_frame_count == 0
            assert stream.endpoint_triggered is False

            # Now send enough silence to trigger
            silence_frames_needed = int((650 / 1000) * SAMPLE_RATE / SAMPLES_PER_FRAME)
            for i in range(silence_frames_needed):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=0,
                    seq=5 + short_silence + 5 + i,
                    stream_id=stream_id,
                    sample_offset=(5 + short_silence + 5 + i) * SAMPLES_PER_FRAME,
                    payload=create_silence_frame()
                )
                server._audio(pack(frame), streams)

                if i == silence_frames_needed - 1:
                    await asyncio.sleep(0.01)
                    mock_finish.assert_called_once_with(stream)

    @pytest.mark.asyncio
    async def test_endpoint_prevents_duplicate_finalization(self, server):
        """Test that endpointing doesn't cause duplicate finalization with stop."""
        # Track calls to finalize
        finalize_calls = []
        original_finalize = server.finalize

        def tracked_finalize(stream, reason):
            finalize_calls.append((stream.stream_id, reason))
            return original_finalize(stream, reason)

        server.finalize = tracked_finalize

        with patch.object(server, '_finish_asr_worker', new_callable=AsyncMock) as mock_finish:
            streams = {}
            stream_id = 1
            device_id = "test-device"

            # Process hello and start
            hello = await server._control(AsyncMock(), '{"type":"hello","proto":1,"device_id":"test-device","sample_rate":16000,"codecs":["pcm_s16le"]}', None, streams)
            await server._control(AsyncMock(), '{"type":"start","stream_id":1,"codec":"pcm_s16le","sample_rate":16000,"channels":1,"frame_ms":20}', hello, streams)

            stream = streams[stream_id]

            # Send speech
            for _ in range(5):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=FLAG_FIRST if _ == 0 else 0,
                    seq=_,
                    stream_id=stream_id,
                    sample_offset=_ * SAMPLES_PER_FRAME,
                    payload=create_loud_frame()
                )
                server._audio(pack(frame), streams)

            # Trigger endpoint
            silence_frames_needed = int((650 / 1000) * SAMPLE_RATE / SAMPLES_PER_FRAME)
            for i in range(silence_frames_needed):
                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=0,
                    seq=5 + i,
                    stream_id=stream_id,
                    sample_offset=(5 + i) * SAMPLES_PER_FRAME,
                    payload=create_silence_frame()
                )
                server._audio(pack(frame), streams)

            # Give the background task a chance to start
            await asyncio.sleep(0)

            # Wait for the background task to complete by waiting for finalization
            # Give it up to 2 seconds to complete
            for _ in range(20):
                if stream.finalized:
                    break
                await asyncio.sleep(0.1)
            assert stream.finalized, "Stream was not finalized within timeout"

            # Verify endpoint triggered and finish called
            assert stream.endpoint_triggered is True
            mock_finish.assert_called_once()

            # Now try to stop the stream - should not cause duplicate finalization
            await server._control(AsyncMock(), '{"type":"stop","stream_id":1}', hello, streams)

            # finalize should only be called once (from endpoint)
            assert len(finalize_calls) == 1
            assert finalize_calls[0][1] == "endpoint"  # Reason should be "endpoint"

            # Stream should be marked as finalized
            assert stream.finalized is True

    def test_stream_initialization_sets_endpoint_detector_none(self):
        """Test that Stream initializes endpoint_detector as None."""
        stream = Stream(stream_id=1, device_id="test")
        assert stream.endpoint_detector is None
        assert stream.endpoint_triggered is False
        assert stream.finalized is False
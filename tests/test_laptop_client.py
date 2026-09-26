"""Unit tests for the laptop microphone client."""
import asyncio
import json
import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import numpy as np
import pytest

from tools.laptop_client import LaptopMicClient, SAMPLE_RATE, SAMPLES_PER_FRAME, PCM_PAYLOAD_LEN


@pytest.fixture(autouse=True)
def mock_vosk_model():
    """Isolate tests from disk/network Vosk model loading."""
    with patch("tools.laptop_client.vosk.Model"), \
         patch("tools.laptop_client.vosk.KaldiRecognizer"), \
         patch.object(LaptopMicClient, "_ensure_vosk_model"):
        yield


def test_laptop_client_initialization():
    """Client initializes with correct attributes."""
    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )
    assert client.host == "127.0.0.1"
    assert client.port == 8765
    assert client.keyword == "hey snail"
    assert client.output_dir == Path("/tmp")
    assert client.device_id.startswith("laptop-")
    assert client.stream_id is None
    assert not client.keyword_detected


def test_audio_frame_creation():
    """Test that audio frames are created with correct format."""
    from backend.protocol import AudioFrame, CODEC_PCM, FLAG_FIRST, pack

    payload = b"\x00\x00" * (PCM_PAYLOAD_LEN // 2)
    frame = AudioFrame(
        codec=CODEC_PCM,
        flags=FLAG_FIRST,
        seq=0,
        stream_id=1,
        sample_offset=0,
        payload=payload,
    )
    packed = pack(frame)
    # Unpack to verify
    from backend.protocol import unpack
    unpacked = unpack(packed)
    assert unpacked.codec == CODEC_PCM
    assert unpacked.flags == FLAG_FIRST
    assert unpacked.seq == 0
    assert unpacked.stream_id == 1
    assert unpacked.sample_offset == 0
    assert unpacked.payload == payload


@pytest.mark.asyncio
async def test_prebuffer_framing():
    """Test that prebuffer frames get correct flags."""
    from backend.protocol import FLAG_FIRST, FLAG_PREBUF, unpack

    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )
    dummy_frame = b"\x00\x00" * (PCM_PAYLOAD_LEN // 2)
    for _ in range(3):
        client.prebuffer.append(dummy_frame)

    sent_messages = []
    mock_ws = AsyncMock()
    mock_ws.send = AsyncMock(side_effect=lambda data: sent_messages.append(data))
    mock_ws.recv = AsyncMock(return_value=json.dumps({"type": "hello_ack", "proto": 1}))
    mock_ws.close = AsyncMock()

    with patch("websockets.connect", new_callable=AsyncMock, return_value=mock_ws), \
         patch.object(client, "save_wav", new_callable=AsyncMock):
        client.stop_event.set()
        await client.start_streaming()

    assert len(sent_messages) == 6
    assert json.loads(sent_messages[0])["type"] == "hello"
    assert json.loads(sent_messages[1])["type"] == "start"

    frame0 = unpack(sent_messages[2])
    frame1 = unpack(sent_messages[3])
    frame2 = unpack(sent_messages[4])

    assert frame0.flags == (FLAG_PREBUF | FLAG_FIRST)
    assert frame0.seq == 0
    assert frame1.flags == FLAG_PREBUF
    assert frame1.seq == 1
    assert frame2.flags == FLAG_PREBUF
    assert frame2.seq == 2


@pytest.mark.asyncio
async def test_keyword_detection_simulation():
    """Simulate audio input and verify keyword detection triggers."""
    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )
    with patch.object(client, 'vosk_recognizer') as mock_recognizer, \
         patch.object(client, 'start_streaming', new_callable=AsyncMock) as mock_start:
        mock_recognizer.AcceptWaveform.side_effect = [False, False, True]
        mock_recognizer.Result.return_value = '{"text": "hey snail", "confidence": 0.9}'
        mock_recognizer.PartialResult.return_value = '{"partial": ""}'

        async def mock_start_streaming():
            client.stop_event.set()

        mock_start.side_effect = mock_start_streaming

        # Feed three audio chunks using the thread-safe queue's put_nowait
        dummy_audio = b"\x00\x00" * PCM_PAYLOAD_LEN
        for _ in range(3):
            client.audio_queue.put_nowait(dummy_audio)

        task = asyncio.create_task(client.audio_processor())
        await asyncio.wait_for(task, timeout=1.0)

        # Check that keyword detection was triggered
        assert client.keyword_detected
        assert client.keyword_detected_time is not None
        # Check that start_streaming was called
        mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_non_keyword_does_not_trigger():
    """Simulate audio input with non-keyword speech and verify no trigger."""
    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )
    with patch.object(client, 'vosk_recognizer') as mock_recognizer, \
         patch.object(client, 'start_streaming', new_callable=AsyncMock) as mock_start:
        mock_recognizer.AcceptWaveform.side_effect = [False, False, False]
        mock_recognizer.Result.return_value = '{"text": "hello world", "confidence": 0.9}'
        mock_recognizer.PartialResult.return_value = '{"partial": ""}'

        dummy_audio = b"\x00\x00" * PCM_PAYLOAD_LEN
        for _ in range(3):
            client.audio_queue.put_nowait(dummy_audio)

        task = asyncio.create_task(client.audio_processor())
        while client.frames_processed < 3:
            await asyncio.sleep(0.01)
        client.stop()
        await asyncio.wait_for(task, timeout=1.0)

        # Check that keyword detection was NOT triggered
        assert not client.keyword_detected
        assert client.keyword_detected_time is None
        # Check that start_streaming was NOT called
        mock_start.assert_not_called()


@pytest.mark.asyncio
async def test_case_insensitive_keyword():
    """Simulate audio input with keyword in different case and verify trigger."""
    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )
    with patch.object(client, 'vosk_recognizer') as mock_recognizer, \
         patch.object(client, 'start_streaming', new_callable=AsyncMock) as mock_start:
        mock_recognizer.AcceptWaveform.side_effect = [False, False, True]
        mock_recognizer.Result.return_value = '{"text": "HEY SNAIL", "confidence": 0.9}'
        mock_recognizer.PartialResult.return_value = '{"partial": ""}'

        async def mock_start_streaming():
            client.stop_event.set()

        mock_start.side_effect = mock_start_streaming

        dummy_audio = b"\x00\x00" * PCM_PAYLOAD_LEN
        for _ in range(3):
            client.audio_queue.put_nowait(dummy_audio)

        task = asyncio.create_task(client.audio_processor())
        await asyncio.wait_for(task, timeout=1.0)

        # Check that keyword detection was triggered (case-insensitive)
        assert client.keyword_detected
        assert client.keyword_detected_time is not None
        # Check that start_streaming was called
        mock_start.assert_called_once()


@pytest.mark.asyncio
async def test_thread_safe_audio_queue():
    """Test that audio data from a thread-safe queue reaches the audio processor."""
    client = LaptopMicClient(
        host="127.0.0.1",
        port=8765,
        keyword="hey snail",
        output_dir=Path("/tmp"),
    )

    # Patch Vosk recognizer to simulate:
    # - First chunk: no keyword (partial result empty)
    # - Second chunk: keyword detected
    with patch.object(client, 'vosk_recognizer') as mock_recognizer, \
         patch.object(client, 'start_streaming', new_callable=AsyncMock) as mock_start:
        mock_recognizer.AcceptWaveform.side_effect = [False, True]  # First no, second yes
        mock_recognizer.Result.return_value = '{"text": "hey snail", "confidence": 0.9}'
        mock_recognizer.PartialResult.return_value = '{"partial": ""}'

        # Make the mock start_streaming set the stop event to simulate real behavior
        async def mock_start_streaming():
            client.stop_event.set()

        mock_start.side_effect = mock_start_streaming

        # Simulate putting audio data into the queue from a different thread
        # This mimics what the sounddevice callback does
        dummy_audio = b"\x00\x00" * PCM_PAYLOAD_LEN

        # Put TWO audio chunks into the thread-safe queue
        client.audio_queue.put_nowait(dummy_audio)  # First chunk - no keyword
        client.audio_queue.put_nowait(dummy_audio)  # Second chunk - has keyword

        # Start the audio processor task
        task = asyncio.create_task(client.audio_processor())

        # Wait for the task to finish cleanly
        await asyncio.wait_for(task, timeout=1.0)

        # Verify that the audio processor processed BOTH frames
        assert client.frames_processed == 2, f"Expected 2 frames processed, got {client.frames_processed}"
        # Verify that keyword detection was triggered (since we mocked Vosk to return keyword on second chunk)
        assert client.keyword_detected, "Keyword detection was not triggered"

        # Verify that start_streaming was called
        mock_start.assert_called_once()

        # Verify that the audio data was added to prebuffer
        # The first chunk should be in prebuffer, the second chunk triggered keyword detection
        # and was not added to prebuffer (because we only add if no keyword detected in that chunk)
        assert len(client.prebuffer) == 1, f"Expected 1 frame in prebuffer (first chunk), got {len(client.prebuffer)}"
        assert client.prebuffer[0] == dummy_audio, "Prebuffer contains incorrect audio data"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
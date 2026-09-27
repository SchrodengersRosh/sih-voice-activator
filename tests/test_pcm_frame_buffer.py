"""Tests for PCM frame normalization buffer (fixed 20 ms / 640 bytes @ 16 kHz mono s16le).

Hard requirements covered:
1. Exactly 640 bytes in -> 1 chunk of 640 bytes emitted.
2. 1280 bytes in -> 2 chunks of 640 bytes emitted.
3. 300 bytes + 340 bytes -> 1 chunk of 640 bytes on 2nd call (0 on first).
4. 100 + 100 + 440 bytes -> 1 chunk of 640 bytes on 3rd call (0 on first two).
5. 700 bytes -> 1 chunk of 640 bytes emitted, 60 bytes remaining in buffer.
6. Leftover 60 bytes + 580 bytes -> 1 chunk of 640 bytes emitted, 0 bytes remaining.
7. Two different streams have independent buffers.
8. Server _audio integration feeds ASR in normalized 640-byte chunks.
"""
from __future__ import annotations

from unittest.mock import MagicMock
import pytest

from backend.protocol import (
    CODEC_PCM,
    FLAG_FIRST,
    PCM_PAYLOAD_LEN,
    SAMPLES_PER_FRAME,
    AudioFrame,
    pack,
)
from backend.server import PCMFrameBuffer, Stream, VoiceServer


class TestPCMFrameBuffer:
    """Unit tests for PCMFrameBuffer chunk extraction and buffering."""

    def test_1_exact_640_bytes_produces_one_chunk(self):
        """1. Exactly 640 bytes in -> 1 chunk of 640 bytes."""
        buf = PCMFrameBuffer()
        data = b"\x01" * 640
        chunks = buf.feed(data)
        assert len(chunks) == 1
        assert len(chunks[0]) == 640
        assert chunks[0] == data
        assert buf.remaining_bytes == 0

    def test_2_1280_bytes_produces_two_chunks(self):
        """2. 1280 bytes in -> 2 chunks of 640 bytes."""
        buf = PCMFrameBuffer()
        chunk1 = b"\x01" * 640
        chunk2 = b"\x02" * 640
        data = chunk1 + chunk2
        assert len(data) == 1280
        chunks = buf.feed(data)
        assert len(chunks) == 2
        assert chunks[0] == chunk1
        assert chunks[1] == chunk2
        assert buf.remaining_bytes == 0

    def test_3_split_300_plus_340_produces_one_chunk_on_second_call(self):
        """3. 300 bytes + 340 bytes -> 1 chunk of 640 bytes on 2nd call."""
        buf = PCMFrameBuffer()
        part1 = b"\xaa" * 300
        part2 = b"\xbb" * 340

        chunks1 = buf.feed(part1)
        assert chunks1 == []
        assert buf.remaining_bytes == 300

        chunks2 = buf.feed(part2)
        assert len(chunks2) == 1
        assert len(chunks2[0]) == 640
        assert chunks2[0] == part1 + part2
        assert buf.remaining_bytes == 0

    def test_4_split_100_plus_100_plus_440_produces_one_chunk_on_third_call(self):
        """4. 100 + 100 + 440 bytes -> 1 chunk of 640 bytes on 3rd call."""
        buf = PCMFrameBuffer()
        p1 = b"\x11" * 100
        p2 = b"\x22" * 100
        p3 = b"\x33" * 440

        assert buf.feed(p1) == []
        assert buf.remaining_bytes == 100

        assert buf.feed(p2) == []
        assert buf.remaining_bytes == 200

        chunks = buf.feed(p3)
        assert len(chunks) == 1
        assert chunks[0] == p1 + p2 + p3
        assert len(chunks[0]) == 640
        assert buf.remaining_bytes == 0

    def test_5_700_bytes_emits_one_chunk_and_leaves_60_bytes(self):
        """5. 700 bytes -> 1 chunk of 640 bytes emitted, 60 bytes remaining in buffer."""
        buf = PCMFrameBuffer()
        expected_chunk = b"\x44" * 640
        leftover = b"\x55" * 60
        data = expected_chunk + leftover
        assert len(data) == 700

        chunks = buf.feed(data)
        assert len(chunks) == 1
        assert chunks[0] == expected_chunk
        assert buf.remaining_bytes == 60

    def test_6_leftover_60_plus_580_bytes_emits_one_chunk_zero_remaining(self):
        """6. Leftover 60 bytes + 580 bytes -> 1 chunk of 640 bytes, 0 bytes remaining."""
        buf = PCMFrameBuffer()
        # Seed with 700 bytes so 60 bytes remain
        part1_full = b"\x01" * 640
        part1_tail = b"\x02" * 60
        first_feed = buf.feed(part1_full + part1_tail)
        assert len(first_feed) == 1
        assert buf.remaining_bytes == 60

        # Feed 580 bytes to complete the second 640-byte chunk
        part2_add = b"\x03" * 580
        second_feed = buf.feed(part2_add)
        assert len(second_feed) == 1
        assert len(second_feed[0]) == 640
        assert second_feed[0] == part1_tail + part2_add
        assert buf.remaining_bytes == 0

    def test_7_independent_stream_buffers(self):
        """7. Two different streams have independent buffers."""
        stream1 = Stream(stream_id=1, device_id="esp32-1")
        stream2 = Stream(stream_id=2, device_id="esp32-2")

        # Buffers are distinct instances
        assert stream1.asr_frame_buffer is not stream2.asr_frame_buffer

        # Feed partial data to stream 1
        p1 = b"\xaa" * 300
        out1 = stream1.asr_frame_buffer.feed(p1)
        assert out1 == []
        assert stream1.asr_frame_buffer.remaining_bytes == 300
        assert stream2.asr_frame_buffer.remaining_bytes == 0

        # Feed partial data to stream 2
        p2 = b"\xbb" * 500
        out2 = stream2.asr_frame_buffer.feed(p2)
        assert out2 == []
        assert stream1.asr_frame_buffer.remaining_bytes == 300
        assert stream2.asr_frame_buffer.remaining_bytes == 500

        # Completing stream 1 does not affect stream 2
        p1_rest = b"\xcc" * 340
        out1_rest = stream1.asr_frame_buffer.feed(p1_rest)
        assert len(out1_rest) == 1
        assert out1_rest[0] == p1 + p1_rest
        assert stream1.asr_frame_buffer.remaining_bytes == 0
        assert stream2.asr_frame_buffer.remaining_bytes == 500

        # Completing stream 2 does not affect stream 1
        p2_rest = b"\xdd" * 140
        out2_rest = stream2.asr_frame_buffer.feed(p2_rest)
        assert len(out2_rest) == 1
        assert out2_rest[0] == p2 + p2_rest
        assert stream1.asr_frame_buffer.remaining_bytes == 0
        assert stream2.asr_frame_buffer.remaining_bytes == 0

    def test_8_server_audio_integration_feeds_asr_normalized_640_byte_chunks(self, tmp_path):
        """8. Server _audio integration feeds ASR in normalized 640-byte chunks."""
        server = VoiceServer(tmp_path)
        streams: dict[int, Stream] = {}
        stream_id = 42
        stream = Stream(stream_id=stream_id, device_id="edgeai-esp32s3")
        streams[stream_id] = stream

        # Mock ASR worker to capture chunks passed to add_audio
        mock_asr_worker = MagicMock()
        stream.asr_worker = mock_asr_worker
        stream.asr_thread_started = True

        # 1. Send first frame (FLAG_FIRST) with exact 640 bytes payload
        payload1 = b"\x12" * PCM_PAYLOAD_LEN
        frame1 = AudioFrame(
            codec=CODEC_PCM,
            flags=FLAG_FIRST,
            seq=0,
            stream_id=stream_id,
            sample_offset=0,
            payload=payload1,
        )
        server._audio(pack(frame1), streams)

        assert mock_asr_worker.add_audio.call_count == 1
        assert mock_asr_worker.add_audio.call_args_list[0][0][0] == payload1
        assert len(mock_asr_worker.add_audio.call_args_list[0][0][0]) == 640
        assert stream.asr_frame_buffer.remaining_bytes == 0

        # 2. Send second frame (seq=1) with exact 640 bytes payload
        payload2 = b"\x34" * PCM_PAYLOAD_LEN
        frame2 = AudioFrame(
            codec=CODEC_PCM,
            flags=0,
            seq=1,
            stream_id=stream_id,
            sample_offset=SAMPLES_PER_FRAME,
            payload=payload2,
        )
        server._audio(pack(frame2), streams)

        assert mock_asr_worker.add_audio.call_count == 2
        assert mock_asr_worker.add_audio.call_args_list[1][0][0] == payload2
        assert len(mock_asr_worker.add_audio.call_args_list[1][0][0]) == 640
        assert stream.asr_frame_buffer.remaining_bytes == 0

        # 3. Simulate leftover accumulation and feed in stream buffer
        # Prime buffer with 200 bytes unaligned
        stream.asr_frame_buffer.feed(b"\x99" * 200)
        assert stream.asr_frame_buffer.remaining_bytes == 200
        calls_before = mock_asr_worker.add_audio.call_count

        # Send third frame (640 bytes). The buffer now has 200 + 640 = 840 bytes.
        # It must emit exactly 1 normalized frame of 640 bytes to ASR worker, leaving 200 bytes.
        payload3 = b"\x56" * PCM_PAYLOAD_LEN
        frame3 = AudioFrame(
            codec=CODEC_PCM,
            flags=0,
            seq=2,
            stream_id=stream_id,
            sample_offset=SAMPLES_PER_FRAME * 2,
            payload=payload3,
        )
        server._audio(pack(frame3), streams)

        assert mock_asr_worker.add_audio.call_count == calls_before + 1
        emitted_chunk = mock_asr_worker.add_audio.call_args_list[-1][0][0]
        assert len(emitted_chunk) == 640
        assert emitted_chunk == (b"\x99" * 200) + payload3[:440]
        assert stream.asr_frame_buffer.remaining_bytes == 200

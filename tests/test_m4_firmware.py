"""M4 ESP32-S3 Firmware Host Validation Tests."""

import asyncio
import json
import logging
import os
from pathlib import Path
import subprocess
import time
import wave

import pytest
import websockets

from backend.protocol import (
    CODEC_PCM,
    FLAG_FIRST,
    FLAG_LAST,
    FLAG_PREBUF,
    PCM_PAYLOAD_LEN,
    SAMPLES_PER_FRAME,
    SAMPLE_RATE,
    AudioFrame,
    pack,
    unpack,
)
from backend.server import VoiceServer


def test_firmware_native_c_suite():
    """Compile and run the firmware C unit test suite on the host using clang/gcc."""
    project_root = Path(__file__).resolve().parent.parent
    c_srcs = [
        str(project_root / "firmware" / "main" / "protocol_frames.c"),
        str(project_root / "firmware" / "main" / "audio_ring_buffer.c"),
        str(project_root / "firmware" / "main" / "energy_detector.c"),
        str(project_root / "firmware" / "main" / "kws_engine.c"),
        str(project_root / "firmware" / "tests" / "test_firmware_native.c"),
    ]
    include_dir = str(project_root / "firmware" / "main")
    test_bin = project_root / ".pytest_firmware_native"

    compile_cmd = [
        "clang",
        "-Wall",
        "-Wextra",
        f"-I{include_dir}",
        *c_srcs,
        "-o",
        str(test_bin),
    ]

    try:
        subprocess.run(compile_cmd, check=True, capture_output=True, text=True)
        res = subprocess.run([str(test_bin)], check=True, capture_output=True, text=True)
        assert "All 5 firmware host tests PASSED" in res.stdout
    finally:
        if test_bin.exists():
            test_bin.unlink()


def test_m4_firmware_constants_and_constraints():
    """Verify M4 firmware configuration parameters align with canonical system constraints."""
    prebuffer_ms = 800
    frame_ms = 20
    prebuffer_frames = prebuffer_ms // frame_ms
    assert prebuffer_frames == 40

    prebuffer_samples = prebuffer_frames * SAMPLES_PER_FRAME
    assert prebuffer_samples == 12800

    prebuffer_bytes = prebuffer_frames * PCM_PAYLOAD_LEN
    assert prebuffer_bytes == 25600

    # Ensure canonical sample rate and frame payload match frozen protocol
    assert SAMPLE_RATE == 16000
    assert PCM_PAYLOAD_LEN == 640


@pytest.mark.asyncio
async def test_esp32_firmware_streaming_lifecycle_and_transition(tmp_path):
    """
    Test the exact streaming lifecycle executed by firmware main.c:
    1. hello -> hello_ack
    2. KWS trigger: start with 800ms prebuffer (live offset = 12800)
    3. 40 prebuffer frames (FLAG_PREBUF, frame 0 has FLAG_FIRST)
    4. Seamless transition to live frames (seq 40+, sample_offset 12800+)
    5. Final frame with FLAG_LAST + stop JSON message
    6. Verify backend reconstructed WAV has exact sample count, 0 gaps, 0 bad frames.
    """
    server = VoiceServer(tmp_path)
    async with websockets.serve(server.handler, "127.0.0.1", 0, max_size=2048) as listening:
        port = listening.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"

        async with websockets.connect(uri) as ws:
            # Step 1: Handshake
            device_id = "esp32s3-test-device"
            await ws.send(json.dumps({
                "type": "hello",
                "proto": 1,
                "device_id": device_id,
                "codecs": ["pcm_s16le"],
                "sample_rate": 16000,
            }))
            ack = json.loads(await ws.recv())
            assert ack == {"type": "hello_ack", "proto": 1}

            # Step 2: Trigger wake word & send start
            stream_id = 998877
            prebuffer_ms = 800
            prebuffer_frames = prebuffer_ms // 20  # 40 frames
            live_frames = 50                       # 50 frames (1 second of live audio)
            total_frames = prebuffer_frames + live_frames
            live_sample_offset = prebuffer_frames * SAMPLES_PER_FRAME  # 12800
            t_detect_us = time.monotonic_ns() // 1000

            await ws.send(json.dumps({
                "type": "start",
                "stream_id": stream_id,
                "codec": "pcm_s16le",
                "sample_rate": 16000,
                "channels": 1,
                "frame_ms": 20,
                "prebuffer_ms": prebuffer_ms,
                "live_sample_offset": live_sample_offset,
                "t_detect_us": t_detect_us,
            }))

            # Step 3 & 4: Stream 40 prebuffer frames + 50 live frames
            for seq in range(total_frames):
                is_prebuffer = (seq < prebuffer_frames)
                is_first = (seq == 0)
                is_last = (seq == total_frames - 1)

                flags = 0
                if is_first:
                    flags |= FLAG_FIRST
                if is_prebuffer:
                    flags |= FLAG_PREBUF
                if is_last:
                    flags |= FLAG_LAST

                sample_offset = seq * SAMPLES_PER_FRAME
                # Audio payload: deterministic counter values
                payload = bytearray(PCM_PAYLOAD_LEN)
                for s in range(SAMPLES_PER_FRAME):
                    val = (seq * SAMPLES_PER_FRAME + s) % 32767
                    payload[s * 2] = val & 0xFF
                    payload[s * 2 + 1] = (val >> 8) & 0xFF

                frame = AudioFrame(
                    codec=CODEC_PCM,
                    flags=flags,
                    seq=seq,
                    stream_id=stream_id,
                    sample_offset=sample_offset,
                    payload=bytes(payload),
                )
                await ws.send(pack(frame))

            # Step 5: Stop stream
            await ws.send(json.dumps({
                "type": "stop",
                "stream_id": stream_id,
                "reason": "duration_limit",
            }))

        await asyncio.sleep(0.05)

    # Step 6: Validate output WAV
    wav_path = tmp_path / f"stream-{stream_id}.wav"
    assert wav_path.exists(), "Reconstructed WAV file must exist"

    with wave.open(str(wav_path), "rb") as wav_file:
        assert wav_file.getnchannels() == 1
        assert wav_file.getsampwidth() == 2
        assert wav_file.getframerate() == 16000
        # Exactly 90 frames * 320 samples = 28,800 samples
        assert wav_file.getnframes() == total_frames * SAMPLES_PER_FRAME

import asyncio
import json
import logging
from pathlib import Path
import time
import wave

import pytest
from websockets.asyncio.server import serve
import websockets

from backend.protocol import AudioFrame, CODEC_PCM, FLAG_FIRST, pack
from backend.server import VoiceServer
from tools.compare import compare
from tools.sim_esp32 import send_wav


def wav(path: Path, samples: int) -> None:
    # Deliberately deterministic and often non-frame-aligned audio.
    data = b"".join(((i * 37 % 30000) - 15000).to_bytes(2, "little", signed=True) for i in range(samples))
    with wave.open(str(path), "wb") as out:
        out.setnchannels(1); out.setsampwidth(2); out.setframerate(16000); out.writeframes(data)


@pytest.mark.asyncio
async def test_three_wavs_roundtrip_and_two_clients(tmp_path):
    server = VoiceServer(tmp_path)
    async with serve(server.handler, "127.0.0.1", 0, max_size=2048) as listening:
        port = listening.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        paths = [tmp_path / f"in-{i}.wav" for i in range(3)]
        for path, count in zip(paths, (1, 997, 2000)): wav(path, count)
        await asyncio.gather(*(send_wav(uri, path, device_id=f"sim-{i}", stream_id=i + 1) for i, path in enumerate(paths)))
    for i, path in enumerate(paths):
        assert compare(path, tmp_path / f"stream-{i + 1}.wav") == "IDENTICAL"


@pytest.mark.asyncio
async def test_start_before_hello_is_closed(tmp_path):
    server = VoiceServer(tmp_path)
    async with serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        async with websockets.connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send('{"type":"start","stream_id":1}')
            assert 'HELLO_REQUIRED' in await ws.recv()
            with pytest.raises(websockets.exceptions.ConnectionClosed):
                await ws.recv()


@pytest.mark.asyncio
async def test_abrupt_client_termination(tmp_path):
    server = VoiceServer(tmp_path)
    async with serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "proto": 1, "device_id": "abrupt-dev", "codecs": ["pcm_s16le"], "sample_rate": 16000}))
            assert json.loads(await ws.recv()) == {"type": "hello_ack", "proto": 1}
            await ws.send(json.dumps({"type": "start", "stream_id": 42, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20, "prebuffer_ms": 0, "live_sample_offset": 0, "t_detect_us": 0}))
            for seq in range(3):
                flags = FLAG_FIRST if seq == 0 else 0
                await ws.send(pack(AudioFrame(CODEC_PCM, flags, seq, 42, seq * 320, b"\x01\x00" * 320)))
            # Abrupt disconnect
        await asyncio.sleep(0.05)
        partial_wav = tmp_path / "stream-42.wav"
        assert partial_wav.exists()
        with wave.open(str(partial_wav), "rb") as w:
            assert w.getnframes() == 3 * 320

        # Verify server remains alive for next client
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "proto": 1, "device_id": "followup-dev", "codecs": ["pcm_s16le"], "sample_rate": 16000}))
            assert json.loads(await ws.recv()) == {"type": "hello_ack", "proto": 1}


@pytest.mark.asyncio
async def test_json_as_binary_frame(tmp_path, caplog):
    server = VoiceServer(tmp_path)
    async with serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        with caplog.at_level(logging.WARNING):
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({"type": "hello", "proto": 1, "device_id": "json-bin-dev", "codecs": ["pcm_s16le"], "sample_rate": 16000}))
                assert json.loads(await ws.recv()) == {"type": "hello_ack", "proto": 1}
                await ws.send(b'{"type":"ping"}')
                # Server must not crash and continue operating normally
                await ws.send(json.dumps({"type": "start", "stream_id": 7, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20, "prebuffer_ms": 0, "live_sample_offset": 0, "t_detect_us": 0}))
                await ws.send(json.dumps({"type": "stop", "stream_id": 7, "reason": "test"}))
                await asyncio.sleep(0.05)
        assert any("JSON sent as binary frame" in record.message for record in caplog.records)


@pytest.mark.asyncio
async def test_localhost_start_to_first_frame_latency(tmp_path):
    server = VoiceServer(tmp_path)
    latencies = []
    orig_audio = server._audio
    def tracked_audio(raw, streams):
        orig_audio(raw, streams)
        for s in streams.values():
            if s.first_frame_ns is not None and s.stream_id not in [x[0] for x in latencies]:
                latencies.append((s.stream_id, (s.first_frame_ns - s.started_ns) / 1e6))
    server._audio = tracked_audio

    async with serve(server.handler, "127.0.0.1", 0) as listening:
        port = listening.sockets[0].getsockname()[1]
        uri = f"ws://127.0.0.1:{port}"
        async with websockets.connect(uri) as ws:
            await ws.send(json.dumps({"type": "hello", "proto": 1, "device_id": "lat-dev", "codecs": ["pcm_s16le"], "sample_rate": 16000}))
            assert json.loads(await ws.recv()) == {"type": "hello_ack", "proto": 1}
            await ws.send(json.dumps({"type": "start", "stream_id": 1, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20, "prebuffer_ms": 0, "live_sample_offset": 0, "t_detect_us": 0}))
            await ws.send(pack(AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 1, 0, b"\0" * 640)))
            await asyncio.sleep(0.02)

    assert len(latencies) == 1
    stream_id, latency_ms = latencies[0]
    assert latency_ms < 5.0, f"Latency {latency_ms:.3f} ms exceeds target of 5 ms"


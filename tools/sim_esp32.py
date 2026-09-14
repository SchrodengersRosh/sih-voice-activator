"""Reference M1 client: WAV -> frozen protocol -> WebSocket."""
from __future__ import annotations
import argparse
import asyncio
import json
from pathlib import Path
import time
import wave

import websockets

from backend.protocol import CODEC_PCM, FLAG_FIRST, FLAG_LAST, FLAG_PREBUF, PCM_PAYLOAD_LEN, SAMPLES_PER_FRAME, AudioFrame, pack


def read_pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError("input WAV must be uncompressed mono 16-bit 16 kHz PCM")
        pcm = source.readframes(source.getnframes())
    remainder = len(pcm) % PCM_PAYLOAD_LEN
    return pcm if not remainder else pcm + b"\0" * (PCM_PAYLOAD_LEN - remainder)


async def send_wav(uri: str, wav_path: Path, *, device_id: str = "sim-01", stream_id: int = 1, prebuffer_ms: int = 800) -> None:
    pcm = read_pcm(wav_path)
    frames = [pcm[i:i + PCM_PAYLOAD_LEN] for i in range(0, len(pcm), PCM_PAYLOAD_LEN)]
    pre_frames = min(len(frames), prebuffer_ms // 20)
    async with websockets.connect(uri, max_size=2048, ping_interval=20, ping_timeout=20) as ws:
        await ws.send(json.dumps({"type": "hello", "proto": 1, "device_id": device_id, "codecs": ["pcm_s16le"], "sample_rate": 16000}))
        ack = json.loads(await ws.recv())
        if ack != {"type": "hello_ack", "proto": 1}:
            raise RuntimeError(f"unexpected hello response: {ack}")
        await ws.send(json.dumps({"type": "start", "stream_id": stream_id, "codec": "pcm_s16le", "sample_rate": 16000, "channels": 1, "frame_ms": 20, "prebuffer_ms": prebuffer_ms, "live_sample_offset": pre_frames * SAMPLES_PER_FRAME, "t_detect_us": time.monotonic_ns() // 1000}))
        for seq, payload in enumerate(frames):
            flags = (FLAG_FIRST if seq == 0 else 0) | (FLAG_LAST if seq == len(frames) - 1 else 0)
            if seq < pre_frames:
                flags |= FLAG_PREBUF
            await ws.send(pack(AudioFrame(CODEC_PCM, flags, seq, stream_id, seq * SAMPLES_PER_FRAME, payload)))
            if seq >= pre_frames:
                await asyncio.sleep(0.020)
        await ws.send(json.dumps({"type": "stop", "stream_id": stream_id, "reason": "eof"}))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wav", type=Path)
    parser.add_argument("--uri", default="ws://127.0.0.1:8765")
    parser.add_argument("--device-id", default="sim-01")
    parser.add_argument("--stream-id", type=int, default=1)
    parser.add_argument("--prebuffer-ms", type=int, default=800)
    args = parser.parse_args()
    asyncio.run(send_wav(args.uri, args.wav, device_id=args.device_id, stream_id=args.stream_id, prebuffer_ms=args.prebuffer_ms))


if __name__ == "__main__":
    main()

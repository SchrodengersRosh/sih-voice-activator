#!/usr/bin/env python3
"""
Laptop microphone client for end-to-end voice activation validation.
Captures microphone audio, performs keyword detection, and streams to the backend
using the frozen v1.0 protocol.

This is a development/validation tool only; not intended for final embedded deployment.
"""

import argparse
import asyncio
import json
import logging
import os
import signal
import sys
import tarfile
import time
import wave
from collections import deque
from datetime import datetime
from pathlib import Path
from typing import Any, Deque, List, Optional
import urllib.request

import numpy as np
import sounddevice as sd
import vosk
import websockets
import queue
from backend.protocol import (
    AudioFrame,
    CODEC_PCM,
    FLAG_FIRST,
    FLAG_LAST,
    FLAG_PREBUF,
    PCM_PAYLOAD_LEN,
    SAMPLES_PER_FRAME,
    SAMPLE_RATE,
    pack,
    unpack,
)

# Audio configuration
CHANNELS = 1
DTYPE = 'int16'  # signed 16-bit
SAMPLE_RATE = 16000  # must match protocol
FRAME_MS = 20
FRAME_SIZE = SAMPLES_PER_FRAME  # 320 samples
PREBUFFER_MS = 800
PREBUFFER_FRAMES = PREBUFFER_MS // FRAME_MS  # 40 frames
PREBUFFER_SAMPLES = PREBUFFER_FRAMES * FRAME_SIZE
MAX_STREAM_SECONDS = 10  # from backend constants
MAX_GAP_SAMPLES = SAMPLE_RATE * 2  # from backend

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s %(levelname)s %(message)s',
    datefmt='%H:%M:%S',
)
LOG = logging.getLogger(__name__)


class LaptopMicClient:
    def __init__(
        self,
        host: str,
        port: int,
        keyword: str,
        output_dir: Path,
        vosk_model_path: Optional[str] = None,
    ):
        self.host = host
        self.port = port
        self.keyword = keyword.lower()
        self.output_dir = output_dir
        self.output_dir.mkdir(parents=True, exist_ok=True)

        # Vosk setup for keyword spotting
        if vosk_model_path is None:
            vosk_model_path = "vosk-model-small-en-us-0.15"
        self.vosk_model_path = vosk_model_path
        self._ensure_vosk_model()
        LOG.info(f"Loading Vosk model from {self.vosk_model_path}")
        self.vosk_model = vosk.Model(self.vosk_model_path)
        # Create a recognizer for unrestricted recognition
        self.vosk_recognizer = vosk.KaldiRecognizer(self.vosk_model, SAMPLE_RATE)
        self.vosk_recognizer.SetWords(True)

        # Audio buffers
        self.prebuffer: Deque[bytes] = deque(maxlen=PREBUFFER_FRAMES)  # store frames for prebuffer
        self.audio_queue: queue.Queue = queue.Queue()  # raw audio bytes from callback
        self.stream_id: Optional[int] = None
        self.device_id: str = f"laptop-{os.getpid()}"
        self.websocket: Optional[Any] = None

        # Timing
        self.keyword_detected_time: Optional[float] = None
        self.stream_start_time: Optional[float] = None
        self.stream_end_time: Optional[float] = None

        # Control
        self.running = True
        self.stop_event = asyncio.Event()
        self.streaming_event = asyncio.Event()  # set when we are actively streaming

        # Tasks
        self._audio_task: Optional[asyncio.Task] = None
        self._stream_task: Optional[asyncio.Task] = None

        # Stats
        self.frames_processed = 0
        self.keyword_detected = False

    def _ensure_vosk_model(self):
        """Download and extract Vosk small English model if not present."""
        model_dir = Path(self.vosk_model_path)
        if model_dir.exists():
            return
        LOG.info(f"Vosk model not found at {model_dir}. Downloading...")
        model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        zip_path = model_dir.with_suffix('.zip')
        try:
            urllib.request.urlretrieve(model_url, zip_path)
            LOG.info("Download complete. Extracting...")
            with tarfile.open(zip_path, "r:zip") as tar:
                tar.extractall(path=model_dir.parent)
            zip_path.unlink()  # remove zip
            LOG.info(f"Model extracted to {model_dir}")
        except Exception as e:
            LOG.exception(f"Failed to download Vosk model: {e}")
            raise

    def _get_audio_chunk(self, timeout: float = 0.1) -> Optional[bytes]:
        """Thread-safe retrieval of an audio chunk from the queue with timeout."""
        if not hasattr(self, '_get_audio_chunk_count'):
            self._get_audio_chunk_count = 0
        self._get_audio_chunk_count += 1
        try:
            audio_bytes = self.audio_queue.get(timeout=timeout)
            if self._get_audio_chunk_count % 50 == 0:
                LOG.info(f"_get_audio_chunk: count={self._get_audio_chunk_count}, got {len(audio_bytes) if audio_bytes is not None else 0} bytes")
            return audio_bytes
        except queue.Empty:
            if self._get_audio_chunk_count % 50 == 0:
                LOG.info(f"_get_audio_chunk: count={self._get_audio_chunk_count}, timeout (no audio)")
            return None

    def audio_callback(self, indata, frames, time_info, status):
        """Callback from sounddevice.InputStream."""
        if status:
            LOG.warning(f"Audio callback status: {status}")
        audio_bytes = indata.tobytes()
        # Enforce exact PCM_PAYLOAD_LEN chunks
        if len(audio_bytes) == PCM_PAYLOAD_LEN:
            self.audio_queue.put_nowait(audio_bytes)
        else:
            for i in range(0, len(audio_bytes), PCM_PAYLOAD_LEN):
                chunk = audio_bytes[i:i + PCM_PAYLOAD_LEN]
                if len(chunk) == PCM_PAYLOAD_LEN:
                    self.audio_queue.put_nowait(chunk)
        # Throttled diagnostic logging for audio callback
        if not hasattr(self, '_callback_count'):
            self._callback_count = 0
        self._callback_count += 1
        if self._callback_count % 50 == 0:
            queue_size = self.audio_queue.qsize() if hasattr(self.audio_queue, 'qsize') else 'unknown'
            LOG.info(f"Audio callback: count={self._callback_count}, frames={frames}, "
                     f"bytes={len(audio_bytes)}, queue_size={queue_size}")

    async def audio_processor(self):
        """Process audio chunks from the queue for keyword detection."""
        while self.running and not self.stop_event.is_set():
            try:
                # Get audio bytes from the thread-safe queue with bounded wait
                audio_bytes = await asyncio.to_thread(self._get_audio_chunk, 0.1)
                if audio_bytes is None:
                    continue
                self.frames_processed += 1
                # Throttled log for audio processor frame count
                if self.frames_processed % 50 == 0:
                    LOG.info(f"Audio processor received {self.frames_processed} frames")
                # Convert bytes to numpy array for Vosk (expects 16-bit little-endian)
                audio_np = np.frombuffer(audio_bytes, dtype=np.int16)
                # Feed to Vosk recognizer
                if self.vosk_recognizer.AcceptWaveform(audio_bytes):
                    result = json.loads(self.vosk_recognizer.Result())
                    text = result.get("text", "").lower()
                else:
                    result = json.loads(self.vosk_recognizer.PartialResult())
                    text = result.get("partial", "").lower()
                if text:
                    LOG.info(f"Vosk recognition: {text!r}")
                # Check if keyword is in the recognized text
                if self.keyword in text and not self.keyword_detected:
                    self.keyword_detected = True
                    self.keyword_detected_time = time.time()
                    LOG.info(f"Keyword '{self.keyword}' detected at {self.keyword_detected_time:.3f}")
                    # Trigger streaming
                    await self.start_streaming()
                else:
                    # Add to prebuffer only if we haven't detected the keyword in this chunk
                    self.prebuffer.append(audio_bytes)
            except asyncio.CancelledError:
                break
            except Exception as e:
                LOG.exception(f"Error in audio processor: {e}")

    async def start_streaming(self):
        """Start streaming prebuffer + live audio to backend."""
        # Cancel audio processor task to avoid double consumption if called externally
        current = asyncio.current_task()
        if self._audio_task and self._audio_task is not current and not self._audio_task.done():
            self._audio_task.cancel()
            try:
                await self._audio_task
            except asyncio.CancelledError:
                pass
            self._audio_task = None

        if self.stream_id is not None:
            # Already streaming
            return
        # Generate a random stream ID
        self.stream_id = int.from_bytes(os.urandom(4), byteorder='big') & 0xFFFFFFFF
        self.stream_start_time = time.time()
        LOG.info(f"Starting stream {self.stream_id} for device {self.device_id}")

        # Connect to backend
        uri = f"ws://{self.host}:{self.port}"
        try:
            self.websocket = await websockets.connect(uri, max_size=2048)
        except Exception as e:
            LOG.error(f"Failed to connect to backend {uri}: {e}")
            self.stop_event.set()
            return

        # Send hello
        hello_msg = {
            "type": "hello",
            "proto": 1,
            "device_id": self.device_id,
            "codecs": ["pcm_s16le"],
            "sample_rate": SAMPLE_RATE,
        }
        await self.websocket.send(json.dumps(hello_msg))
        response = await self.websocket.recv()
        hello_ack = json.loads(response)
        if hello_ack != {"type": "hello_ack", "proto": 1}:
            LOG.error(f"Unexpected hello response: {hello_ack}")
            await self.websocket.close()
            self.websocket = None
            self.stop_event.set()
            return
        LOG.debug("Received hello_ack")

        # Send start
        start_msg = {
            "type": "start",
            "stream_id": self.stream_id,
            "codec": "pcm_s16le",
            "sample_rate": SAMPLE_RATE,
            "channels": CHANNELS,
            "frame_ms": FRAME_MS,
            "prebuffer_ms": PREBUFFER_MS,
            "live_sample_offset": PREBUFFER_SAMPLES,  # we will send prebuffer first
            "t_detect_us": int(self.keyword_detected_time * 1_000_000) if self.keyword_detected else 0,
        }
        await self.websocket.send(json.dumps(start_msg))

        # Initialize list to hold audio for WAV (we'll stream and record simultaneously)
        self.stream_frames: List[bytes] = []
        # Add prebuffer frames to stream_frames
        self.stream_frames.extend(list(self.prebuffer))

        # Stream prebuffer frames
        LOG.info(f"Streaming {len(self.prebuffer)} prebuffer frames")
        for i, frame_bytes in enumerate(self.prebuffer):
            # Create frame with appropriate flags
            flags = FLAG_PREBUF
            if i == 0:
                flags |= FLAG_FIRST
            # Compute sample offset: each frame is 320 samples.
            sample_offset = i * SAMPLES_PER_FRAME
            frame = AudioFrame(
                codec=CODEC_PCM,
                flags=flags,
                seq=i,
                stream_id=self.stream_id,
                sample_offset=sample_offset,
                payload=frame_bytes,
            )
            packed = pack(frame)
            await self.websocket.send(packed)
            await asyncio.sleep(FRAME_MS / 1000.0)  # simulate real-time

        # Now stream live audio until stop condition
        LOG.info("Switching to live audio streaming")
        live_seq = len(self.prebuffer)
        while self.running and not self.stop_event.is_set() and not self.streaming_event.is_set():
            audio_bytes = await asyncio.to_thread(self._get_audio_chunk, 1.0)
            if audio_bytes is None:
                # No audio for 1 second, maybe stop due to inactivity
                continue
            # Create frame
            flags = 0
            # Live frames: no PREBUF, no FIRST unless it's the very first frame (already handled)
            sample_offset = live_seq * SAMPLES_PER_FRAME
            frame = AudioFrame(
                codec=CODEC_PCM,
                flags=flags,
                seq=live_seq,
                stream_id=self.stream_id,
                sample_offset=sample_offset,
                payload=audio_bytes,
            )
            packed = pack(frame)
            await self.websocket.send(packed)
            self.stream_frames.append(audio_bytes)
            live_seq += 1
            await asyncio.sleep(FRAME_MS / 1000.0)

            # Stop condition: we could stop after a maximum duration or when we detect silence.
            # For simplicity, we'll stop after MAX_STREAM_SECONDS from start.
            if time.time() - self.stream_start_time > MAX_STREAM_SECONDS:
                LOG.info("Maximum stream duration reached")
                break

        # Send stop
        stop_msg = {
            "type": "stop",
            "stream_id": self.stream_id,
            "reason": "completed",
        }
        await self.websocket.send(json.dumps(stop_msg))

        # Close connection
        await self.websocket.close()
        self.websocket = None
        self.stream_end_time = time.time()
        LOG.info(f"Stream {self.stream_id} ended at {self.stream_end_time:.3f}")

        # Save the captured audio as WAV
        await self.save_wav()

        # Mark streaming as done
        self.streaming_event.set()

    async def save_wav(self):
        """Save prebuffer + live audio captured during streaming to a WAV file."""
        if not hasattr(self, 'stream_frames') or not self.stream_frames:
            LOG.warning("No audio frames to save for WAV")
            return
        # Concatenate all frames
        audio_data = b''.join(self.stream_frames)
        # Generate filename with timestamp
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        wav_filename = self.output_dir / f"stream-{self.stream_id}_{timestamp}.wav"
        try:
            with wave.open(str(wav_filename), 'wb') as wf:
                wf.setnchannels(CHANNELS)
                wf.setsampwidth(2)  # 16-bit = 2 bytes
                wf.setframerate(SAMPLE_RATE)
                wf.writeframes(audio_data)
            LOG.info(f"Saved stream audio to {wav_filename} ({len(audio_data)} bytes)")
        except Exception as e:
            LOG.exception(f"Failed to write WAV file: {e}")

    async def run(self):
        """Main run loop."""
        # Start audio input stream
        LOG.info("Starting microphone input")
        try:
            with sd.InputStream(
                samplerate=SAMPLE_RATE,
                channels=CHANNELS,
                dtype=DTYPE,
                blocksize=FRAME_SIZE,
                callback=self.audio_callback,
            ):
                # Start audio processor task
                self._audio_task = asyncio.create_task(self.audio_processor())
                # Wait for stop event
                await self.stop_event.wait()
                # Cancel tasks
                if self._audio_task and not self._audio_task.done():
                    self._audio_task.cancel()
                if self._stream_task and not self._stream_task.done():
                    self._stream_task.cancel()
                # Wait for tasks to finish
                if self._audio_task:
                    try:
                        await self._audio_task
                    except asyncio.CancelledError:
                        pass
                if self._stream_task:
                    try:
                        await self._stream_task
                    except asyncio.CancelledError:
                        pass
        except Exception as e:
            LOG.exception(f"Error in audio input: {e}")
        finally:
            self.running = False
            if self.websocket:
                try:
                    await self.websocket.close()
                except Exception:
                    pass
                self.websocket = None
            LOG.info("Client shutting down")

    def stop(self):
        """Stop the client."""
        self.running = False
        self.stop_event.set()


def main():
    parser = argparse.ArgumentParser(description="Laptop microphone client for SIH voice activation")
    parser.add_argument("--host", default="127.0.0.1", help="Backend host")
    parser.add_argument("--port", type=int, default=8765, help="Backend port")
    parser.add_argument("--keyword", default="sentinel", help="Keyword to detect (lowercase)")
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("received_laptop"),
        help="Directory to save triggered audio WAV files",
    )
    parser.add_argument(
        "--vosk-model",
        type=str,
        default=None,
        help="Path to Vosk model directory (default: vosk-model-small-en-us-0.15)",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    if args.debug:
        LOG.setLevel(logging.DEBUG)

    client = LaptopMicClient(
        host=args.host,
        port=args.port,
        keyword=args.keyword,
        output_dir=args.output_dir,
        vosk_model_path=args.vosk_model,
    )

    # Handle SIGINT for graceful shutdown
    def signal_handler(sig, frame):
        LOG.info("Received interrupt signal")
        client.stop()
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    try:
        asyncio.run(client.run())
    except KeyboardInterrupt:
        pass
    finally:
        LOG.info("Client terminated")


if __name__ == "__main__":
    main()
"""Focused unit tests for ASRWorker lifecycle, especially finish() finalization.

These tests use the real Vosk model to verify actual ASR behavior.
They do NOT test server.py integration — only the worker itself.
"""
from __future__ import annotations

import struct
import time

import pytest
import vosk

from backend.asr.worker import ASRResult, ASRWorker
from backend.protocol import SAMPLE_RATE, SAMPLES_PER_FRAME, PCM_PAYLOAD_LEN


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _load_shared_model() -> vosk.Model:
    """Load the shared Vosk model (cached on disk)."""
    from pathlib import Path
    model_dir = Path.home() / ".cache" / "vosk" / "vosk-model-small-en-us-0.15"
    if not model_dir.is_dir():
        pytest.skip("Vosk model not available at ~/.cache/vosk/vosk-model-small-en-us-0.15")
    vosk.SetLogLevel(-1)
    return vosk.Model(str(model_dir))


def _silence_frame() -> bytes:
    """One 20ms frame of silence (320 samples × 2 bytes)."""
    return b"\x00\x00" * SAMPLES_PER_FRAME


def _tone_frame(freq_hz: int = 440, sample_offset: int = 0) -> bytes:
    """One 20ms frame of a simple sine-wave tone (helps Vosk produce partial text)."""
    import math
    samples = []
    for i in range(SAMPLES_PER_FRAME):
        t = (sample_offset + i) / SAMPLE_RATE
        val = int(16000 * math.sin(2 * math.pi * freq_hz * t))
        val = max(-32768, min(32767, val))
        samples.append(struct.pack("<h", val))
    return b"".join(samples)


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestASRWorkerStartStop:
    """Basic start/stop lifecycle (existing behavior preserved)."""

    def test_start_stop_no_audio(self):
        """Worker starts and stops cleanly even without any audio."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        assert w.running
        w.stop()
        assert not w.running

    def test_stop_is_idempotent(self):
        """Calling stop() multiple times is safe."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        w.stop()
        w.stop()  # should not raise
        assert not w.running

    def test_add_audio_after_stop_is_ignored(self):
        """Audio added after stop() is silently dropped."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        w.stop()
        w.add_audio(_silence_frame())
        assert w.audio_queue.empty()


class TestASRWorkerFinish:
    """Tests for the new finish() finalization lifecycle."""

    def test_finish_returns_final_result(self):
        """finish() returns an ASRResult with is_final=True."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()

        # Feed a few frames of silence so Vosk has something to finalize.
        for _ in range(5):
            w.add_audio(_silence_frame())

        result = w.finish()

        assert result is not None
        assert isinstance(result, ASRResult)
        assert result.is_final is True
        # Silence produces empty text — that's correct behavior.
        assert isinstance(result.text, str)

        # Worker should be fully stopped after finish().
        assert not w.running
        assert w._finished

    def test_finish_drains_queued_audio(self):
        """Audio queued before finish() is fed to Vosk before FinalResult()."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()

        # Queue many frames rapidly (more than the worker can process instantly).
        for i in range(25):
            w.add_audio(_silence_frame())

        result = w.finish()

        assert result is not None
        assert result.is_final is True
        # The audio queue should be fully drained.
        assert w.audio_queue.empty()

    def test_add_audio_after_finish_is_ignored(self):
        """Audio added after finish() is silently dropped."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        w.add_audio(_silence_frame())
        w.finish()

        # After finish, add_audio should be a no-op.
        w.add_audio(_silence_frame())
        assert w.audio_queue.empty()

    def test_double_finish_returns_none(self):
        """Calling finish() twice returns None the second time (safe/idempotent)."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        w.add_audio(_silence_frame())

        result1 = w.finish()
        assert result1 is not None

        result2 = w.finish()
        assert result2 is None

    def test_finish_on_not_started_returns_none(self):
        """finish() on a worker that was never started returns None."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        # Never called start().
        result = w.finish()
        assert result is None

    def test_stop_then_finish_returns_none(self):
        """stop() followed by finish() returns None (worker already dead)."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()
        w.add_audio(_silence_frame())
        w.stop()

        result = w.finish()
        assert result is None

    def test_stop_discards_buffered_audio(self):
        """stop() does NOT call FinalResult — buffered audio is lost.
        This tests the pre-existing behavior to document it."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()

        # Queue audio but immediately stop.
        for _ in range(10):
            w.add_audio(_silence_frame())
        w.stop()

        # No final result should have been produced by stop().
        # (Any results that happened to be produced during processing
        # are fine, but there's no guaranteed FinalResult.)
        # The key point: stop() does not call FinalResult().
        assert not w.running

    def test_finish_completes_within_timeout(self):
        """finish() should complete quickly — not hang."""
        model = _load_shared_model()
        w = ASRWorker(model=model)
        w.start()

        for _ in range(10):
            w.add_audio(_silence_frame())

        t0 = time.perf_counter()
        result = w.finish()
        elapsed = time.perf_counter() - t0

        assert result is not None
        # Should complete well within 5 seconds for 10 frames of silence.
        assert elapsed < 5.0, f"finish() took {elapsed:.2f}s"


class TestASRWorkerNoModel:
    """Tests for worker behavior when no model is available."""

    def test_finish_without_model_returns_none(self):
        """finish() on a worker without a model returns None (no recognizer)."""
        # Create worker without model, but prevent it from downloading.
        w = ASRWorker()
        w._model_loading_attempted = True  # Prevent download attempt
        w.model = None
        w.recognizer = None
        w.running = True
        w._finished = False
        # Start the thread manually — it will loop doing nothing useful.
        w.worker_thread = __import__("threading").Thread(target=w._worker_loop, daemon=True)
        w.worker_thread.start()

        w.add_audio(_silence_frame())
        result = w.finish()

        # No model → no recognizer → FinalResult never called → None.
        assert result is None


class TestASRResultDataclass:
    """Basic ASRResult dataclass tests."""

    def test_asr_result_fields(self):
        r = ASRResult(text="hello", is_final=True, confidence=0.95)
        assert r.text == "hello"
        assert r.is_final is True
        assert r.confidence == 0.95

    def test_asr_result_defaults(self):
        r = ASRResult(text="", is_final=False)
        assert r.confidence == 0.0

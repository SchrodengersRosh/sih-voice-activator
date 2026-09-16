"""Vosk ASR worker running in a separate thread to avoid blocking asyncio event loop."""
from __future__ import annotations

import json
import queue
import threading
from dataclasses import dataclass
from typing import Optional

import vosk

# Import SAMPLE_RATE from protocol - using absolute import
try:
    # When running as module
    from backend.protocol import SAMPLE_RATE
except ImportError:
    # When running directly or in tests
    from protocol import SAMPLE_RATE


@dataclass
class ASRResult:
    """Result from ASR processing."""
    text: str
    is_final: bool
    confidence: float = 0.0


class ASRWorker:
    """Worker thread for Vosk ASR processing."""

    def __init__(self, model_path: Optional[str] = None):
        """
        Initialize ASR worker.

        Args:
            model_path: Path to Vosk model. If None, uses small English model.
        """
        self.model_path = model_path
        self.model = None
        self.recognizer = None
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.worker_thread = None
        self.running = False
        self._model_loading_attempted = False

    def start(self) -> None:
        """Start the ASR worker thread."""
        if self.running:
            return

        self.running = True
        self.worker_thread = threading.Thread(target=self._worker_loop, daemon=True)
        self.worker_thread.start()

    def stop(self) -> None:
        """Stop the ASR worker thread."""
        self.running = False
        if self.worker_thread:
            self.worker_thread.join(timeout=5.0)

    def add_audio(self, audio_data: bytes) -> None:
        """Add audio data to be processed."""
        if self.running:
            self.audio_queue.put(audio_data)

    def get_result(self, timeout: float = 0.1) -> Optional[ASRResult]:
        """Get ASR result if available."""
        try:
            return self.result_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def _worker_loop(self) -> None:
        """Main worker loop running in separate thread."""
        while self.running:
            try:
                # Get audio data with timeout
                audio_data = self.audio_queue.get(timeout=0.1)

                # Load Vosk model if not already loaded (and not attempted)
                if self.model is None and not self._model_loading_attempted:
                    self._model_loading_attempted = True
                    try:
                        if self.model_path is None:
                            # For now, we'll use a simple approach - in real implementation,
                            # this would download/load a proper model
                            try:
                                self.model = vosk.Model(lang="en-us")
                            except Exception:
                                # Fallback: create a minimal model for testing
                                # In production, this would be a proper model download
                                self.model = vosk.Model(model_name="vosk-model-small-en-us-0.15")
                        else:
                            self.model = vosk.Model(self.model_path)

                        self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
                        self.recognizer.SetWords(True)
                    except Exception as e:
                        # Log error but keep worker running (without model)
                        print(f"ASR Worker failed to load model: {e}")
                        self.model = None
                        self.recognizer = None

                # If we still don't have a model, discard audio data and continue
                if self.model is None:
                    continue

                # Process with Vosk
                if self.recognizer.AcceptWaveform(audio_data):
                    # Final result
                    result_json = self.recognizer.Result()
                    result = json.loads(result_json)
                    asr_result = ASRResult(
                        text=result.get("text", ""),
                        is_final=True,
                        confidence=result.get("confidence", 0.0)
                    )
                else:
                    # Partial result
                    result_json = self.recognizer.PartialResult()
                    result = json.loads(result_json)
                    asr_result = ASRResult(
                        text=result.get("partial", ""),
                        is_final=False,
                        confidence=0.0
                    )

                # Put result in queue (non-blocking)
                try:
                    self.result_queue.put_nowait(asr_result)
                except queue.Full:
                    # Drop result if queue is full
                    pass

            except queue.Empty:
                continue
            except Exception as e:
                # Log error but keep worker running
                print(f"ASR Worker error: {e}")
                continue

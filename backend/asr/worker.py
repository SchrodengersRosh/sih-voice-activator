"""Vosk ASR worker running in a separate thread to avoid blocking asyncio event loop."""
from __future__ import annotations

import json
import queue
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path
import threading
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

    # Internal sentinel to signal finish
    _FINISH = object()

    def __init__(self, model_path: Optional[str] = None, model: Optional[vosk.Model] = None):
        """
        Initialize ASR worker.

        Args:
            model_path: Path to Vosk model. If None, uses small English model.
            model: Pre-loaded Vosk model. If provided, skips model loading.
        """
        self.model_path = model_path
        # If a pre-loaded model is provided, use it; otherwise load later
        self.model = model
        self.recognizer = None
        if self.model is not None:
            # Initialize recognizer with pre-loaded model
            self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
            self.recognizer.SetWords(True)
        self.audio_queue = queue.Queue()
        self.result_queue = queue.Queue()
        self.worker_thread = None
        self.running = False
        self._finished = False
        self._final_result = None
        self._model_loading_attempted = model is not None  # Skip loading if model provided

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
        if self.running and not self._finished:
            self.audio_queue.put(audio_data)

    def get_result(self, timeout: float = 0.1) -> Optional[ASRResult]:
        """Get ASR result if available."""
        try:
            return self.result_queue.get(timeout=timeout)
        except queue.Empty:
            return None

    def finish(self) -> Optional[ASRResult]:
        """
        Finish processing and return final ASR result.

        Returns:
            ASRResult with final transcription, or None if worker not started,
            already stopped, or already finished.
        """
        # Return None if worker was never started
        if not self.running:
            return None

        # Return None if finish() has already been called
        if self._finished:
            return None

        # Mark as finished to prevent further add_audio
        self._finished = True

        # Enqueue the finish sentinel
        self.audio_queue.put(self._FINISH)

        # Wait for worker thread to finish processing
        if self.worker_thread:
            self.worker_thread.join(timeout=5.0)
            self.worker_thread = None

        # Return the final result if available
        try:
            return self.result_queue.get_nowait()
        except queue.Empty:
            return None

    def _ensure_model_loaded(self) -> None:
        """Ensure Vosk model is loaded, downloading and extracting if necessary."""
        if self.model is not None or self._model_loading_attempted:
            return
        self._model_loading_attempted = True
        try:
            # Determine model directory path
            if self.model_path is None:
                model_dir = self._get_default_model_path()
            else:
                model_dir = Path(self.model_path)
            # Ensure the model directory exists (extract if needed)
            if not model_dir.exists():
                self._download_and_extract_model(model_dir)
            # Load the model
            self.model = vosk.Model(str(model_dir))
            self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
            self.recognizer.SetWords(True)
        except Exception as e:
            # Log error but keep worker running (without model)
            print(f"ASR Worker failed to load model: {e}")
            self.model = None
            self.recognizer = None

    def _get_default_model_path(self) -> Path:
        """Return the path to the default Vosk small English model directory."""
        local_model = Path("vosk-model-small-en-us-0.15")
        if local_model.is_dir():
            return local_model
        cache_dir = Path.home() / ".cache" / "vosk"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / "vosk-model-small-en-us-0.15"

    def _download_and_extract_model(self, model_dir: Path) -> None:
        """Download and extract the Vosk small English model to the given directory."""
        model_url = "https://alphacephei.com/vosk/models/vosk-model-small-en-us-0.15.zip"
        zip_path = model_dir.with_suffix('.zip')
        try:
            if not zip_path.exists():
                print(f"Downloading Vosk model from {model_url}...")
                urllib.request.urlretrieve(model_url, zip_path)
                print("Download complete. Extracting...")
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(path=model_dir.parent)
            zip_path.unlink()
            print(f"Vosk model extracted to {model_dir}")
        except Exception as e:
            print(f"Failed to download or extract Vosk model: {e}")
            raise

    def _worker_loop(self) -> None:
        """Main worker loop running in separate thread."""
        while self.running:
            try:
                # Get audio data with timeout
                audio_data = self.audio_queue.get(timeout=0.1)

                # Check for finish sentinel
                if audio_data is self._FINISH:
                    # Drain is complete, finalize recognizer and emit final result
                    if self.model is not None and self.recognizer is not None:
                        # Call FinalResult to get the final transcription
                        result_json = self.recognizer.FinalResult()
                        result = json.loads(result_json)
                        asr_result = ASRResult(
                            text=result.get("text", ""),
                            is_final=True,
                            confidence=result.get("confidence", 0.0)
                        )
                        try:
                            self.result_queue.put_nowait(asr_result)
                        except queue.Full:
                            pass
                    # Break out of the loop to terminate the worker
                    break

                # Load Vosk model if not already loaded (and not attempted)
                if self.model is None:
                    self._ensure_model_loaded()

                # Ensure recognizer is created if we have a model but no recognizer
                if self.model is not None and self.recognizer is None:
                    self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
                    self.recognizer.SetWords(True)

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

                # Put result in queue only if not finished (to avoid queuing partial results during finish)
                if not self._finished:
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
        # Set running to false to ensure cleanup
        self.running = False

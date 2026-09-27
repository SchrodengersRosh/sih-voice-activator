"""TTS provider abstraction for SIH voice activator."""
from __future__ import annotations

import os
import shutil
import struct
import subprocess
import sys
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path
from typing import Optional
import wave


def _convert_to_16k_mono_s16le(raw_pcm: bytes, sample_rate: int, channels: int, sampwidth: int) -> bytes:
    """Convert raw PCM audio to 16 kHz mono signed 16-bit little-endian."""
    if not raw_pcm:
        return b""

    # 1. Convert sample width to 16-bit signed if needed
    if sampwidth != 2:
        try:
            import audioop
            raw_pcm = audioop.lin2lin(raw_pcm, sampwidth, 2)
            sampwidth = 2
        except Exception:
            pass

    # 2. Downmix multi-channel to mono if needed
    if channels > 1:
        try:
            import audioop
            raw_pcm = audioop.tomono(raw_pcm, 2, 1.0 / channels, 1.0 / channels)
            channels = 1
        except Exception:
            stride = 2 * channels
            raw_pcm = b"".join(raw_pcm[i : i + 2] for i in range(0, len(raw_pcm), stride))
            channels = 1

    # 3. Resample to 16,000 Hz if needed
    if sample_rate != 16000:
        try:
            import audioop
            converted, _ = audioop.ratecv(raw_pcm, 2, 1, sample_rate, 16000, None)
            raw_pcm = converted
        except Exception:
            # Linear interpolation fallback if audioop is not available
            in_samples = len(raw_pcm) // 2
            out_samples = int(in_samples * (16000 / sample_rate))
            if out_samples > 0:
                in_fmt = f"<{in_samples}h"
                samples = struct.unpack(in_fmt, raw_pcm[: in_samples * 2])
                ratio = (in_samples - 1) / max(1, out_samples - 1)
                resampled = []
                for i in range(out_samples):
                    src_idx = i * ratio
                    i_low = int(src_idx)
                    i_high = min(i_low + 1, in_samples - 1)
                    frac = src_idx - i_low
                    val = int((1.0 - frac) * samples[i_low] + frac * samples[i_high])
                    val = max(-32768, min(32767, val))
                    resampled.append(val)
                raw_pcm = struct.pack(f"<{len(resampled)}h", *resampled)

    return raw_pcm


class TTSProvider(ABC):
    """Abstract base for TTS providers returning 16 kHz mono signed 16-bit PCM."""

    @abstractmethod
    def synthesize_pcm(self, text: str) -> bytes:
        """Synthesize text to raw 16 kHz mono 16-bit signed PCM."""
        ...


class FakeTTSProvider(TTSProvider):
    """Deterministic in-memory TTS for unit testing and fallback."""

    def __init__(self, frame_count: int = 2) -> None:
        self.frame_count = max(1, frame_count)

    def synthesize_pcm(self, text: str) -> bytes:
        if not text or not text.strip():
            return b""
        # Return exact 640-byte frames (20 ms @ 16 kHz mono s16le = 320 samples = 640 bytes)
        frame = b"\x10\x00\xef\xff" * 160  # 640 bytes
        return frame * self.frame_count


class PiperTTSProvider(TTSProvider):
    """
    Cross-platform local TTS using Piper (https://github.com/rhasspy/piper).

    Configuration (via constructor or environment variables):
    - executable_path: PIPER_EXECUTABLE or PIPER_PATH (default: searches PATH for 'piper' or 'piper.exe')
    - model_path: PIPER_MODEL (path to .onnx model file)
    - config_path: PIPER_CONFIG (optional, path to .json config file)
    - speaker_id: PIPER_SPEAKER (optional, int speaker id for multi-speaker models)
    - length_scale: PIPER_LENGTH_SCALE (optional, float speech rate, e.g. 1.0)
    """

    def __init__(
        self,
        executable_path: Optional[str] = None,
        model_path: Optional[str] = None,
        config_path: Optional[str] = None,
        speaker_id: Optional[int] = None,
        length_scale: Optional[float] = None,
    ) -> None:
        # Determine executable path: explicit > env vars > system PATH > venv bin
        default_exec = None
        if not executable_path and not os.environ.get("PIPER_EXECUTABLE") and not os.environ.get("PIPER_PATH"):
            cand_execs = [
                shutil.which("piper"),
                shutil.which("piper.exe"),
                os.path.join(sys.prefix, "bin", "piper"),
                str(Path(__file__).resolve().parents[2] / ".venv" / "bin" / "piper"),
            ]
            for ce in cand_execs:
                if ce and (shutil.which(ce) or os.path.exists(ce)):
                    default_exec = ce
                    break

        self.executable_path = (
            executable_path
            or os.environ.get("PIPER_EXECUTABLE")
            or os.environ.get("PIPER_PATH")
            or default_exec
            or shutil.which("piper")
            or shutil.which("piper.exe")
        )

        # Determine model path: explicit > env var > piper_models directory > cache
        default_model = None
        if not model_path and not os.environ.get("PIPER_MODEL"):
            candidates = [
                Path(__file__).resolve().parents[2] / "piper_models" / "en_US-lessac-low.onnx",
                Path(__file__).resolve().parents[2] / "piper_models" / "en_US-lessac-medium.onnx",
                Path.home() / ".cache" / "piper" / "en_US-lessac-low.onnx",
            ]
            for c in candidates:
                if c.exists():
                    default_model = str(c)
                    break

        self.model_path = model_path or os.environ.get("PIPER_MODEL") or default_model
        
        # Determine config path: explicit > env var > model_path.json
        default_config = None
        if not config_path and not os.environ.get("PIPER_CONFIG") and self.model_path:
            json_candidate = f"{self.model_path}.json"
            if os.path.exists(json_candidate):
                default_config = json_candidate

        self.config_path = config_path or os.environ.get("PIPER_CONFIG") or default_config

        # Speaker ID and length scale
        if speaker_id is not None:
            self.speaker_id = speaker_id
        elif os.environ.get("PIPER_SPEAKER"):
            try:
                self.speaker_id = int(os.environ["PIPER_SPEAKER"])
            except ValueError:
                self.speaker_id = None
        else:
            self.speaker_id = None

        if length_scale is not None:
            self.length_scale = length_scale
        elif os.environ.get("PIPER_LENGTH_SCALE"):
            try:
                self.length_scale = float(os.environ["PIPER_LENGTH_SCALE"])
            except ValueError:
                self.length_scale = None
        else:
            self.length_scale = None

    def is_available(self) -> bool:
        """Check whether the Piper executable and model exist."""
        has_exec = bool(
            self.executable_path
            and (shutil.which(self.executable_path) or os.path.exists(self.executable_path))
        )
        has_model = bool(self.model_path and os.path.exists(self.model_path))
        return has_exec and has_model

    def synthesize_pcm(self, text: str) -> bytes:
        clean_text = text.strip()
        if not clean_text:
            return b""

        # Validate executable
        if not self.executable_path or not (
            shutil.which(self.executable_path) or os.path.exists(self.executable_path)
        ):
            raise RuntimeError(
                "Piper TTS executable not found. "
                "Please install Piper (e.g. from https://github.com/rhasspy/piper/releases or 'pip install piper-tts') "
                "and ensure it is on your PATH or set PIPER_EXECUTABLE to the executable path."
            )

        # Validate model
        if not self.model_path or not os.path.exists(self.model_path):
            raise RuntimeError(
                f"Piper TTS model not configured or file not found: '{self.model_path}'. "
                "Please download a Piper .onnx model and set PIPER_MODEL to its path."
            )

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_wav = tmp_file.name

        try:
            cmd = [
                str(self.executable_path),
                "--model",
                str(self.model_path),
                "--output_file",
                str(tmp_wav),
            ]
            if self.config_path:
                cmd.extend(["--config", str(self.config_path)])
            if self.speaker_id is not None:
                cmd.extend(["--speaker", str(self.speaker_id)])
            if self.length_scale is not None:
                cmd.extend(["--length_scale", str(self.length_scale)])

            proc = subprocess.run(
                cmd,
                input=clean_text.encode("utf-8"),
                capture_output=True,
                check=False,
            )
            if proc.returncode != 0:
                err_msg = proc.stderr.decode("utf-8", errors="replace").strip()
                raise RuntimeError(f"Piper TTS synthesis failed (exit {proc.returncode}): {err_msg}")

            with wave.open(tmp_wav, "rb") as w:
                sample_rate = w.getframerate()
                channels = w.getnchannels()
                sampwidth = w.getsampwidth()
                raw_pcm = w.readframes(w.getnframes())

            return _convert_to_16k_mono_s16le(raw_pcm, sample_rate, channels, sampwidth)

        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.unlink(tmp_wav)
                except OSError:
                    pass


class MacSayTTSProvider(TTSProvider):
    """Local offline TTS using macOS built-in /usr/bin/say command (optional fallback)."""

    def __init__(self, voice: Optional[str] = None) -> None:
        self.voice = voice

    def synthesize_pcm(self, text: str) -> bytes:
        clean_text = text.strip()
        if not clean_text:
            return b""

        with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
            tmp_wav = tmp_file.name

        try:
            cmd = ["say", "-o", tmp_wav, "--data-format=LEI16@16000"]
            if self.voice:
                cmd.extend(["-v", self.voice])
            cmd.append(clean_text)
            subprocess.run(cmd, check=True, capture_output=True)
            with wave.open(tmp_wav, "rb") as w:
                return w.readframes(w.getnframes())
        finally:
            if os.path.exists(tmp_wav):
                try:
                    os.unlink(tmp_wav)
                except OSError:
                    pass


def get_default_tts_provider(provider_type: Optional[str] = None) -> TTSProvider:
    """
    Return the default cross-platform TTS provider.

    Can be configured via:
    1. provider_type parameter ('piper', 'say', 'fake')
    2. TTS_PROVIDER environment variable
    Default is 'piper'.
    """
    provider_name = (provider_type or os.environ.get("TTS_PROVIDER", "piper")).lower()
    if provider_name == "piper":
        return PiperTTSProvider()
    elif provider_name == "fake":
        return FakeTTSProvider()
    elif provider_name == "say":
        return MacSayTTSProvider()
    else:
        raise ValueError(f"Unknown TTS provider: {provider_name}. Valid options: 'piper', 'say', 'fake'")

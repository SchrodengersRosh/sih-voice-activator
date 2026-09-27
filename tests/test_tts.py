"""Tests for cross-platform local TTS providers."""
from __future__ import annotations

import io
import os
import struct
import wave
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from backend.tts import (
    FakeTTSProvider,
    MacSayTTSProvider,
    PiperTTSProvider,
    TTSProvider,
    get_default_tts_provider,
)
from backend.tts.provider import _convert_to_16k_mono_s16le


def _create_dummy_wav_bytes(
    sample_rate: int = 22050,
    channels: int = 1,
    sampwidth: int = 2,
    num_samples: int = 2205,
) -> bytes:
    """Create in-memory WAV file bytes with deterministic tone."""
    buf = io.BytesIO()
    with wave.open(buf, "wb") as w:
        w.setnchannels(channels)
        w.setsampwidth(sampwidth)
        w.setframerate(sample_rate)
        # Create simple non-zero audio samples
        sample_val = 1000
        samples = []
        for _ in range(num_samples * channels):
            samples.append(sample_val)
            sample_val = -sample_val
        w.writeframes(struct.pack(f"<{len(samples)}h", *samples))
    return buf.getvalue()


class TestPiperTTSProvider:
    """Unit tests for PiperTTSProvider."""

    def test_piper_empty_text_returns_empty_bytes(self):
        provider = PiperTTSProvider(executable_path="piper", model_path="/dummy/model.onnx")
        assert provider.synthesize_pcm("") == b""
        assert provider.synthesize_pcm("   \n\t  ") == b""

    def test_piper_missing_executable_raises_actionable_error(self, tmp_path: Path):
        dummy_model = tmp_path / "model.onnx"
        dummy_model.write_bytes(b"dummy")

        provider = PiperTTSProvider(
            executable_path="/nonexistent/path/to/piper",
            model_path=str(dummy_model),
        )
        with pytest.raises(RuntimeError) as exc_info:
            provider.synthesize_pcm("Hello world")

        err = str(exc_info.value)
        assert "Piper TTS executable not found" in err
        assert "PIPER_EXECUTABLE" in err

    def test_piper_missing_model_raises_actionable_error(self, tmp_path: Path):
        dummy_exec = tmp_path / "piper"
        dummy_exec.write_bytes(b"dummy")
        dummy_exec.chmod(0o755)

        provider = PiperTTSProvider(
            executable_path=str(dummy_exec),
            model_path="/nonexistent/path/to/model.onnx",
        )
        with pytest.raises(RuntimeError) as exc_info:
            provider.synthesize_pcm("Hello world")

        err = str(exc_info.value)
        assert "Piper TTS model not configured or file not found" in err
        assert "PIPER_MODEL" in err

    def test_piper_subprocess_failure_raises_runtime_error(self, tmp_path: Path):
        dummy_exec = tmp_path / "piper"
        dummy_exec.write_bytes(b"dummy")
        dummy_model = tmp_path / "model.onnx"
        dummy_model.write_bytes(b"dummy")

        provider = PiperTTSProvider(executable_path=str(dummy_exec), model_path=str(dummy_model))

        mock_proc = MagicMock(returncode=1, stderr=b"CUDA out of memory")
        with patch("subprocess.run", return_value=mock_proc):
            with pytest.raises(RuntimeError) as exc_info:
                provider.synthesize_pcm("Hello world")
            assert "Piper TTS synthesis failed" in str(exc_info.value)
            assert "CUDA out of memory" in str(exc_info.value)

    def test_piper_successful_synthesis_converts_22k_to_16k_mono_s16le(self, tmp_path: Path):
        dummy_exec = tmp_path / "piper"
        dummy_exec.write_bytes(b"dummy")
        dummy_model = tmp_path / "model.onnx"
        dummy_model.write_bytes(b"dummy")

        provider = PiperTTSProvider(
            executable_path=str(dummy_exec),
            model_path=str(dummy_model),
            speaker_id=2,
            length_scale=1.1,
        )

        wav_22k = _create_dummy_wav_bytes(sample_rate=22050, channels=1, sampwidth=2, num_samples=22050)

        def mock_subprocess_run(cmd, input, capture_output, check):
            # Output file is in cmd
            out_file_idx = cmd.index("--output_file") + 1
            out_path = Path(cmd[out_file_idx])
            out_path.write_bytes(wav_22k)
            # Verify passed options
            assert "--model" in cmd
            assert "--speaker" in cmd
            assert "2" in cmd
            assert "--length_scale" in cmd
            assert "1.1" in cmd
            return MagicMock(returncode=0, stderr=b"")

        with patch("subprocess.run", side_effect=mock_subprocess_run):
            pcm_out = provider.synthesize_pcm("Dr. Rajendra Prasad was the first president.")

        assert len(pcm_out) > 0
        # 1 sec @ 16 kHz mono 16-bit = 16000 * 2 = 32000 bytes
        assert len(pcm_out) == 32000
        # Verify 16-bit alignment
        assert len(pcm_out) % 2 == 0

    def test_piper_is_available(self, tmp_path: Path):
        dummy_exec = tmp_path / "piper"
        dummy_exec.write_bytes(b"dummy")
        dummy_model = tmp_path / "model.onnx"
        dummy_model.write_bytes(b"dummy")

        # Both present
        provider = PiperTTSProvider(executable_path=str(dummy_exec), model_path=str(dummy_model))
        assert provider.is_available() is True

        # Missing model
        provider_no_model = PiperTTSProvider(executable_path=str(dummy_exec), model_path="/missing.onnx")
        assert provider_no_model.is_available() is False

        # Missing exec
        provider_no_exec = PiperTTSProvider(executable_path="/missing_piper", model_path=str(dummy_model))
        assert provider_no_exec.is_available() is False

    def test_piper_configured_via_environment_variables(self, monkeypatch, tmp_path: Path):
        dummy_exec = tmp_path / "env_piper"
        dummy_exec.write_bytes(b"dummy")
        dummy_model = tmp_path / "env_model.onnx"
        dummy_model.write_bytes(b"dummy")

        monkeypatch.setenv("PIPER_EXECUTABLE", str(dummy_exec))
        monkeypatch.setenv("PIPER_MODEL", str(dummy_model))
        monkeypatch.setenv("PIPER_CONFIG", "/path/to/env_config.json")
        monkeypatch.setenv("PIPER_SPEAKER", "5")
        monkeypatch.setenv("PIPER_LENGTH_SCALE", "0.9")

        provider = PiperTTSProvider()
        assert provider.executable_path == str(dummy_exec)
        assert provider.model_path == str(dummy_model)
        assert provider.config_path == "/path/to/env_config.json"
        assert provider.speaker_id == 5
        assert provider.length_scale == 0.9


class TestAudioConversion:
    """Unit tests for _convert_to_16k_mono_s16le helper."""

    def test_stereo_to_mono_conversion(self):
        # 16000 Hz, 2 channels, 2 bytes/sample, 1600 samples
        raw_stereo = b"\x01\x00\x02\x00" * 1600
        mono = _convert_to_16k_mono_s16le(raw_stereo, sample_rate=16000, channels=2, sampwidth=2)
        assert len(mono) == 1600 * 2

    def test_resampling_from_24k_to_16k(self):
        # 24000 Hz, 1 channel, 2 bytes/sample, 2400 samples (0.1 sec)
        raw_24k = b"\x10\x00" * 2400
        resampled = _convert_to_16k_mono_s16le(raw_24k, sample_rate=24000, channels=1, sampwidth=2)
        assert len(resampled) == 1600 * 2

    def test_empty_audio_returns_empty(self):
        assert _convert_to_16k_mono_s16le(b"", 16000, 1, 2) == b""


class TestDefaultProviderFactory:
    """Unit tests for get_default_tts_provider factory."""

    def test_default_is_piper_tts_provider(self, monkeypatch):
        monkeypatch.delenv("TTS_PROVIDER", raising=False)
        provider = get_default_tts_provider()
        assert isinstance(provider, PiperTTSProvider)

    def test_env_var_fake_provider(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "fake")
        provider = get_default_tts_provider()
        assert isinstance(provider, FakeTTSProvider)

    def test_env_var_say_provider(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "say")
        provider = get_default_tts_provider()
        assert isinstance(provider, MacSayTTSProvider)

    def test_explicit_argument_overrides_env(self, monkeypatch):
        monkeypatch.setenv("TTS_PROVIDER", "say")
        provider = get_default_tts_provider("fake")
        assert isinstance(provider, FakeTTSProvider)

    def test_unknown_provider_raises_value_error(self):
        with pytest.raises(ValueError):
            get_default_tts_provider("invalid_provider_name")

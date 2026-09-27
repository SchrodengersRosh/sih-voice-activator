"""backend.tts — Local Text-to-Speech layer for SIH voice assistant."""
from .provider import (
    FakeTTSProvider,
    MacSayTTSProvider,
    PiperTTSProvider,
    TTSProvider,
    get_default_tts_provider,
)

__all__ = [
    "TTSProvider",
    "FakeTTSProvider",
    "PiperTTSProvider",
    "MacSayTTSProvider",
    "get_default_tts_provider",
]

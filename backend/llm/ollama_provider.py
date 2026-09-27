"""
Ollama provider — streams text from a locally running Ollama instance.

Uses the HTTP API directly (no extra dependencies beyond ``requests``).
"""
from __future__ import annotations

import json
import logging
from typing import Generator, Optional

import requests

from .provider import DEFAULT_SYSTEM_PROMPT, LLMProvider

log = logging.getLogger(__name__)

_DEFAULT_BASE_URL = "http://127.0.0.1:11434"
_DEFAULT_MODEL = "qwen3:8b"


class OllamaProvider(LLMProvider):
    """LLMProvider backed by a local Ollama server."""

    def __init__(
        self,
        model: str = _DEFAULT_MODEL,
        base_url: str = _DEFAULT_BASE_URL,
        *,
        temperature: float = 0.7,
        num_ctx: int = 2048,
        think: bool = False,
    ) -> None:
        self._model = model
        self._base_url = base_url.rstrip("/")
        self._temperature = temperature
        self._num_ctx = num_ctx
        self._think = think

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def model_name(self) -> str:
        return self._model

    def generate_stream(
        self, prompt: str, system: str = DEFAULT_SYSTEM_PROMPT
    ) -> Generator[str, None, None]:
        """Yield text chunks via Ollama ``/api/generate`` (streaming)."""
        payload = {
            "model": self._model,
            "prompt": prompt,
            "system": system,
            "stream": True,
            "think": self._think,
            "options": {
                "temperature": self._temperature,
                "num_ctx": self._num_ctx,
            },
        }
        url = f"{self._base_url}/api/generate"
        log.debug("ollama request model=%s prompt=%r", self._model, prompt[:80])

        resp = requests.post(url, json=payload, stream=True, timeout=120)
        resp.raise_for_status()

        for line in resp.iter_lines(chunk_size=1, decode_unicode=True):
            if not line:
                continue
            obj = json.loads(line)
            token = obj.get("response", "")
            if token:
                yield token
            if obj.get("done"):
                break

    def warm(self) -> None:
        """Pre-load the model into memory (Ollama keep-alive)."""
        log.info("warming model %s …", self._model)
        payload = {"model": self._model, "prompt": "", "stream": False}
        url = f"{self._base_url}/api/generate"
        try:
            resp = requests.post(url, json=payload, timeout=120)
            resp.raise_for_status()
            log.info("model %s warm", self._model)
        except requests.RequestException as exc:
            log.warning("warm failed: %s", exc)

    def is_available(self) -> bool:
        """Check whether the Ollama server is reachable."""
        try:
            resp = requests.get(f"{self._base_url}/api/tags", timeout=5)
            return resp.ok
        except requests.RequestException:
            return False

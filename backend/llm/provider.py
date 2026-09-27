"""
LLM provider abstraction for local inference.

Provides a streaming text generation interface decoupled from any
specific runtime. Concrete providers implement the LLMProvider protocol.
"""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Generator, Optional


# ---------------------------------------------------------------------------
# System prompt: concise spoken-answer style for a voice assistant
# ---------------------------------------------------------------------------
DEFAULT_SYSTEM_PROMPT = (
    "You are a helpful voice assistant on an embedded device. "
    "Answer directly and concisely. Do not use markdown formatting. "
    "Keep answers short — one to three sentences for factual questions. "
    "Speak naturally as if answering aloud."
)


# ---------------------------------------------------------------------------
# Telemetry
# ---------------------------------------------------------------------------
@dataclass
class LLMTelemetry:
    """Timing and token metrics for a single generation request."""
    request_start: float = 0.0       # time.perf_counter()
    first_token: float = 0.0
    first_sentence_complete: float = 0.0
    final_token: float = 0.0
    generated_tokens: int = 0
    prompt_tokens: int = 0

    @property
    def ttft_ms(self) -> float:
        """Time to first token in milliseconds."""
        if self.first_token and self.request_start:
            return (self.first_token - self.request_start) * 1000
        return 0.0

    @property
    def time_to_first_sentence_ms(self) -> float:
        if self.first_sentence_complete and self.request_start:
            return (self.first_sentence_complete - self.request_start) * 1000
        return 0.0

    @property
    def total_generation_ms(self) -> float:
        if self.final_token and self.request_start:
            return (self.final_token - self.request_start) * 1000
        return 0.0

    @property
    def tokens_per_second(self) -> float:
        gen_time = self.final_token - self.first_token
        if gen_time > 0 and self.generated_tokens > 1:
            return (self.generated_tokens - 1) / gen_time
        return 0.0


# ---------------------------------------------------------------------------
# Abstract provider
# ---------------------------------------------------------------------------
class LLMProvider(ABC):
    """Abstract base for local LLM providers (Ollama, llama.cpp, MLX, …)."""

    @abstractmethod
    def generate_stream(
        self, prompt: str, system: str = DEFAULT_SYSTEM_PROMPT
    ) -> Generator[str, None, None]:
        """Yield text chunks as they are produced by the model."""
        ...

    @abstractmethod
    def model_name(self) -> str:
        """Return the model identifier being used."""
        ...

    def generate_with_telemetry(
        self, prompt: str, system: str = DEFAULT_SYSTEM_PROMPT
    ) -> tuple[str, LLMTelemetry]:
        """Generate a full response and collect timing telemetry."""
        tel = LLMTelemetry()
        chunks: list[str] = []
        sentence_enders = {'.', '!', '?'}
        tel.request_start = time.perf_counter()

        for chunk in self.generate_stream(prompt, system):
            now = time.perf_counter()
            if not tel.first_token:
                tel.first_token = now
            chunks.append(chunk)
            tel.generated_tokens += 1

            if not tel.first_sentence_complete:
                text_so_far = "".join(chunks)
                if any(text_so_far.rstrip().endswith(e) for e in sentence_enders):
                    tel.first_sentence_complete = now

            tel.final_token = now

        # If no sentence-ending punctuation was seen, mark end as first sentence
        if not tel.first_sentence_complete and tel.final_token:
            tel.first_sentence_complete = tel.final_token

        return "".join(chunks), tel

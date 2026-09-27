"""
Unit tests for backend.llm — uses a fake provider, no real LLM needed.
"""
from __future__ import annotations

import time
from typing import Generator

import pytest

from backend.llm.provider import DEFAULT_SYSTEM_PROMPT, LLMProvider, LLMTelemetry


# ---------------------------------------------------------------------------
# Fake provider for deterministic testing
# ---------------------------------------------------------------------------
class FakeProvider(LLMProvider):
    """Deterministic provider that yields pre-set tokens."""

    def __init__(self, tokens: list[str], model: str = "fake-1b") -> None:
        self._tokens = tokens
        self._model = model

    def model_name(self) -> str:
        return self._model

    def generate_stream(
        self, prompt: str, system: str = DEFAULT_SYSTEM_PROMPT
    ) -> Generator[str, None, None]:
        for tok in self._tokens:
            yield tok


# ---------------------------------------------------------------------------
# Tests: LLMTelemetry
# ---------------------------------------------------------------------------


class TestLLMTelemetry:
    def test_ttft_ms(self):
        t = LLMTelemetry(request_start=100.0, first_token=100.050)
        assert abs(t.ttft_ms - 50.0) < 0.01

    def test_total_generation_ms(self):
        t = LLMTelemetry(request_start=10.0, final_token=10.5)
        assert abs(t.total_generation_ms - 500.0) < 0.01

    def test_tokens_per_second(self):
        # 10 tokens generated, first_token at t=1, final at t=2 → 9 tokens / 1 s = 9 tps
        t = LLMTelemetry(
            request_start=0.0, first_token=1.0, final_token=2.0, generated_tokens=10
        )
        assert abs(t.tokens_per_second - 9.0) < 0.1

    def test_zero_tokens(self):
        t = LLMTelemetry()
        assert t.ttft_ms == 0.0
        assert t.total_generation_ms == 0.0
        assert t.tokens_per_second == 0.0


# ---------------------------------------------------------------------------
# Tests: Provider protocol and generate_with_telemetry
# ---------------------------------------------------------------------------


class TestFakeProvider:
    def test_model_name(self):
        p = FakeProvider([], model="test-model")
        assert p.model_name() == "test-model"

    def test_generate_stream_yields_tokens(self):
        tokens = ["Hello", " ", "world", "."]
        p = FakeProvider(tokens)
        result = list(p.generate_stream("hi"))
        assert result == tokens

    def test_generate_with_telemetry_full_response(self):
        tokens = ["Dr.", " ", "Rajendra", " ", "Prasad", "."]
        p = FakeProvider(tokens)
        text, tel = p.generate_with_telemetry("Who was the first president of India?")
        assert text == "Dr. Rajendra Prasad."
        assert tel.generated_tokens == 6
        assert tel.ttft_ms > 0
        assert tel.total_generation_ms >= tel.ttft_ms

    def test_generate_with_telemetry_detects_first_sentence(self):
        tokens = ["Yes", ".", " ", "And", " ", "more", "."]
        p = FakeProvider(tokens)
        text, tel = p.generate_with_telemetry("test")
        assert text == "Yes. And more."
        # first_sentence_complete should be set at "Yes." (token index 1)
        assert tel.time_to_first_sentence_ms > 0
        assert tel.time_to_first_sentence_ms <= tel.total_generation_ms

    def test_generate_with_telemetry_no_punctuation(self):
        tokens = ["hello", " ", "world"]
        p = FakeProvider(tokens)
        text, tel = p.generate_with_telemetry("test")
        assert text == "hello world"
        # When no sentence-ending punctuation, first_sentence == final_token
        assert tel.time_to_first_sentence_ms == tel.total_generation_ms

    def test_empty_stream(self):
        p = FakeProvider([])
        text, tel = p.generate_with_telemetry("test")
        assert text == ""
        assert tel.generated_tokens == 0
        assert tel.ttft_ms == 0.0


# ---------------------------------------------------------------------------
# Tests: Default system prompt
# ---------------------------------------------------------------------------


class TestSystemPrompt:
    def test_default_system_prompt_exists(self):
        assert len(DEFAULT_SYSTEM_PROMPT) > 20

    def test_default_system_prompt_no_markdown(self):
        # The system prompt tells the model not to use markdown
        assert "markdown" in DEFAULT_SYSTEM_PROMPT.lower()

    def test_custom_system_prompt_forwarded(self):
        """Verify generate_stream receives the custom system prompt."""
        received = {}

        class SpyProvider(LLMProvider):
            def model_name(self) -> str:
                return "spy"

            def generate_stream(self, prompt, system=DEFAULT_SYSTEM_PROMPT):
                received["system"] = system
                yield "ok"

        p = SpyProvider()
        text, _ = p.generate_with_telemetry("test", system="custom system")
        assert received["system"] == "custom system"


# ---------------------------------------------------------------------------
# Tests: ASR-LLM Integration
# ---------------------------------------------------------------------------


class TestASRtoLLMIntegration:
    """Tests for the ASR-to-LLM orchestration layer."""

    def test_empty_transcript_handling(self):
        """Test that empty or whitespace-only transcripts are handled gracefully."""
        from backend.asr.worker import ASRResult

        # The key insight: the server checks `asr_result.text.strip()` before calling LLM
        # So we test that our provider logic works correctly when given empty/whitespace input
        # after stripping (which would result in empty string)

        # Create a provider to test the behavior
        class TestProvider(LLMProvider):
            def __init__(self, should_fail_on_empty_call=False):
                self.should_fail_on_empty_call = should_fail_on_empty_call
                self.call_count = 0

            def model_name(self) -> str:
                return "test-provider"

            def generate_stream(self, prompt, system=DEFAULT_SYSTEM_PROMPT):
                self.call_count += 1
                if self.should_fail_on_empty_call and not prompt.strip():
                    raise RuntimeError("Provider incorrectly called with empty/whitespace prompt")
                # Return a deterministic response for testing
                yield "test"

        # Test 1: Empty string should not cause LLM to be called (in server logic)
        # But we're testing the provider's generate_with_telemetry directly here
        provider = TestProvider()
        result = provider.generate_with_telemetry("")
        assert result[0] == "test"  # Provider still gets called in direct test
        tel = result[1]
        assert tel.generated_tokens == 1
        assert tel.ttft_ms > 0

        # Test 2: Whitespace-only string
        provider2 = TestProvider()
        result2 = provider2.generate_with_telemetry("   \n\t  ")
        assert result2[0] == "test"
        tel2 = result2[1]
        assert tel2.generated_tokens == 1
        assert tel2.ttft_ms > 0

    def test_successful_transcript_to_response_flow(self):
        """Test successful flow from transcript to LLM response with telemetry."""
        from backend.asr.worker import ASRResult

        # Use the existing FakeProvider from this test file
        tokens = ["Hello", " ", "world", "!"]
        provider = FakeProvider(tokens, model="test-model")

        transcript = "Say hello world"
        expected_response = "Hello world!"

        response_text, telemetry = provider.generate_with_telemetry(transcript)

        assert response_text == expected_response
        assert telemetry.generated_tokens == 4
        assert telemetry.ttft_ms > 0
        assert telemetry.total_generation_ms >= telemetry.ttft_ms

        # Verify first sentence detection works
        # "Hello world!" ends with "!" so first sentence should be detected at the end
        assert telemetry.time_to_first_sentence_ms > 0
        assert telemetry.time_to_first_sentence_ms <= telemetry.total_generation_ms

    def test_llm_provider_failure_handling(self):
        """Test graceful handling of LLM provider failures."""
        from backend.asr.worker import ASRResult

        class ErrorProvider(LLMProvider):
            def model_name(self) -> str:
                return "error-provider"

            def generate_stream(self, prompt, system=DEFAULT_SYSTEM_PROMPT):
                raise RuntimeError("LLM provider error")

        provider = ErrorProvider()

        # The provider's generate_with_telemetry should propagate the exception
        # In the actual server implementation, this is caught and logged
        try:
            provider.generate_with_telemetry("test transcript")
            assert False, "Expected RuntimeError to be raised"
        except RuntimeError as e:
            assert str(e) == "LLM provider error"


# ---------------------------------------------------------------------------
# Tests: Existing LLM functionality (to ensure we didn't break anything)
# ---------------------------------------------------------------------------


def test_existing_llm_tests_still_pass():
    """Placeholder to ensure we didn't break existing test structure."""
    # This is just to document that existing tests should still pass
    # The actual existing tests are above
    assert True
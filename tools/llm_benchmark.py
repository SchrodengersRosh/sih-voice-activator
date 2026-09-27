#!/usr/bin/env python3
"""
LLM benchmark tool — measures local LLM response latency.

Usage:
    python tools/llm_benchmark.py --prompt "Who was the first president of India?"
    python tools/llm_benchmark.py --prompt "What is photosynthesis?" --model qwen3:8b
"""
from __future__ import annotations

import argparse
import sys
import os

# Allow running from repo root: python tools/llm_benchmark.py
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from backend.llm.provider import DEFAULT_SYSTEM_PROMPT
from backend.llm.ollama_provider import OllamaProvider


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark local LLM response")
    parser.add_argument("--prompt", required=True, help="Text prompt to send")
    parser.add_argument("--model", default="qwen3:8b", help="Ollama model name")
    parser.add_argument("--base-url", default="http://127.0.0.1:11434",
                        help="Ollama server URL")
    parser.add_argument("--system", default=DEFAULT_SYSTEM_PROMPT,
                        help="System prompt")
    parser.add_argument("--warm", action="store_true",
                        help="Pre-warm the model before benchmarking")
    args = parser.parse_args()

    provider = OllamaProvider(model=args.model, base_url=args.base_url)

    if not provider.is_available():
        print(f"ERROR: Ollama server not reachable at {args.base_url}", file=sys.stderr)
        sys.exit(1)

    if args.warm:
        print(f"Warming model {args.model} …")
        provider.warm()

    print(f"\n--- LLM Benchmark ---")
    print(f"Model:  {provider.model_name()}")
    print(f"Prompt: {args.prompt}")
    print()

    # Stream and display response live
    print("Response: ", end="", flush=True)
    response, tel = provider.generate_with_telemetry(args.prompt, args.system)
    print(response)

    # Print metrics
    print(f"\n--- Metrics ---")
    print(f"TTFT (time to first token):     {tel.ttft_ms:8.1f} ms")
    print(f"Time to first sentence:         {tel.time_to_first_sentence_ms:8.1f} ms")
    print(f"Total generation time:          {tel.total_generation_ms:8.1f} ms")
    print(f"Generated tokens:               {tel.generated_tokens:>8d}")
    print(f"Tokens/sec (decode):            {tel.tokens_per_second:8.1f}")


if __name__ == "__main__":
    main()

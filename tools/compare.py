"""Compare source and received WAV PCM, accounting for M1's final-frame padding."""
from __future__ import annotations
import argparse
from pathlib import Path
import wave


def pcm(path: Path) -> bytes:
    with wave.open(str(path), "rb") as source:
        if (source.getnchannels(), source.getsampwidth(), source.getframerate(), source.getcomptype()) != (1, 2, 16000, "NONE"):
            raise ValueError(f"{path}: expected mono 16-bit 16 kHz PCM WAV")
        return source.readframes(source.getnframes())


def compare(source: Path, received: Path) -> str:
    expected, actual = pcm(source), pcm(received)
    padded = expected + b"\0" * ((-len(expected)) % 640)
    if padded == actual:
        return "IDENTICAL"
    first = next((i for i, (a, b) in enumerate(zip(padded, actual)) if a != b), min(len(padded), len(actual)))
    return f"MISMATCH source={len(expected)} padded={len(padded)} received={len(actual)} first_byte={first}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source", type=Path)
    parser.add_argument("received", type=Path)
    args = parser.parse_args()
    result = compare(args.source, args.received)
    print(result)
    raise SystemExit(0 if result == "IDENTICAL" else 1)


if __name__ == "__main__":
    main()

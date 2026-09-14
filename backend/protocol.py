"""Frozen v1.0 binary audio wire protocol."""
from __future__ import annotations

from dataclasses import dataclass
import struct

MAGIC = 0xA5
VERSION = 1
HEADER = struct.Struct("<BBBBHHII")
HLEN = HEADER.size

CODEC_PCM = 0
CODEC_MULAW = 1
CODEC_ADPCM = 2
CODEC_OPUS = 3
CODEC_NAMES = {"pcm_s16le": CODEC_PCM, "mulaw": CODEC_MULAW, "adpcm_ima": CODEC_ADPCM, "opus": CODEC_OPUS}

FLAG_FIRST = 1
FLAG_LAST = 2
FLAG_PREBUF = 4
SAMPLES_PER_FRAME = 320
PCM_PAYLOAD_LEN = 640
EXPECTED_LEN = {CODEC_PCM: PCM_PAYLOAD_LEN}


class ProtocolError(ValueError):
    """A message violates the frozen protocol."""


class JsonSentAsBinaryError(ProtocolError):
    """A JSON control message was supplied in a binary WebSocket frame."""


@dataclass(frozen=True, slots=True)
class AudioFrame:
    codec: int
    flags: int
    seq: int
    stream_id: int
    sample_offset: int
    payload: bytes


def pack(frame: AudioFrame) -> bytes:
    """Serialize one complete binary audio frame."""
    _validate_fields(frame)
    return HEADER.pack(MAGIC, VERSION, frame.codec, frame.flags, frame.seq,
                       len(frame.payload), frame.stream_id, frame.sample_offset) + frame.payload


def unpack(message: bytes) -> AudioFrame:
    """Parse and strictly validate one complete binary audio frame."""
    if len(message) < HLEN:
        if message[:1] == b"{":
            raise JsonSentAsBinaryError("JSON sent as binary frame")
        raise ProtocolError(f"frame too short: {len(message)} bytes (need {HLEN})")
    magic, version, codec, flags, seq, payload_len, stream_id, sample_offset = HEADER.unpack_from(message)
    if magic != MAGIC:
        if message[:1] == b"{":
            raise JsonSentAsBinaryError("JSON sent as binary frame")
        raise ProtocolError(f"invalid magic: 0x{magic:02x}")
    if version != VERSION:
        raise ProtocolError(f"unsupported protocol version: {version}")
    payload = message[HLEN:]
    if payload_len != len(payload):
        raise ProtocolError(f"payload length header={payload_len}, actual={len(payload)}")
    frame = AudioFrame(codec, flags, seq, stream_id, sample_offset, payload)
    _validate_fields(frame)
    return frame


def _validate_fields(frame: AudioFrame) -> None:
    if not 0 <= frame.codec <= 255:
        raise ProtocolError("codec is outside u8 range")
    if frame.codec not in EXPECTED_LEN:
        raise ProtocolError(f"unsupported codec in M1: {frame.codec}")
    if not 0 <= frame.flags <= 255 or not 0 <= frame.seq <= 0xFFFF:
        raise ProtocolError("flags or sequence outside wire range")
    if not 0 <= frame.stream_id <= 0xFFFFFFFF or not 0 <= frame.sample_offset <= 0xFFFFFFFF:
        raise ProtocolError("stream_id or sample_offset outside wire range")
    expected = EXPECTED_LEN[frame.codec]
    if len(frame.payload) != expected:
        raise ProtocolError(f"codec {frame.codec} requires {expected} bytes, got {len(frame.payload)}")

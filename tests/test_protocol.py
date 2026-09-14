import pytest
from backend.protocol import *


def frame(payload=b"x" * 640):
    return AudioFrame(CODEC_PCM, FLAG_FIRST, 0, 9, 0, payload)


def test_pack_unpack_round_trip():
    assert unpack(pack(frame())) == frame()
    assert HLEN == 16


@pytest.mark.parametrize("message, error", [(b"{\"type\":\"hello\"}", JsonSentAsBinaryError), (b"\0" * 16, ProtocolError)])
def test_bad_magic(message, error):
    with pytest.raises(error): unpack(message)


def test_invalid_version_and_payload_length():
    raw = bytearray(pack(frame()))
    raw[1] = 2
    with pytest.raises(ProtocolError, match="version"): unpack(bytes(raw))
    raw = bytearray(pack(frame()))
    raw[6] = 1
    with pytest.raises(ProtocolError, match="payload length"): unpack(bytes(raw))


def test_pcm_frame_must_be_640_bytes():
    with pytest.raises(ProtocolError, match="requires 640"): pack(frame(b"x" * 639))

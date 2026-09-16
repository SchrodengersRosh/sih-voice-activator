# Protocol v1.0 (Frozen)

**WAV is a test/input container only. Live wire format is WebSocket binary frames as specified below.**

Audio is sent in WebSocket **binary** messages. Controls are WebSocket **text** JSON. A binary JSON payload is explicitly diagnosed and dropped.

## Audio frame

Every binary frame is a 16-byte little-endian header (`struct.Struct("<BBBBHHII")`) followed by payload:

| Offset | Type | Field |
|---:|---|---|
| 0 | u8 | magic `0xA5` |
| 1 | u8 | version `1` |
| 2 | u8 | codec |
| 3 | u8 | flags |
| 4 | u16 | seq |
| 6 | u16 | payload_len |
| 8 | u32 | stream_id |
| 12 | u32 | sample_offset |

Codec IDs are PCM s16le=0, µ-law=1, IMA ADPCM=2, Opus=3. M1 permits only PCM: 16 kHz, mono, signed 16-bit LE. Each frame has exactly 320 decoded samples / 20 ms / **640 payload bytes**. Last frames are zero-padded.

Flags: `FIRST=1`, `LAST=2`, `PREBUF=4`. `seq` starts at zero and increments per frame. `sample_offset` is decoded-sample index, not a timestamp: 0, 320, 640…. `payload_len` must match the actual message length minus 16 and the codec’s fixed expected length.

## Lifecycle

Connection order: `hello` → `hello_ack` → `start` → binary frames → `stop`. `hello` includes proto=1, device ID, codec list, and 16000 sample rate; response is `{ "type": "hello_ack", "proto": 1 }`. `start` is mandatory before frame zero and supplies stream ID, `pcm_s16le`, 16000 Hz, 1 channel, 20 ms, prebuffer size, live offset, and `t_detect_us`. Start before hello returns `HELLO_REQUIRED` then closes 1002.

Streams are scoped to `(WebSocket connection, stream_id)`. Unknown stream frames are dropped without killing the connection. Malformed fields, version, magic, payload length, codec, and offsets are rejected defensively. End occurs on `stop`, 1.5 s inactivity, 10 s maximum, or connection closure; `LAST` is informational.

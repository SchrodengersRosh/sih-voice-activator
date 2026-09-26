/**
 * @file protocol_frames.h
 * @brief Frozen v1.0 binary audio wire protocol serialization for ESP32-S3.
 */

#ifndef PROTOCOL_FRAMES_H
#define PROTOCOL_FRAMES_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define PROTOCOL_MAGIC          0xA5
#define PROTOCOL_VERSION        1

#define CODEC_PCM_S16LE         0
#define CODEC_MULAW             1
#define CODEC_ADPCM_IMA         2
#define CODEC_OPUS              3

#define FLAG_FIRST              (1 << 0)  // 1
#define FLAG_LAST               (1 << 1)  // 2
#define FLAG_PREBUF             (1 << 2)  // 4

#define AUDIO_SAMPLE_RATE       16000
#define AUDIO_FRAME_MS          20
#define AUDIO_SAMPLES_PER_FRAME 320
#define AUDIO_PCM_PAYLOAD_LEN   640
#define PROTOCOL_HEADER_LEN     16
#define TOTAL_WIRE_FRAME_LEN    (PROTOCOL_HEADER_LEN + AUDIO_PCM_PAYLOAD_LEN) // 656 bytes

#define PREBUFFER_MS            800
#define PREBUFFER_FRAMES        (PREBUFFER_MS / AUDIO_FRAME_MS) // 40 frames
#define PREBUFFER_SAMPLES       (PREBUFFER_FRAMES * AUDIO_SAMPLES_PER_FRAME) // 12800 samples

#pragma pack(push, 1)
typedef struct {
    uint8_t  magic;          // 0xA5
    uint8_t  version;        // 1
    uint8_t  codec;          // 0 = PCM s16le
    uint8_t  flags;          // Bitmask: FIRST=1, LAST=2, PREBUF=4
    uint16_t seq;            // Sequence number (0, 1, 2...)
    uint16_t payload_len;    // 640 bytes
    uint32_t stream_id;      // Stream ID
    uint32_t sample_offset;  // Decoded sample offset (0, 320, 640...)
} protocol_header_t;
#pragma pack(pop)

/**
 * @brief Pack an audio frame into a buffer using strict little-endian layout.
 *
 * @param out_buffer Output buffer of at least TOTAL_WIRE_FRAME_LEN bytes.
 * @param codec Codec ID (must be CODEC_PCM_S16LE for M1-M4).
 * @param flags Bitmask of FLAG_FIRST, FLAG_LAST, FLAG_PREBUF.
 * @param seq Sequence number.
 * @param stream_id Stream ID.
 * @param sample_offset Sample offset.
 * @param pcm_payload Pointer to 640 bytes of signed 16-bit LE PCM audio.
 * @return size_t Total bytes written (656), or 0 on error.
 */
size_t protocol_pack_frame(
    uint8_t *out_buffer,
    uint8_t codec,
    uint8_t flags,
    uint16_t seq,
    uint32_t stream_id,
    uint32_t sample_offset,
    const uint8_t *pcm_payload
);

/**
 * @brief Unpack an audio frame header (for host verification / testing).
 *
 * @param in_buffer Input buffer of at least 16 bytes.
 * @param in_len Length of input buffer.
 * @param out_header Pointer to header struct to populate.
 * @return true if valid header matching protocol v1.0, false otherwise.
 */
bool protocol_unpack_header(
    const uint8_t *in_buffer,
    size_t in_len,
    protocol_header_t *out_header
);

#ifdef __cplusplus
}
#endif

#endif // PROTOCOL_FRAMES_H

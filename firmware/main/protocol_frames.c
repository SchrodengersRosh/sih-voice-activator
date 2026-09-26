/**
 * @file protocol_frames.c
 * @brief Frozen v1.0 binary audio wire protocol serialization implementation.
 */

#include "protocol_frames.h"
#include <string.h>

size_t protocol_pack_frame(
    uint8_t *out_buffer,
    uint8_t codec,
    uint8_t flags,
    uint16_t seq,
    uint32_t stream_id,
    uint32_t sample_offset,
    const uint8_t *pcm_payload
) {
    if (!out_buffer || !pcm_payload) {
        return 0;
    }

    // Little-endian explicit serialization
    out_buffer[0] = PROTOCOL_MAGIC;
    out_buffer[1] = PROTOCOL_VERSION;
    out_buffer[2] = codec;
    out_buffer[3] = flags;

    // seq (u16 little-endian)
    out_buffer[4] = (uint8_t)(seq & 0xFF);
    out_buffer[5] = (uint8_t)((seq >> 8) & 0xFF);

    // payload_len (u16 little-endian: 640)
    out_buffer[6] = (uint8_t)(AUDIO_PCM_PAYLOAD_LEN & 0xFF);
    out_buffer[7] = (uint8_t)((AUDIO_PCM_PAYLOAD_LEN >> 8) & 0xFF);

    // stream_id (u32 little-endian)
    out_buffer[8]  = (uint8_t)(stream_id & 0xFF);
    out_buffer[9]  = (uint8_t)((stream_id >> 8) & 0xFF);
    out_buffer[10] = (uint8_t)((stream_id >> 16) & 0xFF);
    out_buffer[11] = (uint8_t)((stream_id >> 24) & 0xFF);

    // sample_offset (u32 little-endian)
    out_buffer[12] = (uint8_t)(sample_offset & 0xFF);
    out_buffer[13] = (uint8_t)((sample_offset >> 8) & 0xFF);
    out_buffer[14] = (uint8_t)((sample_offset >> 16) & 0xFF);
    out_buffer[15] = (uint8_t)((sample_offset >> 24) & 0xFF);

    // Copy 640 payload bytes
    memcpy(out_buffer + PROTOCOL_HEADER_LEN, pcm_payload, AUDIO_PCM_PAYLOAD_LEN);

    return TOTAL_WIRE_FRAME_LEN;
}

bool protocol_unpack_header(
    const uint8_t *in_buffer,
    size_t in_len,
    protocol_header_t *out_header
) {
    if (!in_buffer || !out_header || in_len < PROTOCOL_HEADER_LEN) {
        return false;
    }

    out_header->magic   = in_buffer[0];
    out_header->version = in_buffer[1];
    out_header->codec   = in_buffer[2];
    out_header->flags   = in_buffer[3];

    out_header->seq = (uint16_t)in_buffer[4] | ((uint16_t)in_buffer[5] << 8);
    out_header->payload_len = (uint16_t)in_buffer[6] | ((uint16_t)in_buffer[7] << 8);

    out_header->stream_id = (uint32_t)in_buffer[8] |
                            ((uint32_t)in_buffer[9] << 8) |
                            ((uint32_t)in_buffer[10] << 16) |
                            ((uint32_t)in_buffer[11] << 24);

    out_header->sample_offset = (uint32_t)in_buffer[12] |
                                ((uint32_t)in_buffer[13] << 8) |
                                ((uint32_t)in_buffer[14] << 16) |
                                ((uint32_t)in_buffer[15] << 24);

    if (out_header->magic != PROTOCOL_MAGIC ||
        out_header->version != PROTOCOL_VERSION ||
        out_header->codec != CODEC_PCM_S16LE ||
        out_header->payload_len != AUDIO_PCM_PAYLOAD_LEN) {
        return false;
    }

    return true;
}

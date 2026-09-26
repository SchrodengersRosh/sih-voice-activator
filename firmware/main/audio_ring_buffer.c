/**
 * @file audio_ring_buffer.c
 * @brief Circular audio pre-buffer implementation.
 */

#include "audio_ring_buffer.h"
#include <string.h>

void audio_ring_buffer_init(audio_ring_buffer_t *rb) {
    if (!rb) return;
    memset(rb->buffer, 0, sizeof(rb->buffer));
    rb->write_idx = 0;
    rb->total_frames = 0;
    rb->count = 0;
}

void audio_ring_buffer_push(audio_ring_buffer_t *rb, const uint8_t *frame_data) {
    if (!rb || !frame_data) return;

    memcpy(rb->buffer[rb->write_idx], frame_data, AUDIO_PCM_PAYLOAD_LEN);
    rb->write_idx = (rb->write_idx + 1) % RING_BUFFER_CAPACITY_FRAMES;
    rb->total_frames++;
    if (rb->count < RING_BUFFER_CAPACITY_FRAMES) {
        rb->count++;
    }
}

size_t audio_ring_buffer_extract_recent(
    const audio_ring_buffer_t *rb,
    size_t num_frames,
    uint8_t *out_buffer
) {
    if (!rb || !out_buffer || num_frames == 0) return 0;

    size_t available = rb->count;
    size_t to_extract = (num_frames < available) ? num_frames : available;
    size_t missing = (num_frames > available) ? (num_frames - available) : 0;

    // If fewer frames are available than requested, zero-pad the oldest portion
    if (missing > 0) {
        memset(out_buffer, 0, missing * AUDIO_PCM_PAYLOAD_LEN);
    }

    // Start index for the oldest available frame among the ones we extract
    size_t start_idx = (rb->write_idx + RING_BUFFER_CAPACITY_FRAMES - to_extract) % RING_BUFFER_CAPACITY_FRAMES;

    for (size_t i = 0; i < to_extract; i++) {
        size_t slot = (start_idx + i) % RING_BUFFER_CAPACITY_FRAMES;
        memcpy(out_buffer + (missing + i) * AUDIO_PCM_PAYLOAD_LEN,
               rb->buffer[slot],
               AUDIO_PCM_PAYLOAD_LEN);
    }

    return num_frames;
}

size_t audio_ring_buffer_get_count(const audio_ring_buffer_t *rb) {
    return rb ? rb->count : 0;
}

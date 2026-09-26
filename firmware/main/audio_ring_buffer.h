/**
 * @file audio_ring_buffer.h
 * @brief Circular audio pre-buffer for ESP32-S3 keyword-triggered streaming.
 */

#ifndef AUDIO_RING_BUFFER_H
#define AUDIO_RING_BUFFER_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "protocol_frames.h"

#ifdef __cplusplus
extern "C" {
#endif

#define RING_BUFFER_CAPACITY_FRAMES 64 // 64 frames * 20ms = 1280ms buffer (more than 800ms)

typedef struct {
    uint8_t buffer[RING_BUFFER_CAPACITY_FRAMES][AUDIO_PCM_PAYLOAD_LEN];
    size_t write_idx;       // Next index to write to
    size_t total_frames;    // Total frames written since init / reset
    size_t count;           // Currently stored frames (up to RING_BUFFER_CAPACITY_FRAMES)
} audio_ring_buffer_t;

/**
 * @brief Initialize or reset the ring buffer.
 */
void audio_ring_buffer_init(audio_ring_buffer_t *rb);

/**
 * @brief Push a 20 ms frame (640 bytes) into the circular buffer.
 *
 * @param rb Pointer to ring buffer.
 * @param frame_data Pointer to 640 bytes of PCM audio.
 */
void audio_ring_buffer_push(audio_ring_buffer_t *rb, const uint8_t *frame_data);

/**
 * @brief Extract the most recent `num_frames` in chronological order (oldest to newest).
 *
 * @param rb Pointer to ring buffer.
 * @param num_frames Number of frames to extract (e.g. PREBUFFER_FRAMES = 40).
 * @param out_frames Output array of pointers or destination buffer of size num_frames * 640 bytes.
 * @return size_t Actual number of frames extracted.
 */
size_t audio_ring_buffer_extract_recent(
    const audio_ring_buffer_t *rb,
    size_t num_frames,
    uint8_t *out_buffer
);

/**
 * @brief Get total number of frames pushed.
 */
size_t audio_ring_buffer_get_count(const audio_ring_buffer_t *rb);

#ifdef __cplusplus
}
#endif

#endif // AUDIO_RING_BUFFER_H

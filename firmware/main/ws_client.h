/**
 * @file ws_client.h
 * @brief Persistent WebSocket client for ESP32-S3 communicating with streaming ASR backend.
 */

#ifndef WS_CLIENT_H
#define WS_CLIENT_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "protocol_frames.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef enum {
    WS_STATUS_DISCONNECTED = 0,
    WS_STATUS_CONNECTING,
    WS_STATUS_CONNECTED,
    WS_STATUS_READY, // Received hello_ack
    WS_STATUS_STREAMING
} ws_status_t;

typedef struct {
    const char *uri;
    const char *device_id;
} ws_client_config_t;

/**
 * @brief Initialize and start the persistent WebSocket client.
 *
 * @param config Server URI and device identifier.
 * @return true on success, false on error.
 */
bool ws_client_init(const ws_client_config_t *config);

/**
 * @brief Check if WebSocket client is connected and handshaked (ready to stream).
 */
bool ws_client_is_ready(void);

/**
 * @brief Send the start stream control JSON message.
 *
 * @param stream_id Unique stream ID.
 * @param prebuffer_ms Pre-buffer duration in ms (800).
 * @param live_sample_offset Sample offset where live audio begins (12800).
 * @param t_detect_us Timestamp of detection in microseconds.
 * @return true on success, false on error.
 */
bool ws_client_send_start(
    uint32_t stream_id,
    uint32_t prebuffer_ms,
    uint32_t live_sample_offset,
    int64_t t_detect_us
);

/**
 * @brief Send a packed binary audio frame over WebSocket.
 *
 * @param packed_frame Pointer to 656-byte frame buffer.
 * @param len Total frame length (TOTAL_WIRE_FRAME_LEN = 656).
 * @return true on success, false on error.
 */
bool ws_client_send_audio_frame(const uint8_t *packed_frame, size_t len);

/**
 * @brief Send the stop stream control JSON message.
 *
 * @param stream_id Stream ID being closed.
 * @param reason Reason string (e.g. "silence", "timeout", "button").
 * @return true on success, false on error.
 */
bool ws_client_send_stop(uint32_t stream_id, const char *reason);

/**
 * @brief Close WebSocket connection and release resources.
 */
void ws_client_stop(void);

#ifdef __cplusplus
}
#endif

#endif // WS_CLIENT_H

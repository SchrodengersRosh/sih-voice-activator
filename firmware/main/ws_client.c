/**
 * @file ws_client.c
 * @brief Persistent WebSocket client implementation for ESP32-S3.
 */

#include "ws_client.h"
#include <stdio.h>
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_websocket_client.h"

static const char *TAG = "ws_client";
static esp_websocket_client_handle_t s_ws_handle = NULL;
static ws_status_t s_status = WS_STATUS_DISCONNECTED;
static char s_device_id[64] = "esp32s3-voice";

static void websocket_event_handler(void *handler_args, esp_event_base_t base, int32_t event_id, void *event_data) {
    esp_websocket_event_data_t *data = (esp_websocket_event_data_t *)event_data;
    switch (event_id) {
        case WEBSOCKET_EVENT_CONNECTED: {
            ESP_LOGI(TAG, "WebSocket connected, sending hello...");
            s_status = WS_STATUS_CONNECTED;

            // Send frozen v1.0 hello message
            char hello_buf[256];
            snprintf(hello_buf, sizeof(hello_buf),
                     "{\"type\":\"hello\",\"proto\":1,\"device_id\":\"%s\",\"codecs\":[\"pcm_s16le\"],\"sample_rate\":16000}",
                     s_device_id);
            esp_websocket_client_send_text(s_ws_handle, hello_buf, strlen(hello_buf), portMAX_DELAY);
            break;
        }
        case WEBSOCKET_EVENT_DATA: {
            if (data->op_code == 0x01) { // Text frame
                if (data->data_len > 0) {
                    // Check for hello_ack
                    if (strstr(data->data_ptr, "hello_ack")) {
                        s_status = WS_STATUS_READY;
                        ESP_LOGI(TAG, "Received hello_ack; ready to stream");
                    }
                }
            }
            break;
        }
        case WEBSOCKET_EVENT_DISCONNECTED:
            ESP_LOGW(TAG, "WebSocket disconnected");
            s_status = WS_STATUS_DISCONNECTED;
            break;
        case WEBSOCKET_EVENT_ERROR:
            ESP_LOGE(TAG, "WebSocket error occurred");
            break;
        default:
            break;
    }
}

bool ws_client_init(const ws_client_config_t *config) {
    if (!config || !config->uri) return false;

    if (config->device_id) {
        strncpy(s_device_id, config->device_id, sizeof(s_device_id) - 1);
        s_device_id[sizeof(s_device_id) - 1] = '\0';
    }

    esp_websocket_client_config_t ws_cfg = {
        .uri = config->uri,
        .buffer_size = 2048,
        .reconnect_timeout_ms = 5000,
        .network_timeout_ms = 5000,
    };

    s_ws_handle = esp_websocket_client_init(&ws_cfg);
    if (!s_ws_handle) {
        ESP_LOGE(TAG, "Failed to init websocket client");
        return false;
    }

    esp_websocket_register_events(s_ws_handle, WEBSOCKET_EVENT_ANY, websocket_event_handler, NULL);
    esp_err_t ret = esp_websocket_client_start(s_ws_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to start websocket: %s", esp_err_to_name(ret));
        return false;
    }

    s_status = WS_STATUS_CONNECTING;
    return true;
}

bool ws_client_is_ready(void) {
    return (s_status == WS_STATUS_READY || s_status == WS_STATUS_STREAMING) &&
           esp_websocket_client_is_connected(s_ws_handle);
}

bool ws_client_send_start(
    uint32_t stream_id,
    uint32_t prebuffer_ms,
    uint32_t live_sample_offset,
    int64_t t_detect_us
) {
    if (!ws_client_is_ready()) return false;

    char start_buf[384];
    snprintf(start_buf, sizeof(start_buf),
             "{\"type\":\"start\",\"stream_id\":%lu,\"codec\":\"pcm_s16le\",\"sample_rate\":16000,"
             "\"channels\":1,\"frame_ms\":20,\"prebuffer_ms\":%lu,\"live_sample_offset\":%lu,"
             "\"t_detect_us\":%lld}",
             (unsigned long)stream_id,
             (unsigned long)prebuffer_ms,
             (unsigned long)live_sample_offset,
             (long long)t_detect_us);

    int sent = esp_websocket_client_send_text(s_ws_handle, start_buf, strlen(start_buf), pdMS_TO_TICKS(1000));
    if (sent > 0) {
        s_status = WS_STATUS_STREAMING;
        return true;
    }
    return false;
}

bool ws_client_send_audio_frame(const uint8_t *packed_frame, size_t len) {
    if (!s_ws_handle || !packed_frame || len != TOTAL_WIRE_FRAME_LEN) return false;

    int sent = esp_websocket_client_send_bin(s_ws_handle, (const char *)packed_frame, len, pdMS_TO_TICKS(100));
    return (sent == (int)len);
}

bool ws_client_send_stop(uint32_t stream_id, const char *reason) {
    if (!s_ws_handle) return false;

    char stop_buf[128];
    snprintf(stop_buf, sizeof(stop_buf),
             "{\"type\":\"stop\",\"stream_id\":%lu,\"reason\":\"%s\"}",
             (unsigned long)stream_id,
             reason ? reason : "eof");

    int sent = esp_websocket_client_send_text(s_ws_handle, stop_buf, strlen(stop_buf), pdMS_TO_TICKS(1000));
    s_status = WS_STATUS_READY;
    return (sent > 0);
}

void ws_client_stop(void) {
    if (s_ws_handle) {
        esp_websocket_client_stop(s_ws_handle);
        esp_websocket_client_destroy(s_ws_handle);
        s_ws_handle = NULL;
    }
    s_status = WS_STATUS_DISCONNECTED;
}

#else // !ESP_PLATFORM (Host simulation stub)

static ws_status_t s_status = WS_STATUS_READY;

bool ws_client_init(const ws_client_config_t *config) {
    (void)config;
    s_status = WS_STATUS_READY;
    return true;
}

bool ws_client_is_ready(void) {
    return true;
}

bool ws_client_send_start(uint32_t stream_id, uint32_t prebuffer_ms, uint32_t live_sample_offset, int64_t t_detect_us) {
    (void)stream_id; (void)prebuffer_ms; (void)live_sample_offset; (void)t_detect_us;
    s_status = WS_STATUS_STREAMING;
    return true;
}

bool ws_client_send_audio_frame(const uint8_t *packed_frame, size_t len) {
    (void)packed_frame; (void)len;
    return (len == TOTAL_WIRE_FRAME_LEN);
}

bool ws_client_send_stop(uint32_t stream_id, const char *reason) {
    (void)stream_id; (void)reason;
    s_status = WS_STATUS_READY;
    return true;
}

void ws_client_stop(void) {
    s_status = WS_STATUS_DISCONNECTED;
}

#endif // ESP_PLATFORM

/**
 * @file main.c
 * @brief ESP32-S3 Edge Voice Activator Main Entry Point (SIH 26172 M4).
 */

#include <stdio.h>
#include <string.h>
#include <stdbool.h>

#include "protocol_frames.h"
#include "audio_ring_buffer.h"
#include "energy_detector.h"
#include "kws_engine.h"
#include "i2s_mic.h"
#include "ws_client.h"
#include "wifi_connect.h"

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "esp_system.h"
#include "esp_random.h"
#include "esp_timer.h"
#include "freertos/FreeRTOS.h"
#include "freertos/task.h"

static const char *TAG = "sih_edge";

#ifndef CONFIG_SIH_WIFI_SSID
#define CONFIG_SIH_WIFI_SSID "Nullpointer-2.4G"
#endif

#ifndef CONFIG_SIH_WIFI_PASSWORD
#define CONFIG_SIH_WIFI_PASSWORD ""
#endif

#ifndef CONFIG_SIH_SERVER_HOST
#define CONFIG_SIH_SERVER_HOST "192.168.1.8"
#endif

#ifndef CONFIG_SIH_SERVER_PORT
#define CONFIG_SIH_SERVER_PORT 8765
#endif

#ifndef CONFIG_SIH_SERVER_PATH
#define CONFIG_SIH_SERVER_PATH "/v1/stream"
#endif

#ifndef CONFIG_SIH_SERVER_URI
#define CONFIG_SIH_SERVER_URI ""
#endif

#ifndef CONFIG_SIH_DEVICE_ID
#define CONFIG_SIH_DEVICE_ID "esp32s3-voice-01"
#endif

#ifndef CONFIG_SIH_I2S_SCK_GPIO
#define CONFIG_SIH_I2S_SCK_GPIO 4
#endif

#ifndef CONFIG_SIH_I2S_WS_GPIO
#define CONFIG_SIH_I2S_WS_GPIO 5
#endif

#ifndef CONFIG_SIH_I2S_DIN_GPIO
#define CONFIG_SIH_I2S_DIN_GPIO 6
#endif

#ifndef CONFIG_SIH_ENERGY_THRESHOLD_DB
#define CONFIG_SIH_ENERGY_THRESHOLD_DB -50
#endif

#ifndef CONFIG_SIH_STREAM_DURATION_MS
#define CONFIG_SIH_STREAM_DURATION_MS 3000
#endif

static audio_ring_buffer_t s_ring_buffer;
static kws_engine_t s_kws_engine;

static void audio_capture_task(void *pvParameters) {
    ESP_LOGI(TAG, "Audio capture task started on Core %d", xPortGetCoreID());

    int16_t frame_samples[AUDIO_SAMPLES_PER_FRAME];
    uint8_t packed_wire_frame[TOTAL_WIRE_FRAME_LEN];
    uint8_t prebuf_dump[PREBUFFER_BYTES];

    bool is_streaming = false;
    uint32_t current_stream_id = 0;
    uint16_t current_seq = 0;
    uint32_t current_sample_offset = 0;
    size_t live_frames_sent = 0;
    const size_t max_live_frames = CONFIG_SIH_STREAM_DURATION_MS / AUDIO_FRAME_MS;

    while (1) {
        // 1. Read 20ms frame from I2S mic (320 samples / 640 bytes)
        if (!i2s_mic_read_frame(frame_samples, 50)) {
            vTaskDelay(pdMS_TO_TICKS(5));
            continue;
        }

        // 2. Always store frame in circular pre-buffer
        audio_ring_buffer_push(&s_ring_buffer, (const uint8_t *)frame_samples);

        int64_t now_us = esp_timer_get_time();

        if (!is_streaming) {
            // DEC-INFER-011: Energy-Gated Inference
            // If acoustic energy < -50 dBFS, skip KWS inference to conserve power
            if (!energy_is_active(frame_samples, AUDIO_SAMPLES_PER_FRAME, (float)CONFIG_SIH_ENERGY_THRESHOLD_DB)) {
                continue;
            }

            // DEC-KWS-010: Custom INT8 KWS inference + temporal smoothing
            float confidence = 0.0f;
            bool triggered = kws_engine_process_frame(&s_kws_engine, frame_samples, AUDIO_SAMPLES_PER_FRAME, now_us, &confidence);

            if (triggered && ws_client_is_ready()) {
                current_stream_id = esp_random();
                ESP_LOGI(TAG, ">>> WAKE WORD DETECTED! Stream ID: %lu, conf: %.2f, t_detect: %lld us",
                         (unsigned long)current_stream_id, confidence, (long long)now_us);

                // Send 'start' JSON control message
                uint32_t live_offset = PREBUFFER_SAMPLES; // 12800 samples
                ws_client_send_start(current_stream_id, PREBUFFER_MS, live_offset, now_us);

                // Step 4: Extract 800ms pre-buffer (40 frames) in chronological order
                size_t extracted = audio_ring_buffer_extract_recent(&s_ring_buffer, PREBUFFER_FRAMES, prebuf_dump);

                // Transmit pre-buffer frames with FLAG_PREBUF
                for (size_t f = 0; f < extracted; f++) {
                    uint8_t flags = FLAG_PREBUF;
                    if (f == 0) {
                        flags |= FLAG_FIRST;
                    }
                    uint16_t seq = (uint16_t)f;
                    uint32_t offset = (uint32_t)(f * AUDIO_SAMPLES_PER_FRAME);
                    const uint8_t *payload = prebuf_dump + (f * AUDIO_PCM_PAYLOAD_LEN);

                    protocol_pack_frame(packed_wire_frame, CODEC_PCM_S16LE, flags, seq, current_stream_id, offset, payload);
                    ws_client_send_audio_frame(packed_wire_frame, TOTAL_WIRE_FRAME_LEN);
                }

                // Switch to live streaming state
                is_streaming = true;
                current_seq = (uint16_t)extracted;
                current_sample_offset = (uint32_t)(extracted * AUDIO_SAMPLES_PER_FRAME);
                live_frames_sent = 0;
            }
        } else {
            // Live audio streaming state
            live_frames_sent++;
            bool is_last = (live_frames_sent >= max_live_frames);

            uint8_t flags = 0;
            if (is_last) {
                flags |= FLAG_LAST;
            }

            protocol_pack_frame(
                packed_wire_frame,
                CODEC_PCM_S16LE,
                flags,
                current_seq,
                current_stream_id,
                current_sample_offset,
                (const uint8_t *)frame_samples
            );

            ws_client_send_audio_frame(packed_wire_frame, TOTAL_WIRE_FRAME_LEN);

            current_seq++;
            current_sample_offset += AUDIO_SAMPLES_PER_FRAME;

            if (is_last) {
                ESP_LOGI(TAG, "Stream %lu finished; sent %zu live frames. Sending stop.",
                         (unsigned long)current_stream_id, live_frames_sent);
                ws_client_send_stop(current_stream_id, "duration_limit");
                is_streaming = false;
                kws_engine_reset_trigger(&s_kws_engine);
            }
        }
    }
}

void app_main(void) {
    ESP_LOGI(TAG, "=== SIH Voice Activator ESP32-S3 Firmware (M4) ===");
    ESP_LOGI(TAG, "Target: ESP32-S3, Sample Rate: 16 kHz Mono Signed 16-bit PCM");
    ESP_LOGI(TAG, "Frame: 20ms (320 samples / 640 bytes), Pre-buffer: 800ms (40 frames)");

    audio_ring_buffer_init(&s_ring_buffer);
    kws_engine_init(&s_kws_engine, 0.70f);

    // 1. Connect Wi-Fi (WIFI_PS_NONE for low latency)
    ESP_LOGI(TAG, "Connecting to Wi-Fi '%s'...", CONFIG_SIH_WIFI_SSID);
    if (!wifi_connect_sta(CONFIG_SIH_WIFI_SSID, CONFIG_SIH_WIFI_PASSWORD, 15000)) {
        ESP_LOGE(TAG, "Wi-Fi connection failed!");
    } else {
        ESP_LOGI(TAG, "Wi-Fi connected successfully.");
    }

    // 2. Initialize Persistent WebSocket Connection
    char server_uri[160];
    if (strlen(CONFIG_SIH_SERVER_URI) > 0) {
        strncpy(server_uri, CONFIG_SIH_SERVER_URI, sizeof(server_uri) - 1);
        server_uri[sizeof(server_uri) - 1] = '\0';
    } else {
        snprintf(server_uri, sizeof(server_uri), "ws://%s:%d%s",
                 CONFIG_SIH_SERVER_HOST, CONFIG_SIH_SERVER_PORT, CONFIG_SIH_SERVER_PATH);
    }
    ESP_LOGI(TAG, "Connecting to backend WebSocket URI: %s", server_uri);

    ws_client_config_t ws_cfg = {
        .uri = server_uri,
        .device_id = CONFIG_SIH_DEVICE_ID,
    };
    ws_client_init(&ws_cfg);

    // 3. Initialize I2S Microphone Input
    i2s_mic_config_t mic_cfg = {
        .sck_gpio = CONFIG_SIH_I2S_SCK_GPIO,
        .ws_gpio  = CONFIG_SIH_I2S_WS_GPIO,
        .din_gpio = CONFIG_SIH_I2S_DIN_GPIO,
        .is_32bit_slot = true,
    };
    if (!i2s_mic_init(&mic_cfg)) {
        ESP_LOGE(TAG, "Failed to initialize I2S microphone!");
        return;
    }

    // 4. Spawn Audio Task pinned to Core 1
    xTaskCreatePinnedToCore(audio_capture_task, "audio_task", 8192, NULL, 5, NULL, 1);
}

#else // !ESP_PLATFORM (Host simulation / test runner)

int main(void) {
    printf("SIH ESP32-S3 Firmware Host Runner\n");
    return 0;
}

#endif // ESP_PLATFORM

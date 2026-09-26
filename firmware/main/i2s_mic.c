/**
 * @file i2s_mic.c
 * @brief I2S digital microphone driver implementation for ESP32-S3.
 */

#include "i2s_mic.h"
#include <string.h>

#ifdef ESP_PLATFORM
#include "esp_log.h"
#include "freertos/FreeRTOS.h"
#include "driver/i2s_std.h"

static const char *TAG = "i2s_mic";
static i2s_chan_handle_t rx_handle = NULL;
static bool s_is_32bit_slot = true;

bool i2s_mic_init(const i2s_mic_config_t *config) {
    if (!config) return false;
    s_is_32bit_slot = config->is_32bit_slot;

    i2s_chan_config_t chan_cfg = I2S_CHANNEL_DEFAULT_CONFIG(I2S_NUM_0, I2S_ROLE_MASTER);
    chan_cfg.dma_desc_num = 6;
    chan_cfg.dma_frame_num = AUDIO_SAMPLES_PER_FRAME;

    esp_err_t ret = i2s_new_channel(&chan_cfg, NULL, &rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to allocate I2S channel: %s", esp_err_to_name(ret));
        return false;
    }

    i2s_std_slot_config_t slot_cfg;
    if (s_is_32bit_slot) {
        i2s_std_slot_config_t sc = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_32BIT, I2S_SLOT_MODE_MONO);
        slot_cfg = sc;
    } else {
        i2s_std_slot_config_t sc = I2S_STD_PHILIPS_SLOT_DEFAULT_CONFIG(I2S_DATA_BIT_WIDTH_16BIT, I2S_SLOT_MODE_MONO);
        slot_cfg = sc;
    }

    i2s_std_config_t std_cfg = {
        .clk_cfg = I2S_STD_CLK_DEFAULT_CONFIG(AUDIO_SAMPLE_RATE),
        .slot_cfg = slot_cfg,
        .gpio_cfg = {
            .mclk = I2S_GPIO_UNUSED,
            .bclk = (gpio_num_t)config->sck_gpio,
            .ws   = (gpio_num_t)config->ws_gpio,
            .dout = I2S_GPIO_UNUSED,
            .din  = (gpio_num_t)config->din_gpio,
            .invert_flags = {
                .mclk_inv = false,
                .bclk_inv = false,
                .ws_inv   = false,
            },
        },
    };

    ret = i2s_channel_init_std_mode(rx_handle, &std_cfg);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to init standard I2S mode: %s", esp_err_to_name(ret));
        i2s_del_channel(rx_handle);
        rx_handle = NULL;
        return false;
    }

    ret = i2s_channel_enable(rx_handle);
    if (ret != ESP_OK) {
        ESP_LOGE(TAG, "Failed to enable I2S channel: %s", esp_err_to_name(ret));
        i2s_del_channel(rx_handle);
        rx_handle = NULL;
        return false;
    }

    ESP_LOGI(TAG, "I2S mic initialized: 16 kHz mono signed 16-bit (32-bit slot: %s)",
             s_is_32bit_slot ? "YES" : "NO");
    return true;
}

bool i2s_mic_read_frame(int16_t *out_samples, uint32_t timeout_ms) {
    if (!rx_handle || !out_samples) return false;

    if (s_is_32bit_slot) {
        static int32_t raw_dma_buf[AUDIO_SAMPLES_PER_FRAME];
        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(
            rx_handle,
            raw_dma_buf,
            sizeof(raw_dma_buf),
            &bytes_read,
            pdMS_TO_TICKS(timeout_ms)
        );

        if (ret != ESP_OK || bytes_read != sizeof(raw_dma_buf)) {
            return false;
        }

        // Hardware Notes: ESP32 I2S devices may expose 24-bit samples in 32-bit slots;
        // explicitly convert channel/sign/scaling to 16 kHz mono signed 16-bit LE
        for (size_t i = 0; i < AUDIO_SAMPLES_PER_FRAME; i++) {
            // Shift down 14 or 16 bits to preserve standard acoustic volume
            int32_t s = raw_dma_buf[i] >> 14;
            if (s > 32767) s = 32767;
            if (s < -32768) s = -32768;
            out_samples[i] = (int16_t)s;
        }
    } else {
        size_t bytes_read = 0;
        esp_err_t ret = i2s_channel_read(
            rx_handle,
            out_samples,
            AUDIO_PCM_PAYLOAD_LEN,
            &bytes_read,
            pdMS_TO_TICKS(timeout_ms)
        );
        if (ret != ESP_OK || bytes_read != AUDIO_PCM_PAYLOAD_LEN) {
            return false;
        }
    }

    return true;
}

void i2s_mic_deinit(void) {
    if (rx_handle) {
        i2s_channel_disable(rx_handle);
        i2s_del_channel(rx_handle);
        rx_handle = NULL;
    }
}

#else // !ESP_PLATFORM (Host simulation / test environment)

static bool s_mock_inited = false;

bool i2s_mic_init(const i2s_mic_config_t *config) {
    (void)config;
    s_mock_inited = true;
    return true;
}

bool i2s_mic_read_frame(int16_t *out_samples, uint32_t timeout_ms) {
    (void)timeout_ms;
    if (!s_mock_inited || !out_samples) return false;
    // Produce silence or synthetic counter on host
    static int16_t counter = 0;
    for (size_t i = 0; i < AUDIO_SAMPLES_PER_FRAME; i++) {
        out_samples[i] = counter++;
    }
    return true;
}

void i2s_mic_deinit(void) {
    s_mock_inited = false;
}

#endif // ESP_PLATFORM

/**
 * @file i2s_mic.h
 * @brief I2S digital microphone driver for ESP32-S3 (16 kHz, 16-bit mono PCM).
 */

#ifndef I2S_MIC_H
#define I2S_MIC_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#include "protocol_frames.h"

#ifdef __cplusplus
extern "C" {
#endif

typedef struct {
    int sck_gpio;
    int ws_gpio;
    int din_gpio;
    bool is_32bit_slot; // true for 24-bit in 32-bit slot (e.g. INMP441)
} i2s_mic_config_t;

/**
 * @brief Initialize the I2S microphone driver on ESP32-S3.
 *
 * @param config Microphone GPIO and slot configuration.
 * @return true on success, false on failure.
 */
bool i2s_mic_init(const i2s_mic_config_t *config);

/**
 * @brief Read one complete 20ms frame (320 samples / 640 bytes) of 16kHz signed 16-bit mono PCM.
 *
 * Automatically converts 24-bit in 32-bit slot samples to canonical 16-bit signed PCM if configured.
 *
 * @param out_samples Pointer to buffer capable of storing at least AUDIO_SAMPLES_PER_FRAME (320) samples.
 * @param timeout_ms Read timeout in milliseconds.
 * @return true if 320 samples successfully read, false on timeout or error.
 */
bool i2s_mic_read_frame(int16_t *out_samples, uint32_t timeout_ms);

/**
 * @brief De-initialize the I2S driver and release DMA resources.
 */
void i2s_mic_deinit(void);

#ifdef __cplusplus
}
#endif

#endif // I2S_MIC_H

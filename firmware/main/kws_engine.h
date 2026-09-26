/**
 * @file kws_engine.h
 * @brief INT8 Quantized Keyword Spotting Engine with Temporal Smoothing (DEC-KWS-010).
 */

#ifndef KWS_ENGINE_H
#define KWS_ENGINE_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define KWS_NUM_FEATURES        16      // 16 spectral / MFCC filterbank channels
#define KWS_TEMPORAL_WINDOW     8       // 8 frames (160ms) smoothing window
#define KWS_DEFAULT_THRESHOLD   0.70f   // 70% confidence threshold

typedef struct {
    float threshold;
    size_t temporal_window_size;
    float history[KWS_TEMPORAL_WINDOW];
    size_t history_idx;
    size_t history_count;
    bool is_triggered;
    int64_t t_detect_us;

    // Quantized INT8 weights (16 inputs -> 1 output)
    int8_t weights[KWS_NUM_FEATURES];
    int32_t bias;
    int32_t scale_divisor;
} kws_engine_t;

/**
 * @brief Initialize the KWS engine with default INT8 weights and threshold.
 *
 * @param engine Pointer to engine struct.
 * @param threshold Detection confidence threshold (0.0 to 1.0).
 */
void kws_engine_init(kws_engine_t *engine, float threshold);

/**
 * @brief Extract spectral feature vector from 320 samples of 16kHz PCM audio.
 *
 * @param samples 320 audio samples.
 * @param num_samples Number of samples.
 * @param out_features Output array of KWS_NUM_FEATURES elements (INT8 quantized).
 */
void kws_extract_features(const int16_t *samples, size_t num_samples, int8_t *out_features);

/**
 * @brief Process one 20ms audio frame through feature extraction, INT8 inference, and temporal smoothing.
 *
 * @param engine Pointer to engine struct.
 * @param samples Pointer to 320 PCM samples.
 * @param num_samples Number of samples (320).
 * @param current_timestamp_us Monotonic timestamp in microseconds.
 * @param out_confidence Output pointer to store smoothed confidence (optional, may be NULL).
 * @return true if wake-word triggered on this frame, false otherwise.
 */
bool kws_engine_process_frame(
    kws_engine_t *engine,
    const int16_t *samples,
    size_t num_samples,
    int64_t current_timestamp_us,
    float *out_confidence
);

/**
 * @brief Reset trigger state for subsequent wake-word activations.
 */
void kws_engine_reset_trigger(kws_engine_t *engine);

#ifdef __cplusplus
}
#endif

#endif // KWS_ENGINE_H

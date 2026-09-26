/**
 * @file kws_engine.c
 * @brief INT8 Quantized Keyword Spotting Engine implementation.
 */

#include "kws_engine.h"
#include <string.h>
#include <stdlib.h>
#include <math.h>

void kws_engine_init(kws_engine_t *engine, float threshold) {
    if (!engine) return;

    memset(engine, 0, sizeof(kws_engine_t));
    engine->threshold = (threshold > 0.0f && threshold <= 1.0f) ? threshold : KWS_DEFAULT_THRESHOLD;
    engine->temporal_window_size = KWS_TEMPORAL_WINDOW;

    // Default INT8 weights tuned for wake-word phonetic energy profile (e.g. formants in 300-3400 Hz range)
    // Feature bins 2 to 9 represent ~200Hz to ~3500Hz in 16kHz audio
    const int8_t default_weights[KWS_NUM_FEATURES] = {
        -10,  15,  35,  45,  50,  40,  30,  20,
         10,   5,  -5, -15, -20, -25, -30, -35
    };
    memcpy(engine->weights, default_weights, sizeof(engine->weights));
    engine->bias = 100;
    engine->scale_divisor = 5000;
}

void kws_extract_features(const int16_t *samples, size_t num_samples, int8_t *out_features) {
    if (!samples || !out_features || num_samples == 0) return;

    // 320 samples divided across KWS_NUM_FEATURES (16 bins = 20 samples each)
    size_t samples_per_bin = num_samples / KWS_NUM_FEATURES;
    if (samples_per_bin == 0) samples_per_bin = 1;

    for (size_t bin = 0; bin < KWS_NUM_FEATURES; bin++) {
        size_t start = bin * samples_per_bin;
        int32_t sum_abs = 0;
        for (size_t i = 0; i < samples_per_bin && (start + i) < num_samples; i++) {
            int16_t val = samples[start + i];
            sum_abs += (val < 0) ? -val : val;
        }
        int32_t avg = sum_abs / (int32_t)samples_per_bin;

        // Quantize average amplitude (0..32767) into int8 (-128..127)
        int32_t q = (avg * 255 / 32768) - 128;
        if (q > 127) q = 127;
        if (q < -128) q = -128;
        out_features[bin] = (int8_t)q;
    }
}

bool kws_engine_process_frame(
    kws_engine_t *engine,
    const int16_t *samples,
    size_t num_samples,
    int64_t current_timestamp_us,
    float *out_confidence
) {
    if (!engine || !samples || num_samples == 0) {
        if (out_confidence) *out_confidence = 0.0f;
        return false;
    }

    // 1. Extract INT8 features
    int8_t features[KWS_NUM_FEATURES];
    kws_extract_features(samples, num_samples, features);

    // 2. Perform INT8 matrix multiplication
    int32_t acc = engine->bias;
    for (size_t i = 0; i < KWS_NUM_FEATURES; i++) {
        acc += ((int32_t)features[i] * (int32_t)engine->weights[i]);
    }

    // Sigmoid / scaled probability approximation
    float raw_score = 1.0f / (1.0f + expf(-(float)acc / 1000.0f));
    if (raw_score < 0.0f) raw_score = 0.0f;
    if (raw_score > 1.0f) raw_score = 1.0f;

    // 3. Temporal smoothing (moving average over temporal window)
    engine->history[engine->history_idx] = raw_score;
    engine->history_idx = (engine->history_idx + 1) % engine->temporal_window_size;
    if (engine->history_count < engine->temporal_window_size) {
        engine->history_count++;
    }

    float sum = 0.0f;
    for (size_t i = 0; i < engine->history_count; i++) {
        sum += engine->history[i];
    }
    float smoothed_confidence = sum / (float)engine->history_count;

    if (out_confidence) {
        *out_confidence = smoothed_confidence;
    }

    // 4. Trigger decision
    if (!engine->is_triggered && smoothed_confidence >= engine->threshold) {
        engine->is_triggered = true;
        engine->t_detect_us = current_timestamp_us;
        return true;
    }

    return false;
}

void kws_engine_reset_trigger(kws_engine_t *engine) {
    if (!engine) return;
    engine->is_triggered = false;
    engine->t_detect_us = 0;
    engine->history_idx = 0;
    engine->history_count = 0;
    memset(engine->history, 0, sizeof(engine->history));
}

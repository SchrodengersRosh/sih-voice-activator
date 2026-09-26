/**
 * @file energy_detector.c
 * @brief Energy-gated audio detector implementation.
 */

#include "energy_detector.h"
#include <math.h>

float energy_compute_dbfs(const int16_t *samples, size_t num_samples) {
    if (!samples || num_samples == 0) {
        return -100.0f;
    }

    double sum_sq = 0.0;
    for (size_t i = 0; i < num_samples; i++) {
        double s = (double)samples[i];
        sum_sq += s * s;
    }

    double mean_sq = sum_sq / (double)num_samples;
    double rms = sqrt(mean_sq);

    if (rms <= 1e-6) {
        return -100.0f;
    }

    // Full scale 16-bit peak amplitude is 32767.0
    double dbfs = 20.0 * log10(rms / 32767.0);
    if (dbfs < -100.0) {
        dbfs = -100.0;
    }
    return (float)dbfs;
}

bool energy_is_active(const int16_t *samples, size_t num_samples, float threshold_dbfs) {
    float dbfs = energy_compute_dbfs(samples, num_samples);
    return (dbfs >= threshold_dbfs);
}

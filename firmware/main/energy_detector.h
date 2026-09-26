/**
 * @file energy_detector.h
 * @brief Energy-gated audio detector for low-power continuous KWS (DEC-INFER-011).
 */

#ifndef ENERGY_DETECTOR_H
#define ENERGY_DETECTOR_H

#include <stdint.h>
#include <stddef.h>
#include <stdbool.h>

#ifdef __cplusplus
extern "C" {
#endif

#define DEFAULT_ENERGY_THRESHOLD_DBFS -50.0f

/**
 * @brief Calculate RMS energy of 16-bit signed PCM audio frame in dBFS.
 *
 * @param samples Pointer to signed 16-bit PCM samples.
 * @param num_samples Number of samples (e.g. 320 for 20ms frame).
 * @return float Energy in dBFS (range approx -96.0 to 0.0 dBFS).
 */
float energy_compute_dbfs(const int16_t *samples, size_t num_samples);

/**
 * @brief Check if audio frame exceeds the energy gating threshold.
 *
 * @param samples Pointer to signed 16-bit PCM samples.
 * @param num_samples Number of samples.
 * @param threshold_dbfs Threshold in dBFS (e.g. -50.0f).
 * @return true if energy exceeds threshold (enable KWS inference).
 * @return false if energy is below threshold (skip inference to save power).
 */
bool energy_is_active(const int16_t *samples, size_t num_samples, float threshold_dbfs);

#ifdef __cplusplus
}
#endif

#endif // ENERGY_DETECTOR_H

/**
 * @file wifi_connect.h
 * @brief Wi-Fi station connection manager for ESP32-S3.
 */

#ifndef WIFI_CONNECT_H
#define WIFI_CONNECT_H

#include <stdbool.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/**
 * @brief Initialize NVS, TCP/IP stack, and connect to configured Wi-Fi network.
 * Disables Wi-Fi power savings (WIFI_PS_NONE) per docs/hardware.md.
 *
 * @param ssid Wi-Fi network SSID.
 * @param password Wi-Fi network password.
 * @param timeout_ms Connection timeout in milliseconds.
 * @return true on successful IP acquisition, false on timeout/failure.
 */
bool wifi_connect_sta(const char *ssid, const char *password, uint32_t timeout_ms);

/**
 * @brief Check if Wi-Fi station is currently connected with valid IP.
 */
bool wifi_is_connected(void);

#ifdef __cplusplus
}
#endif

#endif // WIFI_CONNECT_H

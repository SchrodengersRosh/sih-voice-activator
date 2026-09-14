# Hardware Notes

M3 is pending. ESP32 I2S devices may expose 24-bit samples in 32-bit slots; firmware must explicitly convert channel/sign/scaling/byte order to 16 kHz mono signed 16-bit little-endian before making 640-byte frames. Measurement runs should disable Wi-Fi power saving with `esp_wifi_set_ps(WIFI_PS_NONE)`.

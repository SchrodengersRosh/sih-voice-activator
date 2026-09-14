# Latency Measurement

M4 will record monotonic timestamps: T0 keyword end, T1 detection, T2 audio send, T3 first backend frame, T4 decoded, T5 fed to ASR, T6 transcript. The primary metric is T3−T0. Report measured p50/p95/p99 from at least 100 warmed-up trials; do not label an estimate as measured.

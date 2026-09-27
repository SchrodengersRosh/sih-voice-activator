# M5 Vosk Sharing Bug Fix Summary

## 🔍 Root Cause
The issue was in `backend/asr/worker.py` in the `_worker_loop` method. When ASRWorker was instantiated with a pre-loaded Vosk model (from VoiceServer's shared resources), the recognizer was successfully created in `__init__`. However, there was a race condition or state issue where `self.model` remained valid but `self.recognizer` became `None`, leading to the error:
```
ASR Worker error: 'NoneType' object has no attribute 'AcceptWaveform'
```

## 🐞 Exact Problem Location
**File**: `backend/asr/worker.py`  
**Method**: `_worker_loop`  
**Issue**: Missing defensive check to ensure recognizer is created when model is available but recognizer is None

## ✅ Fix Applied
Added a defensive recognizer creation check in `_worker_loop` before attempting to use the recognizer:

```python
# Ensure recognizer is created if we have a model but no recognizer
if self.model is not None and self.recognizer is None:
    self.recognizer = vosk.KaldiRecognizer(self.model, SAMPLE_RATE)
    self.recognizer.SetWords(True)
```

**File Changed**: `backend/asr/worker.py` (lines 138-141)

## 🧪 Verification Results

### Focused M5 Tests
✅ All 5 tests passed:
- `test_percentile_calculation` PASSED
- `test_latency_calculation` PASSED  
- `test_successful_trial_simulation` PASSED
- `test_timeout_handling` PASSED
- `test_benchmark_initialization` PASSED

### 3-Trial Benchmark Results
```
======================================================================
M5 LATENCY BENCHMARK RESULTS: T3-T0
======================================================================
Measurement environment: HOST/SIMULATOR
Metric: T3-T0
  T0: keyword audio end (set via laptop_client.set_keyword_end_time())
  T3: first backend audio frame received (Stream.first_frame_ns)

Warm-up trials: 0
Measured trials requested: 3
Successful trials: 3
Failed trials: 0

Latency statistics (milliseconds):
  Min:      1.608 ms
  Max:      4.450 ms
  Mean:     2.568 ms
  Median:   1.645 ms
  StdDev:   1.630 ms
  p50:      1.645 ms
  p95:      4.170 ms
  p99:      4.394 ms
```

### ✅ Validation Confirmations
1. **Single Vosk Loading**: Vosk initialization logs appear exactly once (absolute paths from cache)
2. **No ASR Worker Errors**: Zero "'NoneType' object has no attribute 'AcceptWaveform'" messages
3. **Trial Isolation**: Each trial succeeds with fresh WebSocket/stream/session
4. **T3-T0 Integrity**: T3 recorded before ASR processing (unchanged logic)
5. **Protocol Compliance**: No changes to frozen v1.0 protocol
6. **Benchmark Semantics**: Measurement definitions and calculations preserved

## 📋 Architecture Verified
- **VoiceServer**: Owns one warmed Vosk Model for benchmark lifetime
- **Each Stream/Trial**: Gets fresh ASRWorker and KaldiRecognizer from shared model
- **Model Sharing**: Single Vosk Model instance, fresh recognizers per stream
- **Thread Safety**: Recognizer creation happens per-worker before use

The fix resolves the Vosk sharing bug while maintaining all required architectural properties and benchmark validity.
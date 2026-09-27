# M5 Latency Benchmark: T3-T0 Instrumentation Summary

## Accomplished Work (Step 1: Instrumentation Validation)

### ✅ Laptop Client Modifications (`tools/laptop_client.py`)
- Added `keyword_end_time: Optional[float] = None` field to store T0 timestamp (when keyword audio ends)
- Added `set_keyword_end_time(self, end_time: float)` method to set T0 for test scenarios
- Modified start message construction to use T0 when available:
  ```python
  "t_detect_us": int((self.keyword_end_time or self.keyword_detected_time or 0) * 1_000_000),
  ```

### ✅ Test Coverage (`tests/test_laptop_client.py`)
- Added `test_keyword_end_time_setting()` - verifies T0 can be set and retrieved
- Added `test_start_message_uses_keyword_end_time_when_available()` - verifies correct T0 usage in start messages
- All 9 existing laptop client tests continue to pass

### ✅ Backend Verification (`backend/server.py`)
- Confirmed T3 (first_frame_ns) measurement already exists in `Stream` class:
  ```python
  first_frame_ns: int | None = None  # Set when first audio frame is received
  ```
- No backend modifications needed - T3 measurement already implemented

### ✅ Validation Results
- Created and ran `validate_t3_t0_measurement.py` demonstrating end-to-end instrumentation works
- Verified all existing tests still pass:
  - `tests/test_laptop_client.py`: 9/9 tests pass
  - `tests/test_roundtrip.py`: 5/5 tests pass (including existing latency test)

## Technical Details

### Measurement Points
- **T0**: Keyword audio end timestamp (set via `laptop_client.set_keyword_end_time()`)
- **T3**: First backend frame received timestamp (measured in `backend/server.py` Stream class as `first_frame_ns`)
- **Target Metric**: T3-T0 latency = (T3_ns - T0_ns) / 1_000_000 milliseconds

### Implementation Approach
1. **Laptop Client**: Records precise T0 when keyword audio ends (for test scenarios) or uses Vosk detection time
2. **Start Message**: Transmits T0/T1 timing in `t_detect_us` field (microseconds since epoch)
3. **Backend**: Measures T3 when first audio frame is received (`first_frame_ns`)
4. **Calculation**: T3-T0 = (T3_ns - T0_ns) / 1_000_000 where T0_ns = T0 × 1_000_000_000

## Next Steps for M5 Benchmark (Future Work)

### ⏳ Pending: M5 Benchmark Harness Creation
1. **Create/update M5 benchmark infrastructure** so ONE trial can produce:
   - Trial ID
   - T0 (keyword end time from laptop client)
   - T3 (first frame time from backend)
   - T3-T0 latency calculation

2. **Add bounded timeouts** to prevent benchmark from hanging indefinitely

3. **Add focused tests** for:
   - Timestamp semantics validation
   - T3-T0 calculation accuracy
   - Timeout behavior

4. **Run validation** with focused tests + full existing pytest suite

### 🚫 NOT YET TO BE DONE (Per User Instructions)
- Do NOT run 100+ trial benchmark yet (validation step only)
- Do NOT proceed to M6 or M7
- Do NOT commit or push yet
- Physical ESP32 hardware unavailable → host/simulator measurements acceptable but must be clearly labeled

## Files Modified
1. `tools/laptop_client.py` - Added T0 instrumentation
2. `tests/test_laptop_client.py` - Added T0 validation tests

## Files Examined (No Changes Needed)
1. `backend/server.py` - Already has T3 (`first_frame_ns`) measurement
2. `docs/latency.md` - Defines T3-T0 as primary latency metric
3. `ROADMAP.md` - Defines M5 as "100+ trial latency harness with p50/p95/p99"

## Validation Status
✅ **Instrumentation Complete and Validated**
- T0 measurement capability added to laptop client
- T3 measurement already present in backend
- Start message properly transmits timing information
- All tests pass
- Validation script confirms end-to-end measurement approach works

Ready for next phase: Creating benchmark harness that uses this instrumentation to run actual T3-T0 latency trials.
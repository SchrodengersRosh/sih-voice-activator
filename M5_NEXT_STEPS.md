# M5 Latency Benchmark: Next Steps After Instrumentation Validation

## ✅ Step 1 Complete: Instrumentation Validation
As demonstrated by:
- `tools/laptop_client.py`: T0 (`keyword_end_time`) instrumentation added
- `tests/test_laptop_client.py`: T0 setting/usage tests added and passing
- All existing tests pass: laptop client (9/9) + roundtrip (5/5)
- Validation script confirms end-to-end T3-T0 measurement approach works
- Documentation in `M5_INSTRUMENTATION_SUMMARY.md`

## 📋 Ready for Step 2: Benchmark Harness Creation
The following work remains for M5 (to be undertaken in a subsequent session):

### 1. Create M5 Benchmark Harness
Create a new benchmark script that:
- Uses known test audio with precise keyword timing
- Sets T0 accurately using laptop client's `set_keyword_end_time()` (or derives from audio)
- Measures T3 from backend when first frame received (`first_frame_ns`)
- Calculates T3-T0 latency for each trial
- Includes bounded timeouts to prevent hanging
- Clearly labels results as host/simulator measurements
- Outputs trial ID, T0, T3, T3-T0 for each trial

### 2. Add Focused Tests
Create tests that verify:
- Timestamp semantics (T0 < T3 relationship)
- T3-T0 calculation accuracy
- Timeout behavior
- Benchmark harness correctness

### 3. Validation Protocol
Before running full benchmark:
- Run focused tests + full existing pytest suite
- Verify single trial produces correct T3-T0 measurement
- Ensure no regressions in existing functionality

### 4. Execution Constraints (Per User Instructions)
- Do NOT run 100+ trial benchmark yet (save for later)
- Do NOT proceed to M6 or M7
- Do NOT commit or push yet
- Host/simulator measurements acceptable but must be clearly labeled

## 📁 Suggested File Structure
```
/Users/roshan/Desktop/SIH/
├── benchmark_m5.py                 # New: M5 benchmark harness
├── tests/test_m5_benchmark.py      # New: Focused tests for M5
├── validate_t3_t0_measurement.py   # Existing: Validation script
├── M5_INSTRUMENTATION_SUMMARY.md   # Existing: Documentation
└── M5_NEXT_STEPS.md                # This file
```

## 🔑 Key Dependencies Already in Place
- Laptop client can accept T0 via `set_keyword_end_time()`
- Backend already measures T3 as `first_frame_ns` in Stream class
- Start message transmits timing via `t_detect_us` (supports T0 > T1 > 0 logic)
- Frozen v1.0 protocol unchanged (only uses existing fields)
- Test infrastructure available (pytest, temporary directories, mocking)

## ⏱️ Estimated Effort
- Benchmark harness: 1-2 hours
- Focused tests: 30-60 minutes
- Validation and integration: 30-60 minutes

## 🚦 Readiness Checkpoint
Before beginning Step 2, verify:
1. All existing tests still pass (run `pytest`)
2. Validation script runs successfully
3. No syntax or import errors in modified files
4. Clear understanding of benchmark requirements from ROADMAP.md and docs/latency.md

Once Step 2 is complete and validated, the user can proceed to running the 100+ trial benchmark as defined in M5: "100+ trial latency harness with p50/p95/p99".
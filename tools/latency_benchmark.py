#!/usr/bin/env python3
"""
M5 Latency Benchmark: 100+ trial T3-T0 latency harness.
Measures T3-T0 latency where:
- T0 = keyword audio end (set via laptop_client.set_keyword_end_time())
- T3 = first backend frame received (measured in backend Stream.first_frame_ns)
- Metric: T3-T0 = (T3_ns - T0_ns) / 1_000_000 milliseconds

This is a HOST/SIMULATOR measurement only.
"""

import asyncio
import json
import time
import statistics
import numpy as np
from pathlib import Path
import tempfile
import sys
import logging
from typing import List, Tuple, Optional

# Setup logging
logging.basicConfig(level=logging.WARNING)  # Reduce noise during benchmark
LOG = logging.getLogger(__name__)

# Import our modules
sys.path.insert(0, '/Users/roshan/Desktop/SIH')

from backend.server import VoiceServer
from backend.protocol import (
    AudioFrame, CODEC_PCM, FLAG_FIRST, PCM_PAYLOAD_LEN, SAMPLES_PER_FRAME, pack, SAMPLE_RATE
)
from tools.laptop_client import LaptopMicClient

import websockets
from websockets.asyncio.server import serve


class M5LatencyBenchmark:
    """M5 latency benchmark harness for T3-T0 measurement."""

    def __init__(self,
                 warmup_trials: int = 10,
                 measured_trials: int = 100,
                 timeout_seconds: float = 5.0):
        """
        Initialize the benchmark.

        Args:
            warmup_trials: Number of warm-up trials (excluded from statistics)
            measured_trials: Number of measured trials for statistics
            timeout_seconds: Timeout for each trial operation
        """
        self.warmup_trials = warmup_trials
        self.measured_trials = measured_trials
        self.timeout_seconds = timeout_seconds

        # Results storage
        self.latencies: List[float] = []  # Successful T3-T0 latencies in ms
        self.failures: List[str] = []     # Failure descriptions

    async def run_single_trial(self, trial_id: int, server: VoiceServer) -> Optional[float]:
        """
        Run a single T3-T0 latency trial.

        Args:
            trial_id: Identifier for this trial
            server: Persistent VoiceServer instance to use for all trials

        Returns:
            T3-T0 latency in milliseconds if successful, None if failed
        """
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                tmp_path = Path(tmpdir)

                # Use the persistent server (passed in)
                # Note: We don't create a new server per trial anymore

                # Track T3 (first frame receive time)
                t3_timestamp_ns: Optional[int] = None
                t3_stream_id: Optional[int] = None

                original_audio = server._audio
                def tracked_audio(raw, streams):
                    nonlocal t3_timestamp_ns, t3_stream_id
                    original_audio(raw, streams)
                    for s in streams.values():
                        # Capture T3 when first frame is received
                        if (s.first_frame_ns is not None and
                            t3_timestamp_ns is None):
                            t3_timestamp_ns = s.first_frame_ns
                            t3_stream_id = s.stream_id
                server._audio = tracked_audio

                # Start backend server
                async with serve(server.handler, '127.0.0.1', 0) as listening:
                    port = listening.sockets[0].getsockname()[1]
                    uri = f'ws://127.0.0.1:{port}'

                    # Create laptop client
                    client = LaptopMicClient(
                        host="127.0.0.1",
                        port=port,
                        keyword="benchmark",
                        output_dir=tmp_path / "output"
                    )

                    # For timing consistency, we'll capture time references
                    # We'll use time.perf_counter_ns() for both T0 and T3 to have consistent monotonic base
                    # Set T0 just before we start the trial sequence
                    t0_timestamp_ns = time.perf_counter_ns()
                    client.set_keyword_end_time(t0_timestamp_ns / 1_000_000_000.0)  # Convert to seconds for laptop client

                    try:
                        # Connect client to backend with timeout
                        ws = await asyncio.wait_for(
                            websockets.connect(uri, max_size=2048),
                            timeout=self.timeout_seconds
                        )

                        try:
                            # Hello handshake
                            await asyncio.wait_for(
                                ws.send(json.dumps({
                                    "type": "hello",
                                    "proto": 1,
                                    "device_id": f"bench-client-{trial_id}",
                                    "codecs": ["pcm_s16le"],
                                    "sample_rate": SAMPLE_RATE,
                                })),
                                timeout=self.timeout_seconds
                            )

                            response = await asyncio.wait_for(
                                ws.recv(),
                                timeout=self.timeout_seconds
                            )
                            hello_ack = json.loads(response)
                            if hello_ack != {"type": "hello_ack", "proto": 1}:
                                raise RuntimeError(f"Unexpected hello response: {hello_ack}")

                            # Send start message (uses our T0 for t_detect_us)
                            await asyncio.wait_for(
                                ws.send(json.dumps({
                                    "type": "start",
                                    "stream_id": 1,
                                    "codec": "pcm_s16le",
                                    "sample_rate": SAMPLE_RATE,
                                    "channels": 1,
                                    "frame_ms": 20,
                                    "prebuffer_ms": 0,  # No prebuffer for simplicity
                                    "live_sample_offset": 0,
                                    "t_detect_us": int((client.keyword_end_time or 0) * 1_000_000),
                                })),
                                timeout=self.timeout_seconds
                            )

                            # Send one audio frame to trigger processing
                            frame = AudioFrame(
                                codec=CODEC_PCM,
                                flags=FLAG_FIRST,
                                seq=0,
                                stream_id=1,
                                sample_offset=0,
                                payload=b"\x00\x00" * (PCM_PAYLOAD_LEN // 2),  # Silence
                            )
                            await asyncio.wait_for(
                                ws.send(pack(frame)),
                                timeout=self.timeout_seconds
                            )

                            # Wait for T3 measurement or timeout
                            start_wait = time.time()
                            while t3_timestamp_ns is None:
                                if time.time() - start_wait > self.timeout_seconds:
                                    raise TimeoutError("Timeout waiting for T3 (first frame)")
                                await asyncio.sleep(0.001)  # 1ms polling

                            # Calculate T3-T0 latency using consistent time base
                            # Both T0 and T3 are now measured using time.perf_counter_ns()
                            # No clock domain conversion needed
                            latency_ms = (t3_timestamp_ns - t0_timestamp_ns) / 1_000_000.0

                            # Sanity check: latency should be reasonable for host/simulator
                            if latency_ms < -50:  # Allow small negative due to measurement imprecision
                                raise ValueError(f"Unreasonably negative latency: {latency_ms} ms")
                            if latency_ms > 5000:  # More than 5 seconds is unreasonable
                                raise ValueError(f"Unreasonably high latency: {latency_ms} ms")

                            return max(0.0, latency_ms)  # Ensure non-negative for reporting

                        finally:
                            # Clean up websocket connection
                            try:
                                await ws.close()
                            except:
                                pass

                    except Exception as e:
                        raise RuntimeError(f"Trial {trial_id} failed during client-server communication: {e}") from e

        except asyncio.TimeoutError:
            raise TimeoutError(f"Trial {trial_id} timed out after {self.timeout_seconds} seconds")
        except Exception as e:
            raise RuntimeError(f"Trial {trial_id} failed: {e}") from e

    async def run_benchmark(self) -> dict:
        """
        Run the full M5 benchmark with warm-up and measured trials.

        Returns:
            Dictionary containing benchmark results
        """
        LOG.info(f"Starting M5 T3-T0 latency benchmark:")
        LOG.info(f"  Warm-up trials: {self.warmup_trials}")
        LOG.info(f"  Measured trials: {self.measured_trials}")
        LOG.info(f"  Timeout per trial: {self.timeout_seconds}s")
        LOG.info("  Measurement environment: HOST/SIMULATOR")
        LOG.info("  Metric: T3-T0 (T3 = first backend frame, T0 = keyword audio end)")

        # Create a persistent VoiceServer for the entire benchmark run
        # Use a temporary directory for the server's output (will be cleaned up after benchmark)
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            server = VoiceServer(tmp_path)

            # Run warm-up trials (excluded from statistics)
            LOG.info("Running warm-up trials...")
            for i in range(self.warmup_trials):
                try:
                    latency = await self.run_single_trial(i, server)
                    if latency is not None:
                        LOG.debug(f"Warm-up trial {i}: {latency:.3f} ms")
                    else:
                        LOG.warning(f"Warm-up trial {i} failed")
                except Exception as e:
                    LOG.warning(f"Warm-up trial {i} failed: {e}")

            # Run measured trials
            LOG.info("Running measured trials...")
            self.latencies = []
            self.failures = []

            for i in range(self.measured_trials):
                trial_num = self.warmup_trials + i
                try:
                    latency = await self.run_single_trial(trial_num, server)
                    if latency is not None:
                        self.latencies.append(latency)
                        if (i + 1) % 10 == 0 or i < 5:  # Log first few and every 10th
                            LOG.info(f"Trial {trial_num}: {latency:.3f} ms")
                    else:
                        self.failures.append(f"Trial {trial_num}: Returned None latency")
                        LOG.warning(f"Trial {trial_num}: Returned None latency")
                except Exception as e:
                    self.failures.append(f"Trial {trial_num}: {str(e)}")
                    LOG.warning(f"Trial {trial_num} failed: {e}")

        # Calculate statistics
        if not self.latencies:
            raise RuntimeError("No successful trials completed")

        # Calculate percentiles using numpy for accuracy
        latencies_sorted = sorted(self.latencies)
        count = len(latencies_sorted)

        def percentile(p):
            """Calculate percentile using numpy method (linear interpolation)"""
            return np.percentile(latencies_sorted, p)

        stats = {
            'warmup_trials': self.warmup_trials,
            'measured_trials_requested': self.measured_trials,
            'successful_trials': len(self.latencies),
            'failed_trials': len(self.failures),
            'latencies_ms': latencies_sorted.copy(),  # Return a copy
            'min_ms': min(latencies_sorted),
            'max_ms': max(latencies_sorted),
            'mean_ms': statistics.mean(latencies_sorted),
            'median_ms': statistics.median(latencies_sorted),
            'stdev_ms': statistics.stdev(latencies_sorted) if len(latencies_sorted) > 1 else 0.0,
            'p50_ms': percentile(50),
            'p95_ms': percentile(95),
            'p99_ms': percentile(99),
            'failures': self.failures.copy()
        }

        return stats

    def print_results(self, stats: dict):
        """Print formatted benchmark results."""
        print("\n" + "="*70)
        print("M5 LATENCY BENCHMARK RESULTS: T3-T0")
        print("="*70)
        print(f"Measurement environment: HOST/SIMULATOR")
        print(f"Metric: T3-T0")
        print(f"  T0: keyword audio end (set via laptop_client.set_keyword_end_time())")
        print(f"  T3: first backend audio frame received (Stream.first_frame_ns)")
        print()
        print(f"Warm-up trials: {stats['warmup_trials']}")
        print(f"Measured trials requested: {stats['measured_trials_requested']}")
        print(f"Successful trials: {stats['successful_trials']}")
        print(f"Failed trials: {stats['failed_trials']}")
        print()
        if stats['latencies_ms']:
            print(f"Latency statistics (milliseconds):")
            print(f"  Min:   {stats['min_ms']:8.3f} ms")
            print(f"  Max:   {stats['max_ms']:8.3f} ms")
            print(f"  Mean:  {stats['mean_ms']:8.3f} ms")
            print(f"  Median:{stats['median_ms']:8.3f} ms")
            print(f"  StdDev:{stats['stdev_ms']:8.3f} ms")
            print(f"  p50:   {stats['p50_ms']:8.3f} ms")
            print(f"  p95:   {stats['p95_ms']:8.3f} ms")
            print(f"  p99:   {stats['p99_ms']:8.3f} ms")
        print()
        if stats['failures']:
            print(f"Failures ({len(stats['failures'])}):")
            for failure in stats['failures'][:5]:  # Show first 5 failures
                print(f"  - {failure}")
            if len(stats['failures']) > 5:
                print(f"  ... and {len(stats['failures']) - 5} more")
        print()
        print("="*70)


async def main():
    """Main benchmark execution."""
    import argparse

    parser = argparse.ArgumentParser(description="M5 T3-T0 Latency Benchmark")
    parser.add_argument("--warmup", type=int, default=10, help="Number of warm-up trials")
    parser.add_argument("--trials", type=int, default=100, help="Number of measured trials")
    parser.add_argument("--timeout", type=float, default=5.0, help="Timeout per trial in seconds")
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    if args.verbose:
        logging.getLogger().setLevel(logging.INFO)

    # Create and run benchmark
    benchmark = M5LatencyBenchmark(
        warmup_trials=args.warmup,
        measured_trials=args.trials,
        timeout_seconds=args.timeout
    )

    try:
        stats = await benchmark.run_benchmark()
        benchmark.print_results(stats)

        # Return success if we had at least some successful trials
        return 0 if stats['successful_trials'] > 0 else 1

    except Exception as e:
        print(f"Benchmark failed: {e}")
        return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    sys.exit(exit_code)
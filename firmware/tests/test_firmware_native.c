/**
 * @file test_firmware_native.c
 * @brief Host-side unit tests for ESP32-S3 firmware core C components.
 */

#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <assert.h>
#include <math.h>

#include "../main/protocol_frames.h"
#include "../main/audio_ring_buffer.h"
#include "../main/energy_detector.h"
#include "../main/kws_engine.h"

static int s_tests_run = 0;

static void test_protocol_frame_packing(void) {
    printf("Running test_protocol_frame_packing...\n");

    uint8_t payload[AUDIO_PCM_PAYLOAD_LEN];
    for (size_t i = 0; i < AUDIO_PCM_PAYLOAD_LEN; i++) {
        payload[i] = (uint8_t)(i & 0xFF);
    }

    uint8_t wire_frame[TOTAL_WIRE_FRAME_LEN];
    uint8_t flags = FLAG_FIRST | FLAG_PREBUF;
    uint16_t seq = 42;
    uint32_t stream_id = 12345678;
    uint32_t sample_offset = 42 * AUDIO_SAMPLES_PER_FRAME;

    size_t packed_len = protocol_pack_frame(
        wire_frame,
        CODEC_PCM_S16LE,
        flags,
        seq,
        stream_id,
        sample_offset,
        payload
    );

    assert(packed_len == TOTAL_WIRE_FRAME_LEN);
    assert(wire_frame[0] == 0xA5); // magic
    assert(wire_frame[1] == 1);    // version
    assert(wire_frame[2] == 0);    // codec PCM
    assert(wire_frame[3] == flags);

    // Unpack and verify
    protocol_header_t hdr;
    bool ok = protocol_unpack_header(wire_frame, packed_len, &hdr);
    assert(ok == true);
    assert(hdr.magic == 0xA5);
    assert(hdr.version == 1);
    assert(hdr.codec == CODEC_PCM_S16LE);
    assert(hdr.flags == flags);
    assert(hdr.seq == 42);
    assert(hdr.payload_len == AUDIO_PCM_PAYLOAD_LEN);
    assert(hdr.stream_id == 12345678);
    assert(hdr.sample_offset == 42 * 320);

    // Payload verification
    assert(memcmp(wire_frame + PROTOCOL_HEADER_LEN, payload, AUDIO_PCM_PAYLOAD_LEN) == 0);

    s_tests_run++;
    printf("  PASSED: test_protocol_frame_packing\n");
}

static void test_audio_ring_buffer_prebuffer(void) {
    printf("Running test_audio_ring_buffer_prebuffer...\n");

    audio_ring_buffer_t rb;
    audio_ring_buffer_init(&rb);
    assert(audio_ring_buffer_get_count(&rb) == 0);

    // Push 50 frames with sequential IDs in their first 2 bytes
    for (uint16_t f = 0; f < 50; f++) {
        uint8_t frame[AUDIO_PCM_PAYLOAD_LEN] = {0};
        frame[0] = (uint8_t)(f & 0xFF);
        frame[1] = (uint8_t)((f >> 8) & 0xFF);
        audio_ring_buffer_push(&rb, frame);
    }

    assert(audio_ring_buffer_get_count(&rb) == 50);

    // Extract 40 prebuffer frames (most recent 40: frames 10 to 49)
    uint8_t extracted[40 * AUDIO_PCM_PAYLOAD_LEN];
    size_t count = audio_ring_buffer_extract_recent(&rb, 40, extracted);
    assert(count == 40);

    for (size_t i = 0; i < 40; i++) {
        const uint8_t *frame_ptr = extracted + (i * AUDIO_PCM_PAYLOAD_LEN);
        uint16_t expected_id = 10 + i;
        uint16_t actual_id = (uint16_t)frame_ptr[0] | ((uint16_t)frame_ptr[1] << 8);
        assert(actual_id == expected_id);
    }

    s_tests_run++;
    printf("  PASSED: test_audio_ring_buffer_prebuffer\n");
}

static void test_audio_ring_buffer_wraparound(void) {
    printf("Running test_audio_ring_buffer_wraparound...\n");

    audio_ring_buffer_t rb;
    audio_ring_buffer_init(&rb);

    // Push 100 frames (more than capacity 64)
    for (uint16_t f = 0; f < 100; f++) {
        uint8_t frame[AUDIO_PCM_PAYLOAD_LEN] = {0};
        frame[0] = (uint8_t)(f & 0xFF);
        frame[1] = (uint8_t)((f >> 8) & 0xFF);
        audio_ring_buffer_push(&rb, frame);
    }

    assert(audio_ring_buffer_get_count(&rb) == RING_BUFFER_CAPACITY_FRAMES);

    // Extract 40 frames: should be frames 60 to 99
    uint8_t extracted[40 * AUDIO_PCM_PAYLOAD_LEN];
    size_t count = audio_ring_buffer_extract_recent(&rb, 40, extracted);
    assert(count == 40);

    for (size_t i = 0; i < 40; i++) {
        const uint8_t *frame_ptr = extracted + (i * AUDIO_PCM_PAYLOAD_LEN);
        uint16_t expected_id = 60 + i;
        uint16_t actual_id = (uint16_t)frame_ptr[0] | ((uint16_t)frame_ptr[1] << 8);
        assert(actual_id == expected_id);
    }

    s_tests_run++;
    printf("  PASSED: test_audio_ring_buffer_wraparound\n");
}

static void test_energy_detector_gating(void) {
    printf("Running test_energy_detector_gating...\n");

    int16_t silence[AUDIO_SAMPLES_PER_FRAME] = {0};
    float db_silence = energy_compute_dbfs(silence, AUDIO_SAMPLES_PER_FRAME);
    assert(db_silence <= -90.0f);
    assert(energy_is_active(silence, AUDIO_SAMPLES_PER_FRAME, -50.0f) == false);

    // Full-scale sine wave
    int16_t loud[AUDIO_SAMPLES_PER_FRAME];
    for (size_t i = 0; i < AUDIO_SAMPLES_PER_FRAME; i++) {
        loud[i] = (int16_t)(32000.0 * sin(2.0 * M_PI * i / 16.0));
    }
    float db_loud = energy_compute_dbfs(loud, AUDIO_SAMPLES_PER_FRAME);
    assert(db_loud > -10.0f);
    assert(energy_is_active(loud, AUDIO_SAMPLES_PER_FRAME, -50.0f) == true);

    s_tests_run++;
    printf("  PASSED: test_energy_detector_gating\n");
}

static void test_kws_engine_features_and_smoothing(void) {
    printf("Running test_kws_engine_features_and_smoothing...\n");

    kws_engine_t engine;
    kws_engine_init(&engine, 0.70f);

    int16_t test_audio[AUDIO_SAMPLES_PER_FRAME];
    for (size_t i = 0; i < AUDIO_SAMPLES_PER_FRAME; i++) {
        test_audio[i] = (int16_t)(10000.0 * sin(2.0 * M_PI * i / 8.0));
    }

    int8_t features[KWS_NUM_FEATURES];
    kws_extract_features(test_audio, AUDIO_SAMPLES_PER_FRAME, features);
    for (size_t b = 0; b < KWS_NUM_FEATURES; b++) {
        assert(features[b] >= -128 && features[b] <= 127);
    }

    // Process multiple frames to test temporal smoothing
    float conf = 0.0f;
    for (int f = 0; f < 10; f++) {
        kws_engine_process_frame(&engine, test_audio, AUDIO_SAMPLES_PER_FRAME, 1000 * f, &conf);
    }
    assert(conf >= 0.0f && conf <= 1.0f);

    kws_engine_reset_trigger(&engine);
    assert(engine.is_triggered == false);

    s_tests_run++;
    printf("  PASSED: test_kws_engine_features_and_smoothing\n");
}

int main(void) {
    printf("=========================================\n");
    printf("ESP32-S3 Firmware Host Unit Test Suite\n");
    printf("=========================================\n");

    test_protocol_frame_packing();
    test_audio_ring_buffer_prebuffer();
    test_audio_ring_buffer_wraparound();
    test_energy_detector_gating();
    test_kws_engine_features_and_smoothing();

    printf("\nAll %d firmware host tests PASSED successfully!\n", s_tests_run);
    return 0;
}

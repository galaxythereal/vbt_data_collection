#pragma once

/**
 * @file IMUReader.h
 * @brief Reads binary IMU packets from ESP32 over USB serial.
 *
 * Handles packet parsing, CRC validation, timestamp mapping,
 * and provides thread-safe access to latest IMU data.
 */

#include <string>
#include <cstdint>
#include <vector>
#include <mutex>
#include <atomic>
#include <thread>
#include <functional>
#include <chrono>
#include "app/Config.h"

namespace vbt {

// ============================================================================
// IMU Data Sample
// ============================================================================
struct IMUSample {
    uint64_t esp_timestamp_us = 0;   // ESP32 microsecond timestamp
    double   host_timestamp_s = 0.0; // Host clock timestamp (seconds since epoch)
    double   unified_time_s   = 0.0; // Unified timeline (seconds)

    float accel_x_g   = 0.0f;       // Acceleration in g
    float accel_y_g   = 0.0f;
    float accel_z_g   = 0.0f;

    float gyro_x_dps  = 0.0f;       // Angular rate in dps
    float gyro_y_dps  = 0.0f;
    float gyro_z_dps  = 0.0f;

    float temperature_c = 0.0f;

    int16_t accel_x_raw = 0;         // Raw values for logging
    int16_t accel_y_raw = 0;
    int16_t accel_z_raw = 0;
    int16_t gyro_x_raw  = 0;
    int16_t gyro_y_raw  = 0;
    int16_t gyro_z_raw  = 0;
    int16_t temp_raw    = 0;

    bool valid = false;
};

// ============================================================================
// IMU Statistics
// ============================================================================
// Copyable snapshot of IMU statistics (no atomics)
struct IMUStats {
    uint64_t total_packets   = 0;
    uint64_t valid_packets   = 0;
    uint64_t crc_errors      = 0;
    uint64_t sync_errors     = 0;
    uint64_t dropouts        = 0;
    double measured_rate_hz  = 0.0;
    double jitter_us_mean    = 0.0;
    double jitter_us_max     = 0.0;
    double jitter_us_stddev  = 0.0;
};

// Internal atomic counters (non-copyable) — private to IMUReader
struct IMUAtomicCounters {
    std::atomic<uint64_t> total_packets{0};
    std::atomic<uint64_t> valid_packets{0};
    std::atomic<uint64_t> crc_errors{0};
    std::atomic<uint64_t> sync_errors{0};
    std::atomic<uint64_t> dropouts{0};
};

// ============================================================================
// IMU Reader Class
// ============================================================================
class IMUReader {
public:
    using Callback = std::function<void(const IMUSample&)>;

    IMUReader();
    ~IMUReader();

    // Lifecycle
    bool open(const IMUConfig& config);
    void close();
    bool is_open() const { return is_open_; }

    // Start/stop streaming thread
    void start();
    void stop();
    bool is_running() const { return is_running_; }

    // Access latest sample (thread-safe)
    IMUSample get_latest_sample() const;

    // Get statistics
    IMUStats get_stats() const;

    // Register callback for new samples
    void set_callback(Callback cb) { callback_ = std::move(cb); }

    // Get first ESP timestamp for clock sync
    uint64_t get_first_esp_timestamp() const { return first_esp_timestamp_; }
    double   get_first_host_timestamp() const { return first_host_timestamp_; }

    // Gyro bias calibration (call during static period)
    void start_gyro_bias_calibration(int duration_ms = 5000);
    bool is_calibrating() const { return is_calibrating_; }
    struct GyroBias { float x = 0, y = 0, z = 0; };
    GyroBias get_gyro_bias() const { return gyro_bias_; }

private:
    void read_thread_func();
    bool parse_packet(const uint8_t* data, size_t len, IMUSample& sample);
    uint16_t crc16_ccitt(const uint8_t* data, size_t length);
    void update_jitter_stats(uint64_t timestamp_us);

    // Serial port handle
    int serial_fd_ = -1;
    IMUConfig config_;

    // Threading
    std::thread read_thread_;
    std::atomic<bool> is_running_{false};
    std::atomic<bool> is_open_{false};

    // Latest sample
    mutable std::mutex sample_mutex_;
    IMUSample latest_sample_;

    // Statistics (atomic counters + rolling jitter)
    IMUAtomicCounters counters_;
    IMUStats          stats_snapshot_;   // updated under sample_mutex_
    uint64_t prev_timestamp_us_ = 0;
    std::vector<double> jitter_history_;

    // Clock sync
    uint64_t first_esp_timestamp_ = 0;
    double   first_host_timestamp_ = 0.0;
    bool     first_sample_received_ = false;

    // Gyro calibration
    std::atomic<bool> is_calibrating_{false};
    GyroBias gyro_bias_;
    std::vector<float> calib_gx_, calib_gy_, calib_gz_;
    int calib_samples_target_ = 0;

    // Callback
    Callback callback_;

    // Packet buffer
    static constexpr size_t PACKET_SIZE = 26;
    static constexpr uint16_t SYNC_WORD = 0xAA55;
    uint8_t ring_buffer_[4096];
    size_t ring_write_ = 0;
    size_t ring_read_  = 0;
};

} // namespace vbt

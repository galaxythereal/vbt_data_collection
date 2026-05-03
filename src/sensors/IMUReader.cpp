/**
 * @file IMUReader.cpp
 * @brief Implementation of ESP32 serial IMU reader.
 */

#include "sensors/IMUReader.h"
#include <spdlog/spdlog.h>
#include <cstring>
#include <numeric>
#include <cmath>
#include <algorithm>
#include <chrono>

// Linux serial
#include <fcntl.h>
#include <termios.h>
#include <unistd.h>
#include <errno.h>

namespace vbt {

IMUReader::IMUReader() = default;

IMUReader::~IMUReader() {
    stop();
    close();
}

bool IMUReader::open(const IMUConfig& config) {
    config_ = config;

    // Open serial port
    serial_fd_ = ::open(config_.port.c_str(), O_RDWR | O_NOCTTY | O_NONBLOCK);
    if (serial_fd_ < 0) {
        spdlog::error("Failed to open serial port {}: {}", config_.port, strerror(errno));
        return false;
    }

    // Configure serial port
    struct termios tty;
    memset(&tty, 0, sizeof(tty));

    if (tcgetattr(serial_fd_, &tty) != 0) {
        spdlog::error("tcgetattr failed: {}", strerror(errno));
        ::close(serial_fd_);
        serial_fd_ = -1;
        return false;
    }

    // Set baud rate
    speed_t baud;
    switch (config_.baud_rate) {
#ifdef B2000000
        case 2000000: baud = B2000000; break;
#endif
#ifdef B921600
        case 921600:  baud = B921600;  break;
#endif
        case 115200:  baud = B115200;  break;
        default:
#ifdef __APPLE__
            // macOS lacks B921600/B2000000 constants; fall back to 115200.
            baud = B115200;
            break;
#else
            spdlog::error("Unsupported baud rate: {}", config_.baud_rate);
            ::close(serial_fd_);
            serial_fd_ = -1;
            return false;
#endif
    }
    cfsetispeed(&tty, baud);
    cfsetospeed(&tty, baud);

    // 8N1 mode
    tty.c_cflag &= ~PARENB;
    tty.c_cflag &= ~CSTOPB;
    tty.c_cflag &= ~CSIZE;
    tty.c_cflag |= CS8;

    // No flow control
    tty.c_cflag &= ~CRTSCTS;
    tty.c_cflag |= CREAD | CLOCAL;

    // Raw mode
    tty.c_lflag &= ~(ICANON | ECHO | ECHOE | ISIG);
    tty.c_iflag &= ~(IXON | IXOFF | IXANY);
    tty.c_iflag &= ~(IGNBRK | BRKINT | PARMRK | ISTRIP | INLCR | IGNCR | ICRNL);
    tty.c_oflag &= ~OPOST;

    // Read with timeout
    tty.c_cc[VMIN] = 0;
    tty.c_cc[VTIME] = 1;  // 100ms timeout

    if (tcsetattr(serial_fd_, TCSANOW, &tty) != 0) {
        spdlog::error("tcsetattr failed: {}", strerror(errno));
        ::close(serial_fd_);
        serial_fd_ = -1;
        return false;
    }

    // Flush buffers
    tcflush(serial_fd_, TCIOFLUSH);

    is_open_ = true;
    spdlog::info("Serial port {} opened at {} baud", config_.port, config_.baud_rate);
    return true;
}

void IMUReader::close() {
    stop();
    if (serial_fd_ >= 0) {
        ::close(serial_fd_);
        serial_fd_ = -1;
    }
    is_open_ = false;
}

void IMUReader::start() {
    if (is_running_ || !is_open_) return;
    is_running_ = true;
    read_thread_ = std::thread(&IMUReader::read_thread_func, this);
    spdlog::info("IMU reader thread started");
}

void IMUReader::stop() {
    is_running_ = false;
    if (read_thread_.joinable()) {
        read_thread_.join();
    }
    spdlog::info("IMU reader thread stopped");
}

IMUSample IMUReader::get_latest_sample() const {
    std::lock_guard<std::mutex> lock(sample_mutex_);
    return latest_sample_;
}

IMUStats IMUReader::get_stats() const {
    std::lock_guard<std::mutex> lock(sample_mutex_);
    IMUStats s = stats_snapshot_;
    s.total_packets  = counters_.total_packets.load();
    s.valid_packets  = counters_.valid_packets.load();
    s.crc_errors     = counters_.crc_errors.load();
    s.sync_errors    = counters_.sync_errors.load();
    s.dropouts       = counters_.dropouts.load();
    // Live rate: average packets/sec since first sample. Stays at 0 until
    // we have at least 0.5 s of data so the readout doesn't flicker on connect.
    s.fsync_hit_count = counters_.fsync_hits.load();
    if (first_sample_received_ && s.valid_packets > 0) {
        auto now = std::chrono::steady_clock::now();
        double now_s = std::chrono::duration<double>(now.time_since_epoch()).count();
        double elapsed = now_s - first_host_timestamp_;
        if (elapsed > 0.5) {
            s.measured_rate_hz = s.valid_packets / elapsed;
            s.fsync_rate_hz   = s.fsync_hit_count / elapsed;
        }
    }
    s.accel_saturation_count = accel_saturation_count_.load();
    s.gyro_saturation_count  = gyro_saturation_count_.load();
    // Rolling stddev as noise floor proxy
    auto stddev = [](const std::vector<float>& v) -> double {
        if (v.size() < 8) return 0.0;
        double mean = 0; for (float x : v) mean += x; mean /= v.size();
        double sq = 0; for (float x : v) { double d = x - mean; sq += d * d; }
        return std::sqrt(sq / v.size());
    };
    s.gyro_noise_floor_dps = stddev(gyro_history_for_noise_);
    s.accel_noise_floor_g  = stddev(accel_history_for_noise_);
    return s;
}

uint16_t IMUReader::crc16_ccitt(const uint8_t* data, size_t length) {
    uint16_t crc = 0xFFFF;
    for (size_t i = 0; i < length; i++) {
        crc ^= (uint16_t)data[i] << 8;
        for (int j = 0; j < 8; j++) {
            if (crc & 0x8000)
                crc = (crc << 1) ^ 0x1021;
            else
                crc <<= 1;
        }
    }
    return crc;
}

bool IMUReader::parse_packet(const uint8_t* data, size_t len, IMUSample& sample) {
    if (len < PACKET_SIZE) return false;

    // Verify sync word
    uint16_t sync = data[0] | (data[1] << 8);
    if (sync != SYNC_WORD) {
        counters_.sync_errors++;
        return false;
    }

    // Verify CRC
    uint16_t received_crc = data[24] | (data[25] << 8);
    uint16_t computed_crc = crc16_ccitt(data, 24);
    if (received_crc != computed_crc) {
        counters_.crc_errors++;
        return false;
    }

    // Parse timestamp
    uint64_t ts = 0;
    memcpy(&ts, &data[2], 8);
    sample.esp_timestamp_us = ts;

    // Parse raw sensor data (little-endian from ESP32)
    memcpy(&sample.accel_x_raw, &data[10], 2);
    memcpy(&sample.accel_y_raw, &data[12], 2);
    memcpy(&sample.accel_z_raw, &data[14], 2);
    memcpy(&sample.gyro_x_raw, &data[16], 2);
    memcpy(&sample.gyro_y_raw, &data[18], 2);
    memcpy(&sample.gyro_z_raw, &data[20], 2);
    memcpy(&sample.temp_raw, &data[22], 2);

    // Bar firmware tags TEMP LSB on samples that coincide with the camera's
    // FSYNC rising edge (configured via FSYNC_UI_SEL=001 + TMST_FSYNC_EN).
    // Mask it out of temp_raw for clean temperature, count it as a sync hit.
    bool fsync_tagged = (sample.temp_raw & 0x0001);
    sample.temp_raw &= ~0x0001;
    if (fsync_tagged) counters_.fsync_hits++;

    // Convert to physical units
    float a_scale = config_.accel_scale();
    float g_scale = config_.gyro_scale();
    sample.accel_x_g  = sample.accel_x_raw * a_scale;
    sample.accel_y_g  = sample.accel_y_raw * a_scale;
    sample.accel_z_g  = sample.accel_z_raw * a_scale;
    sample.gyro_x_dps = sample.gyro_x_raw * g_scale;
    sample.gyro_y_dps = sample.gyro_y_raw * g_scale;
    sample.gyro_z_dps = sample.gyro_z_raw * g_scale;
    sample.temperature_c = sample.temp_raw * config_.temp_scale() + config_.temp_offset();

    // Host timestamp
    auto now = std::chrono::steady_clock::now();
    sample.host_timestamp_s = std::chrono::duration<double>(now.time_since_epoch()).count();

    // Record first timestamp for clock sync
    if (!first_sample_received_) {
        first_esp_timestamp_ = ts;
        first_host_timestamp_ = sample.host_timestamp_s;
        first_sample_received_ = true;
        spdlog::info("IMU first sample: ESP_ts={} µs, host_ts={:.6f} s",
                     ts, sample.host_timestamp_s);
    }

    sample.valid = true;
    return true;
}

void IMUReader::update_jitter_stats(uint64_t timestamp_us) {
    if (prev_timestamp_us_ > 0) {
        double dt_us = static_cast<double>(timestamp_us - prev_timestamp_us_);
        double jitter = std::abs(dt_us - 1000.0);  // Expected 1000µs interval
        jitter_history_.push_back(jitter);

        // Keep last 1000 samples for rolling stats
        if (jitter_history_.size() > 1000) {
            jitter_history_.erase(jitter_history_.begin());
        }

        // Check for dropouts (>2ms gap)
        if (dt_us > 2000.0) {
            counters_.dropouts++;
        }

        // Update jitter snapshot
        if (!jitter_history_.empty()) {
            double sum = std::accumulate(jitter_history_.begin(), jitter_history_.end(), 0.0);
            stats_snapshot_.jitter_us_mean = sum / jitter_history_.size();
            stats_snapshot_.jitter_us_max = *std::max_element(jitter_history_.begin(), jitter_history_.end());

            double sq_sum = 0;
            for (auto j : jitter_history_) {
                double diff = j - stats_snapshot_.jitter_us_mean;
                sq_sum += diff * diff;
            }
            stats_snapshot_.jitter_us_stddev = std::sqrt(sq_sum / jitter_history_.size());
        }
    }
    prev_timestamp_us_ = timestamp_us;
}

void IMUReader::read_thread_func() {
    spdlog::info("IMU read thread running on thread");

    uint8_t read_buf[512];
    uint8_t packet_buf[PACKET_SIZE * 2];  // Double buffer for sync search
    size_t packet_buf_len = 0;

    while (is_running_) {
        // Read from serial
        ssize_t n = ::read(serial_fd_, read_buf, sizeof(read_buf));
        if (n <= 0) {
            if (n < 0 && errno != EAGAIN) {
                spdlog::error("Serial read error: {}", strerror(errno));
            }
            continue;
        }

        // Append to packet buffer
        for (ssize_t i = 0; i < n; i++) {
            // Skip comment lines (ASCII '#' from ESP32 status)
            if (read_buf[i] == '#' && packet_buf_len == 0) {
                // Skip until newline
                while (i < n && read_buf[i] != '\n') i++;
                continue;
            }

            packet_buf[packet_buf_len++] = read_buf[i];

            // Try to parse when we have enough bytes
            if (packet_buf_len >= PACKET_SIZE) {
                // Search for sync word
                bool found = false;
                for (size_t j = 0; j <= packet_buf_len - PACKET_SIZE; j++) {
                    uint16_t sync = packet_buf[j] | (packet_buf[j + 1] << 8);
                    if (sync == SYNC_WORD) {
                        IMUSample sample;
                        counters_.total_packets++;

                        if (parse_packet(&packet_buf[j], PACKET_SIZE, sample)) {
                            counters_.valid_packets++;
                            update_jitter_stats(sample.esp_timestamp_us);

                            // Gyro bias calibration
                            if (is_calibrating_) {
                                calib_gx_.push_back(sample.gyro_x_dps);
                                calib_gy_.push_back(sample.gyro_y_dps);
                                calib_gz_.push_back(sample.gyro_z_dps);
                                if (static_cast<int>(calib_gx_.size()) >= calib_samples_target_) {
                                    // Compute mean
                                    gyro_bias_.x = std::accumulate(calib_gx_.begin(), calib_gx_.end(), 0.0f) / calib_gx_.size();
                                    gyro_bias_.y = std::accumulate(calib_gy_.begin(), calib_gy_.end(), 0.0f) / calib_gy_.size();
                                    gyro_bias_.z = std::accumulate(calib_gz_.begin(), calib_gz_.end(), 0.0f) / calib_gz_.size();
                                    is_calibrating_ = false;
                                    spdlog::info("Gyro bias calibration complete: ({:.4f}, {:.4f}, {:.4f}) dps",
                                                 gyro_bias_.x, gyro_bias_.y, gyro_bias_.z);
                                }
                            }

                            // Apply gyro bias correction
                            sample.gyro_x_dps -= gyro_bias_.x;
                            sample.gyro_y_dps -= gyro_bias_.y;
                            sample.gyro_z_dps -= gyro_bias_.z;

                            // Signal-quality bookkeeping
                            const int16_t kSat = 32700;  // ~99.8% of int16 range
                            if (std::abs(sample.accel_x_raw) > kSat ||
                                std::abs(sample.accel_y_raw) > kSat ||
                                std::abs(sample.accel_z_raw) > kSat) {
                                accel_saturation_count_++;
                            }
                            if (std::abs(sample.gyro_x_raw) > kSat ||
                                std::abs(sample.gyro_y_raw) > kSat ||
                                std::abs(sample.gyro_z_raw) > kSat) {
                                gyro_saturation_count_++;
                            }
                            // Store latest + update rolling-noise buffers under the same lock
                            {
                                std::lock_guard<std::mutex> lock(sample_mutex_);
                                latest_sample_ = sample;
                                float gmag = std::sqrt(sample.gyro_x_dps*sample.gyro_x_dps +
                                                       sample.gyro_y_dps*sample.gyro_y_dps +
                                                       sample.gyro_z_dps*sample.gyro_z_dps);
                                float amag = std::sqrt(sample.accel_x_g*sample.accel_x_g +
                                                       sample.accel_y_g*sample.accel_y_g +
                                                       sample.accel_z_g*sample.accel_z_g);
                                gyro_history_for_noise_.push_back(gmag);
                                accel_history_for_noise_.push_back(amag - 1.0f);
                                if (gyro_history_for_noise_.size() > kNoiseWindow)
                                    gyro_history_for_noise_.erase(gyro_history_for_noise_.begin());
                                if (accel_history_for_noise_.size() > kNoiseWindow)
                                    accel_history_for_noise_.erase(accel_history_for_noise_.begin());
                            }

                            // Callback
                            if (callback_) {
                                callback_(sample);
                            }
                        }

                        // Remove processed bytes
                        size_t remaining = packet_buf_len - (j + PACKET_SIZE);
                        if (remaining > 0) {
                            memmove(packet_buf, &packet_buf[j + PACKET_SIZE], remaining);
                        }
                        packet_buf_len = remaining;
                        found = true;
                        break;
                    }
                }

                // If no sync found and buffer is full, discard oldest byte
                if (!found && packet_buf_len >= PACKET_SIZE * 2) {
                    memmove(packet_buf, &packet_buf[1], packet_buf_len - 1);
                    packet_buf_len--;
                }
            }
        }
    }

    // Compute final rate
    if (first_sample_received_ && counters_.valid_packets > 0) {
        auto now = std::chrono::steady_clock::now();
        double elapsed = std::chrono::duration<double>(now.time_since_epoch()).count() - first_host_timestamp_;
        if (elapsed > 0) {
            stats_snapshot_.measured_rate_hz = counters_.valid_packets.load() / elapsed;
        }
    }
}

void IMUReader::start_gyro_bias_calibration(int duration_ms) {
    calib_gx_.clear();
    calib_gy_.clear();
    calib_gz_.clear();
    calib_samples_target_ = (config_.odr_hz * duration_ms) / 1000;
    is_calibrating_ = true;
    spdlog::info("Starting gyro bias calibration ({} ms, {} samples)",
                 duration_ms, calib_samples_target_);
}

} // namespace vbt

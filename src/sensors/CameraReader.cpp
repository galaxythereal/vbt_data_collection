/**
 * @file CameraReader.cpp
 * @brief Intel RealSense D455 pipeline implementation.
 */

#include "sensors/CameraReader.h"
#include <spdlog/spdlog.h>
#include <opencv2/imgproc.hpp>
#include <chrono>

namespace vbt {

CameraReader::CameraReader() = default;
CameraReader::~CameraReader() { stop(); close(); }

bool CameraReader::open(const CameraConfig& config) {
    config_ = config;
    try {
        rs2::context ctx;
        auto devices = ctx.query_devices();
        if (devices.size() == 0) { spdlog::error("No RealSense device found"); return false; }
        serial_ = devices[0].get_info(RS2_CAMERA_INFO_SERIAL_NUMBER);
        spdlog::info("D455 found: SN={}", serial_);

        rs_config_.enable_stream(RS2_STREAM_INFRARED, 1, config_.width, config_.height, RS2_FORMAT_Y8, config_.fps);
        rs_config_.enable_stream(RS2_STREAM_INFRARED, 2, config_.width, config_.height, RS2_FORMAT_Y8, config_.fps);
        if (config_.enable_depth)
            rs_config_.enable_stream(RS2_STREAM_DEPTH, config_.width, config_.height, RS2_FORMAT_Z16, config_.fps);
        if (config_.enable_rgb)
            rs_config_.enable_stream(RS2_STREAM_COLOR, 848, 480, RS2_FORMAT_BGR8, config_.rgb_fps);

        profile_ = pipeline_.start(rs_config_);
        auto device = profile_.get_device();
        auto sensor = device.first<rs2::depth_sensor>();

        if (sensor.supports(RS2_OPTION_EMITTER_ENABLED))
            sensor.set_option(RS2_OPTION_EMITTER_ENABLED, config_.emitter_on ? 1.0f : 0.0f);
        if (sensor.supports(RS2_OPTION_ENABLE_AUTO_EXPOSURE))
            sensor.set_option(RS2_OPTION_ENABLE_AUTO_EXPOSURE, 0.0f);
        if (sensor.supports(RS2_OPTION_EXPOSURE))
            sensor.set_option(RS2_OPTION_EXPOSURE, (float)config_.exposure_us);
        if (sensor.supports(RS2_OPTION_GAIN))
            sensor.set_option(RS2_OPTION_GAIN, (float)config_.gain);

        auto ir_s = profile_.get_stream(RS2_STREAM_INFRARED, 1).as<rs2::video_stream_profile>();
        ir_intrinsics_ = ir_s.get_intrinsics();
        if (config_.enable_depth) {
            auto d_s = profile_.get_stream(RS2_STREAM_DEPTH).as<rs2::video_stream_profile>();
            depth_intrinsics_ = d_s.get_intrinsics();
        }
        is_open_ = true;
        spdlog::info("D455 opened: {}x{}@{}fps, emitter={}", config_.width, config_.height, config_.fps, config_.emitter_on);
        return true;
    } catch (const rs2::error& e) {
        spdlog::error("RealSense error: {}", e.what()); return false;
    }
}

void CameraReader::close() {
    stop();
    if (is_open_) { try { pipeline_.stop(); } catch (...) {} is_open_ = false; }
}

void CameraReader::start() {
    if (is_running_ || !is_open_) return;
    is_running_ = true;
    stream_thread_ = std::thread(&CameraReader::stream_thread_func, this);
}

void CameraReader::stop() {
    is_running_ = false;
    if (stream_thread_.joinable()) stream_thread_.join();
}

CameraFrame CameraReader::get_latest_frame() const {
    std::lock_guard<std::mutex> lock(frame_mutex_);
    return latest_frame_;
}

CameraStats CameraReader::get_stats() const {
    CameraStats s;
    s.total_frames   = counters_.total_frames.load();
    s.dropped_frames = counters_.dropped_frames.load();
    return s;
}

void CameraReader::stream_thread_func() {
    while (is_running_) {
        try {
            rs2::frameset fs;
            if (!pipeline_.poll_for_frames(&fs)) {
                std::this_thread::sleep_for(std::chrono::microseconds(500));
                continue;
            }
            CameraFrame frame;
            auto now = std::chrono::steady_clock::now();
            frame.host_timestamp_s = std::chrono::duration<double>(now.time_since_epoch()).count();

            auto ir_l = fs.get_infrared_frame(1);
            if (ir_l) {
                frame.hw_timestamp_s = ir_l.get_timestamp() / 1000.0;
                frame.frame_number = ir_l.get_frame_number();
                frame.ir_left = cv::Mat(config_.height, config_.width, CV_8UC1,
                    const_cast<void*>(ir_l.get_data())).clone();
                frame.ir_intrinsics = ir_intrinsics_;
            }
            auto ir_r = fs.get_infrared_frame(2);
            if (ir_r) frame.ir_right = cv::Mat(config_.height, config_.width, CV_8UC1,
                const_cast<void*>(ir_r.get_data())).clone();

            if (config_.enable_depth) {
                auto df = fs.get_depth_frame();
                if (df) { frame.depth = cv::Mat(config_.height, config_.width, CV_16UC1,
                    const_cast<void*>(df.get_data())).clone(); frame.depth_intrinsics = depth_intrinsics_; }
            }
            if (config_.enable_rgb) {
                auto cf = fs.get_color_frame();
                if (cf) { frame.rgb = cv::Mat(480, 848, CV_8UC3,
                    const_cast<void*>(cf.get_data())).clone(); frame.has_rgb = true; }
            }
            if (!first_frame_received_) {
                first_hw_timestamp_ = frame.hw_timestamp_s;
                first_host_timestamp_ = frame.host_timestamp_s;
                first_frame_received_ = true;
            }
            if (prev_frame_number_ > 0 && frame.frame_number > prev_frame_number_ + 1)
                counters_.dropped_frames += frame.frame_number - prev_frame_number_ - 1;
            prev_frame_number_ = frame.frame_number;

            frame.valid = true;
            counters_.total_frames++;
            { std::lock_guard<std::mutex> lock(frame_mutex_); latest_frame_ = frame; }
            if (callback_) callback_(frame);
        } catch (const rs2::error& e) { spdlog::error("Stream error: {}", e.what()); }
    }
}

void CameraReader::start_rgb_recording(const std::string& p) { recording_rgb_ = true; }
void CameraReader::stop_rgb_recording() { recording_rgb_ = false; }

} // namespace vbt

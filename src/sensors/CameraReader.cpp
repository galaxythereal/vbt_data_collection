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
        std::string usb_type = devices[0].get_info(RS2_CAMERA_INFO_USB_TYPE_DESCRIPTOR);
        serial_ = devices[0].get_info(RS2_CAMERA_INFO_SERIAL_NUMBER);
        spdlog::info("D455 found: SN={}, USB={}", serial_, usb_type);

        // Set hardware sync mode (must happen BEFORE pipeline.start). With mode=1
        // (master) the D455 drives a 90 Hz pulse out of aux pin 5 which the ESP
        // wires to the IMU's FSYNC pad — gives full-rate hard sync on every frame.
        for (auto& s : devices[0].query_sensors()) {
            if (s.supports(RS2_OPTION_INTER_CAM_SYNC_MODE)) {
                s.set_option(RS2_OPTION_INTER_CAM_SYNC_MODE, (float)config_.hw_sync_mode);
                spdlog::info("D455 sensor '{}' inter_cam_sync_mode = {}",
                             s.get_info(RS2_CAMERA_INFO_NAME), config_.hw_sync_mode);
            }
            if (s.supports(RS2_OPTION_FRAMES_QUEUE_SIZE)) {
                try { s.set_option(RS2_OPTION_FRAMES_QUEUE_SIZE, 16.0f); } catch (...) {}
            }
        }

        // Try requested fps first. If it fails (typical when USB 2.1 negotiation
        // limits available bandwidth), fall back through 60 → 30 → 15 so the user
        // gets a working camera connection rather than a hard error.
        const int try_fps[] = {config_.fps, 60, 30, 15, 6};
        bool started = false;
        int  effective_fps = 0;
        std::string last_err;
        for (int fps : try_fps) {
            if (fps <= 0) continue;
            try {
                rs2::config cfg;
                cfg.enable_stream(RS2_STREAM_INFRARED, 1, config_.width, config_.height, RS2_FORMAT_Y8, fps);
                cfg.enable_stream(RS2_STREAM_INFRARED, 2, config_.width, config_.height, RS2_FORMAT_Y8, fps);
                if (config_.enable_depth)
                    cfg.enable_stream(RS2_STREAM_DEPTH, config_.width, config_.height, RS2_FORMAT_Z16, fps);
                if (config_.enable_rgb)
                    cfg.enable_stream(RS2_STREAM_COLOR, 848, 480, RS2_FORMAT_BGR8,
                                      std::min(config_.rgb_fps, fps));
                profile_ = pipeline_.start(cfg);
                rs_config_ = cfg;
                effective_fps = fps;
                started = true;
                break;
            } catch (const rs2::error& e) {
                last_err = e.what();
                spdlog::warn("D455 start at {} fps failed: {} — trying lower rate", fps, last_err);
            }
        }
        if (!started) {
            spdlog::error("D455 could not start at any rate (USB={}): {}", usb_type, last_err);
            return false;
        }
        if (effective_fps != config_.fps) {
            spdlog::warn("D455 fell back to {} fps (requested {}). USB negotiation = {}. "
                         "Reseat USB-C cable for full 90 fps.",
                         effective_fps, config_.fps, usb_type);
            config_.fps = effective_fps;       // reflect actual rate so UI can show it
        }
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
    // Live fps: average frames/sec since first frame arrived. Suppress flicker
    // for the first 0.5 s so the readout doesn't bounce on connect.
    if (first_frame_received_ && s.total_frames > 0) {
        auto now = std::chrono::steady_clock::now();
        double now_s = std::chrono::duration<double>(now.time_since_epoch()).count();
        double elapsed = now_s - first_host_timestamp_;
        if (elapsed > 0.5) s.measured_fps = s.total_frames / elapsed;
    }
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

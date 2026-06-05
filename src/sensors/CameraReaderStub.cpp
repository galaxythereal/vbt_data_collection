/**
 * @file CameraReaderStub.cpp
 * @brief Stub CameraReader implementation for builds without librealsense2.
 */

#include "sensors/CameraReader.h"
#include <spdlog/spdlog.h>

namespace vbt {

CameraReader::CameraReader() = default;
CameraReader::~CameraReader() { stop(); close(); }

bool CameraReader::open(const CameraConfig& config) {
    config_ = config;
    camera_imu_active_ = false;
    serial_.clear();
    is_open_ = false;

    spdlog::warn(
        "Camera support is disabled in this build (VBT_WITH_REALSENSE=0). "
        "Rebuild with -DVBT_WITH_REALSENSE=ON and install librealsense2 to enable the D455 pipeline.");
    return false;
}

void CameraReader::close() {
    stop();
    is_open_ = false;
}

void CameraReader::start() {
    // No-op: RealSense streaming is not available.
    is_running_ = false;
}

void CameraReader::stop() {
    is_running_ = false;
    // stream_thread_ should never be started in the stub build, but join defensively.
    if (stream_thread_.joinable()) stream_thread_.join();
}

CameraFrame CameraReader::get_latest_frame() const {
    std::lock_guard<std::mutex> lock(frame_mutex_);
    return latest_frame_;
}

CameraStats CameraReader::get_stats() const {
    return {};
}

void CameraReader::start_rgb_recording(const std::string& /*output_path*/) {
    recording_rgb_ = false;
}

void CameraReader::stop_rgb_recording() { recording_rgb_ = false; }

} // namespace vbt

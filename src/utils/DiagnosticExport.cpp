#include "utils/DiagnosticExport.h"
#include "utils/Notifications.h"
#include "core/Session.h"
#include "app/Version.h"
#include <filesystem>
#include <fstream>
#include <chrono>
#include <iomanip>
#include <sstream>
#include <spdlog/spdlog.h>

namespace fs = std::filesystem;

namespace vbt {

namespace {
std::string now_filename_safe() {
    auto t = std::chrono::system_clock::to_time_t(std::chrono::system_clock::now());
    std::ostringstream ss;
    ss << std::put_time(std::localtime(&t), "%Y%m%d_%H%M%S");
    return ss.str();
}
}

std::string export_diagnostic_bundle(const Application& app, const std::string& out_dir) {
    try {
        fs::create_directories(out_dir);
    } catch (const fs::filesystem_error& e) {
        spdlog::error("Diagnostic export: cannot create '{}': {}", out_dir, e.what());
        return "";
    }
    std::string base = out_dir + "/diagnostic_" + now_filename_safe();
    std::string json_path = base + ".json";
    std::string txt_path  = base + ".txt";

    nlohmann::json j;
    j["bundle_schema_version"] = 1;
    j["build"] = {
        {"app_version", kAppVersion},
        {"git_sha", kGitSha},
        {"git_branch", kGitBranch},
        {"git_dirty", kGitDirty},
        {"build_timestamp", kBuildTimestamp},
        {"build_type", kBuildType},
        {"compiler", kCompilerId},
        {"system", kSystemName},
        {"arch", kSystemArch}
    };
    auto& s = const_cast<Application&>(app).session();
    j["session"] = {
        {"state", s.get_state_string()},
        {"session_id", s.get_info().session_id},
        {"session_dir", s.get_session_dir()},
        {"subject_id", s.get_info().subject_id},
        {"exercise", s.get_info().exercise},
        {"total_weight_kg", s.get_info().total_weight_kg}
    };
    auto imu_stats = s.imu().get_stats();
    j["imu"] = {
        {"is_running", s.imu().is_running()},
        {"valid_packets", imu_stats.valid_packets},
        {"crc_errors", imu_stats.crc_errors},
        {"sync_errors", imu_stats.sync_errors},
        {"dropouts", imu_stats.dropouts},
        {"measured_rate_hz", imu_stats.measured_rate_hz},
        {"jitter_us_mean", imu_stats.jitter_us_mean},
        {"jitter_us_stddev", imu_stats.jitter_us_stddev},
        {"accel_saturation_count", imu_stats.accel_saturation_count},
        {"gyro_saturation_count", imu_stats.gyro_saturation_count},
        {"gyro_noise_floor_dps", imu_stats.gyro_noise_floor_dps},
        {"accel_noise_floor_g", imu_stats.accel_noise_floor_g}
    };
    auto cam_stats = s.camera().get_stats();
    auto trk_stats = s.tracker().get_stats();
    j["camera"] = {
        {"is_running", s.camera().is_running()},
        {"total_frames", cam_stats.total_frames},
        {"dropped_frames", cam_stats.dropped_frames},
        {"detection_rate", trk_stats.detection_rate},
        {"avg_snr", trk_stats.avg_snr}
    };
    auto sync = s.sync().get_sync_result();
    j["sync"] = {
        {"valid", sync.valid},
        {"offset_us", sync.offset_us},
        {"correlation", sync.correlation},
        {"current_drift_ppm", s.sync().get_current_drift_ppm()},
        {"rearm_required", s.sync().rearm_required()}
    };
    j["config"] = app.config();

    // Recent toasts
    auto history = Notifications::get().snapshot();
    nlohmann::json toasts = nlohmann::json::array();
    for (const auto& t : history) {
        toasts.push_back({
            {"level", t.level == ToastLevel::Error ? "error" :
                       t.level == ToastLevel::Warning ? "warning" :
                       t.level == ToastLevel::Success ? "success" : "info"},
            {"text", t.text}
        });
    }
    j["recent_toasts"] = toasts;

    try {
        std::ofstream f(json_path);
        f << j.dump(2);

        std::ofstream t(txt_path);
        t << "VBT Diagnostic Bundle\n"
          << "Build: " << kAppVersion << " (git " << kGitSha
          << ", " << kBuildTimestamp << ")\n"
          << "System: " << kSystemName << " on " << kSystemArch << "\n"
          << "Session state: " << s.get_state_string() << "\n"
          << "IMU: " << (s.imu().is_running() ? "running" : "stopped")
          << " | rate " << imu_stats.measured_rate_hz << " Hz"
          << " | drops " << imu_stats.dropouts
          << " | crc_err " << imu_stats.crc_errors << "\n"
          << "Camera: " << (s.camera().is_running() ? "running" : "stopped")
          << " | track " << (int)(trk_stats.detection_rate*100) << "%\n"
          << "Sync: drift " << s.sync().get_current_drift_ppm() << " ppm\n\n"
          << "Full state in " << json_path << "\n"
          << "Subject identifying data is NOT included.\n";
    } catch (const std::exception& e) {
        spdlog::error("Diagnostic export write failed: {}", e.what());
        return "";
    }
    Notifications::get().success("Diagnostic bundle written to " + json_path);
    spdlog::info("Diagnostic bundle: {}", json_path);
    return json_path;
}

} // namespace vbt

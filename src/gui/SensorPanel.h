#pragma once
#include "app/Application.h"
#include <imgui.h>

namespace vbt {

class SensorPanel {
public:
    explicit SensorPanel(Application& app) : app_(app) {}
    void render() {
        ImGui::Begin("Sensor Status");
        auto& session = app_.session();

        // IMU Status
        ImGui::SeparatorText("IMU (ICM42688-P)");
        auto& imu = session.imu();
        auto imu_stats = imu.get_stats();

        if (imu.is_running()) {
            ImGui::TextColored(ImVec4(0.3f, 1, 0.3f, 1), "● Connected");
            ImGui::Text("Rate: %.0f Hz", imu_stats.measured_rate_hz);
            ImGui::Text("Jitter: %.1f ± %.1f µs (max %.1f)",
                        imu_stats.jitter_us_mean, imu_stats.jitter_us_stddev, imu_stats.jitter_us_max);
            ImGui::Text("Packets: %lu valid, %lu CRC errors, %lu dropouts",
                        imu_stats.valid_packets, imu_stats.crc_errors, imu_stats.dropouts);

            auto sample = imu.get_latest_sample();
            ImGui::Separator();
            ImGui::Text("Accel: (%.4f, %.4f, %.4f) g", sample.accel_x_g, sample.accel_y_g, sample.accel_z_g);
            ImGui::Text("Gyro:  (%.2f, %.2f, %.2f) dps", sample.gyro_x_dps, sample.gyro_y_dps, sample.gyro_z_dps);
            ImGui::Text("Temp:  %.1f °C", sample.temperature_c);

            if (ImGui::Button("Calibrate Gyro Bias (5s)")) {
                imu.start_gyro_bias_calibration(5000);
            }
            if (imu.is_calibrating()) {
                ImGui::SameLine();
                ImGui::TextColored(ImVec4(1, 1, 0, 1), "Calibrating...");
            }
        } else {
            ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "● Disconnected");
            ImGui::InputText("Port", &app_.config().imu.port[0], app_.config().imu.port.capacity());
            if (ImGui::Button("Connect IMU")) {
                if (imu.open(app_.config().imu)) imu.start();
            }
        }

        ImGui::Spacing();
        ImGui::SeparatorText("Camera (RealSense D455)");
        auto& cam = session.camera();
        auto cam_stats = cam.get_stats();

        if (cam.is_running()) {
            ImGui::TextColored(ImVec4(0.3f, 1, 0.3f, 1), "● Connected");
            ImGui::Text("SN: %s", cam.get_serial().c_str());
            ImGui::Text("Frames: %lu (dropped: %lu)", cam_stats.total_frames, cam_stats.dropped_frames);

            auto tracker_stats = session.tracker().get_stats();
            ImGui::Text("Marker: %s (%.1f%% detection rate)",
                        tracker_stats.detection_rate > 0.95f ? "● Tracking" : "○ Lost",
                        tracker_stats.detection_rate * 100.0f);
            ImGui::Text("Avg SNR: %.1f | Avg Confidence: %.2f",
                        tracker_stats.avg_snr, tracker_stats.avg_confidence);
        } else {
            ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "● Disconnected");
            if (ImGui::Button("Connect Camera")) {
                if (cam.open(app_.config().camera)) {
                    session.tracker().configure(app_.config().camera);
                    cam.start();
                }
            }
        }

        ImGui::Spacing();
        ImGui::SeparatorText("Synchronization");
        auto sync_result = session.sync().get_sync_result();
        if (sync_result.valid) {
            ImGui::TextColored(ImVec4(0.3f, 1, 0.3f, 1), "● Synced");
            ImGui::Text("Offset: %.1f µs (%.3f ms)", sync_result.offset_us, sync_result.offset_us / 1000.0);
            ImGui::Text("Drift: %.1f ppm", session.sync().get_current_drift_ppm());
        } else {
            ImGui::TextColored(ImVec4(1, 1, 0, 1), "○ Not calibrated");
        }
        if (ImGui::Button("Run Tap Test")) {
            session.sync().start_tap_test();
        }
        if (session.sync().is_tap_test_active()) {
            ImGui::SameLine();
            ImGui::TextColored(ImVec4(1, 1, 0, 1), "Tap now!");
            if (ImGui::Button("Finish Tap Test")) {
                session.sync().finish_tap_test();
            }
        }

        ImGui::End();
    }
private:
    Application& app_;
};

} // namespace vbt

#pragma once
#include "app/Application.h"
#include <imgui.h>

namespace vbt {

class CalibrationPanel {
public:
    explicit CalibrationPanel(Application& app) : app_(app) {}

    void render() {
        ImGui::Begin("Calibration Tools");

        auto& session = app_.session();

        ImGui::SeparatorText("6-Position Accelerometer Calibration");
        ImGui::TextWrapped("Place the IMU in each orientation and capture data for 10 seconds per position.");

        const char* pos_names[] = {"+X Up", "-X Up", "+Y Up", "-Y Up", "+Z Up", "-Z Up"};
        // This would be connected to CalibrationManager but shown as UI concept
        for (int i = 0; i < 6; i++) {
            bool captured = false; // Would check calib_manager
            if (captured) {
                ImGui::TextColored(ImVec4(0.3f, 1, 0.3f, 1), "✓ %s", pos_names[i]);
            } else {
                ImGui::Text("○ %s", pos_names[i]);
                ImGui::SameLine();
                char btn_label[32];
                snprintf(btn_label, sizeof(btn_label), "Capture##%d", i);
                if (ImGui::Button(btn_label)) {
                    // Start capture for this position
                }
            }
        }

        ImGui::Spacing();
        if (ImGui::Button("Compute Calibration", ImVec2(-1, 30))) {
            // Compute ellipsoid fitting
        }

        ImGui::Spacing();
        ImGui::SeparatorText("Camera Settings (Live)");
        auto& cam_config = app_.config().camera;
        bool changed = false;
        changed |= ImGui::SliderInt("Exposure (µs)", &cam_config.exposure_us, 10, 5000);
        changed |= ImGui::SliderInt("Gain", &cam_config.gain, 16, 248);
        changed |= ImGui::SliderInt("Marker Threshold", &cam_config.marker_threshold, 50, 254);
        changed |= ImGui::SliderFloat("Min Blob Area", &cam_config.marker_min_area, 5, 100);
        changed |= ImGui::SliderFloat("Max Blob Area", &cam_config.marker_max_area, 50, 2000);

        // Soft centre bias: lower σ → stronger preference for central markers.
        // The two green guide-lines in the IR view show ±1σ and ±2σ.
        changed |= ImGui::SliderFloat("Centre Bias σ", &cam_config.marker_center_bias_sigma_frac,
                                      0.05f, 1.0f, "%.2f");
        ImGui::SameLine();
        if (ImGui::SmallButton("off##bias")) {
            cam_config.marker_center_bias_sigma_frac = 0.0f;
            changed = true;
        }

        if (changed) {
            ImGui::TextColored(ImVec4(1, 1, 0, 1), "Settings changed — restart camera to apply exposure/gain");
            session.tracker().configure(cam_config);
        }

        ImGui::Spacing();
        ImGui::SeparatorText("Camera Intrinsics");
        if (session.camera().is_open()) {
            auto intr = session.camera().get_ir_intrinsics();
            ImGui::Text("fx=%.1f  fy=%.1f", intr.fx, intr.fy);
            ImGui::Text("ppx=%.1f  ppy=%.1f", intr.ppx, intr.ppy);
            ImGui::Text("Model: %d  Size: %dx%d", intr.model, intr.width, intr.height);
        } else {
            ImGui::TextDisabled("Connect camera to view intrinsics");
        }

        ImGui::End();
    }

private:
    Application& app_;
};

} // namespace vbt

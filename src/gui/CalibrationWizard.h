#pragma once

/**
 * @file CalibrationWizard.h
 * @brief Step-by-step accel calibration with countdown, residual gauge, and
 *        per-step diagnostics. Replaces the raw button grid in
 *        CalibrationPanel.
 *
 * Steps:
 *   0. Welcome / instructions
 *   1-6. Six-position capture (X-up, X-down, Y-up, Y-down, Z-up, Z-down)
 *        Each step shows: target axis, expected gravity vector, live readout,
 *        countdown timer, accept/retry buttons.
 *   7. Compute & review (residuals per axis vs ideal 1g).
 */

#include "app/Application.h"
#include "core/CalibrationManager.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <chrono>
#include <cmath>
#include <string>

namespace vbt {

class CalibrationWizard {
public:
    explicit CalibrationWizard(Application& app, CalibrationManager& mgr)
        : app_(app), mgr_(mgr) {}

    void open() { is_open_ = true; step_ = 0; }
    bool is_open() const { return is_open_; }

    void render() {
        if (!is_open_) return;
        ImGui::SetNextWindowSize(ImVec2(640, 480), ImGuiCond_FirstUseEver);
        if (!ImGui::Begin("Calibration Wizard", &is_open_,
                          ImGuiWindowFlags_NoSavedSettings)) {
            ImGui::End();
            return;
        }

        if (step_ == 0)        render_intro();
        else if (step_ <= 6)   render_position_step();
        else                   render_review();

        ImGui::End();
        if (!is_open_) reset();
    }

private:
    static const char* pos_name(int s) {
        switch (s) {
            case 1: return "X axis pointing UP";
            case 2: return "X axis pointing DOWN";
            case 3: return "Y axis pointing UP";
            case 4: return "Y axis pointing DOWN";
            case 5: return "Z axis pointing UP";
            case 6: return "Z axis pointing DOWN";
        }
        return "?";
    }
    static CalibrationManager::CalibPosition pos_for_step(int s) {
        using P = CalibrationManager::CalibPosition;
        switch (s) {
            case 1: return P::POS_X_UP;
            case 2: return P::POS_X_DOWN;
            case 3: return P::POS_Y_UP;
            case 4: return P::POS_Y_DOWN;
            case 5: return P::POS_Z_UP;
            case 6: return P::POS_Z_DOWN;
        }
        return P::POS_X_UP;
    }
    static void expected_g(int s, float& gx, float& gy, float& gz) {
        gx = gy = gz = 0;
        switch (s) {
            case 1: gx = +1; break; case 2: gx = -1; break;
            case 3: gy = +1; break; case 4: gy = -1; break;
            case 5: gz = +1; break; case 6: gz = -1; break;
        }
    }

    void reset() {
        step_ = 0;
        capturing_ = false;
        countdown_start_ = {};
    }

    void render_intro() {
        ImGui::TextWrapped(
            "This wizard will guide you through a six-position accelerometer "
            "calibration. For each step you will hold the IMU stationary in a "
            "specific orientation for a few seconds. The order matters — follow "
            "the prompts exactly. Each capture takes ~3 seconds.\n\n"
            "Tip: brace the bar on the floor or in the rack so it doesn't move "
            "during capture. The live gravity vector will turn green when "
            "alignment is within tolerance.");
        ImGui::Spacing();
        if (ImGui::Button("Start", ImVec2(160, 0))) step_ = 1;
        ImGui::SameLine();
        if (ImGui::Button("Cancel", ImVec2(120, 0))) is_open_ = false;
    }

    void render_position_step() {
        auto& imu = app_.session().imu();

        // Header
        ImGui::Text("Step %d / 6: %s", step_, pos_name(step_));
        ImGui::Separator();

        float gx, gy, gz; expected_g(step_, gx, gy, gz);
        ImGui::Text("Target gravity vector: (%+0.0f, %+0.0f, %+0.0f) g", gx, gy, gz);

        // Live readout
        auto sample = imu.get_latest_sample();
        float mag = std::sqrt(sample.accel_x_g * sample.accel_x_g +
                              sample.accel_y_g * sample.accel_y_g +
                              sample.accel_z_g * sample.accel_z_g);
        float dot = sample.accel_x_g * gx + sample.accel_y_g * gy + sample.accel_z_g * gz;
        bool aligned = (std::abs(dot - 1.0f) < 0.05f) && (std::abs(mag - 1.0f) < 0.03f);

        ImGui::Spacing();
        ImVec4 col = aligned ? ImVec4(0.30f, 0.85f, 0.40f, 1) : ImVec4(1, 0.78f, 0.25f, 1);
        ImGui::PushStyleColor(ImGuiCol_Text, col);
        ImGui::Text("Live: (%+0.3f, %+0.3f, %+0.3f) g  |mag|=%.3f",
                    sample.accel_x_g, sample.accel_y_g, sample.accel_z_g, mag);
        ImGui::PopStyleColor();
        ImGui::Text("Alignment dot product = %.3f (target ≥ 0.95)", dot);

        // Countdown / capture
        ImGui::Spacing();
        if (!capturing_) {
            ImGui::BeginDisabled(!aligned);
            if (ImGui::Button("Begin capture (3 s)", ImVec2(220, 0))) {
                mgr_.start_position_capture(pos_for_step(step_));
                capturing_ = true;
                countdown_start_ = std::chrono::steady_clock::now();
            }
            ImGui::EndDisabled();
            if (!aligned) {
                ImGui::SameLine();
                ImGui::TextDisabled("Hold steady — alignment not yet within tolerance");
            }
        } else {
            float elapsed = std::chrono::duration<float>(
                std::chrono::steady_clock::now() - countdown_start_).count();
            float pct = elapsed / 3.0f;
            if (pct > 1) pct = 1;
            char overlay[32];
            std::snprintf(overlay, sizeof(overlay), "%.1f s", 3.0f - elapsed);
            ImGui::ProgressBar(pct, ImVec2(-1, 22), overlay);

            // Feed samples to manager
            mgr_.feed_accel_sample(sample.accel_x_g, sample.accel_y_g, sample.accel_z_g);

            if (elapsed >= 3.0f) {
                mgr_.finish_position_capture();
                capturing_ = false;
                Notifications::get().success(
                    std::string("Captured ") + pos_name(step_));
                if (step_ < 6) ++step_;
                else step_ = 7;
            }
        }

        ImGui::Spacing();
        if (ImGui::Button("Skip step", ImVec2(120, 0))) {
            if (step_ < 6) ++step_;
            else step_ = 7;
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel wizard", ImVec2(140, 0))) is_open_ = false;
    }

    void render_review() {
        if (mgr_.all_positions_captured()) {
            if (ImGui::Button("Compute calibration", ImVec2(220, 0))) {
                bool ok = mgr_.compute_accel_calibration();
                if (ok) {
                    Notifications::get().success("Accelerometer calibration computed");
                    if (app_.session().events().is_open()) {
                        app_.session().events().info("calibration", "accel_complete",
                            "Six-position accel calibration computed");
                    }
                } else {
                    Notifications::get().error("Calibration computation failed; check captures");
                }
            }
            ImGui::SameLine();
            if (ImGui::Button("Save", ImVec2(120, 0))) {
                std::string dir = app_.config().dataset_root + "/calibration";
                if (mgr_.save_all(dir)) {
                    Notifications::get().success("Calibration saved to " + dir);
                } else {
                    Notifications::get().error("Failed to save calibration to " + dir);
                }
            }
        } else {
            ImGui::TextColored(ImVec4(1, 0.78f, 0.25f, 1),
                "Only %d / 6 positions captured. Some sensitivity will be missing.",
                mgr_.positions_captured());
        }
        ImGui::Separator();
        if (ImGui::Button("Close", ImVec2(120, 0))) is_open_ = false;
    }

    Application&         app_;
    CalibrationManager&  mgr_;
    bool   is_open_ = false;
    int    step_    = 0;     // 0 = intro, 1..6 = positions, 7 = review
    bool   capturing_ = false;
    std::chrono::steady_clock::time_point countdown_start_{};
};

} // namespace vbt

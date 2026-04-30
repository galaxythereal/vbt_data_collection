#pragma once

/**
 * @file PreflightPanel.h
 * @brief Pre-flight checklist gating session start.
 *
 * Each check returns Pass / Warn / Fail and a message. The Start button
 * is disabled while any check is Fail. Operator can override (logs to
 * SessionInfo.preflight_overrides as audit trail).
 */

#include "app/Application.h"
#include "core/Session.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <string>
#include <vector>
#include <cmath>

namespace vbt {

enum class CheckStatus { Pass, Warn, Fail };

struct CheckResult {
    std::string  name;
    CheckStatus  status;
    std::string  detail;
};

class PreflightPanel {
public:
    explicit PreflightPanel(Application& app) : app_(app) {}

    /// Run all checks and return them in order.
    std::vector<CheckResult> run_checks() const {
        std::vector<CheckResult> r;
        auto& session = app_.session();
        const auto& cfg = app_.config();

        // IMU connection + rate
        if (!session.imu().is_running()) {
            r.push_back({"IMU connected", CheckStatus::Fail,
                "No IMU connected. Plug in the ESP32 USB cable, check the port "
                "in Sensor Status, then click Connect IMU."});
        } else {
            auto s = session.imu().get_stats();
            if (s.measured_rate_hz < cfg.imu.odr_hz * 0.95) {
                r.push_back({"IMU rate", CheckStatus::Warn,
                    "Measured rate " + std::to_string((int)s.measured_rate_hz) +
                    " Hz < expected " + std::to_string(cfg.imu.odr_hz) +
                    " Hz. Possible packet loss."});
            } else {
                r.push_back({"IMU rate", CheckStatus::Pass,
                    std::to_string((int)s.measured_rate_hz) + " Hz"});
            }
            // Saturation
            if (s.accel_saturation_count > 5 || s.gyro_saturation_count > 5) {
                r.push_back({"IMU saturation", CheckStatus::Warn,
                    "Accel saturated " + std::to_string(s.accel_saturation_count) +
                    "× / Gyro " + std::to_string(s.gyro_saturation_count) +
                    "×. Check FSR settings or remount."});
            }
        }

        // Camera + marker tracking
        if (!session.camera().is_running()) {
            r.push_back({"Camera connected", CheckStatus::Fail,
                "RealSense D455 not connected. Plug in USB-C, check `rs-enumerate-devices`, "
                "then click Connect Camera."});
        } else {
            auto t = session.tracker().get_stats();
            if (t.detection_rate < 0.80f) {
                r.push_back({"Marker tracking", CheckStatus::Fail,
                    "Detection rate " + std::to_string((int)(t.detection_rate*100)) +
                    "% < 80%. Re-aim camera, check IR markers visible, increase exposure."});
            } else if (t.detection_rate < 0.95f) {
                r.push_back({"Marker tracking", CheckStatus::Warn,
                    "Detection " + std::to_string((int)(t.detection_rate*100)) + "%"});
            } else {
                r.push_back({"Marker tracking", CheckStatus::Pass,
                    std::to_string((int)(t.detection_rate*100)) + "%"});
            }
            if (t.avg_snr < 5.0f) {
                r.push_back({"Marker SNR", CheckStatus::Warn,
                    "Mean SNR " + std::to_string(t.avg_snr) +
                    " is low — bright IR contamination?"});
            }
        }

        // Sync
        auto sync = session.sync().get_sync_result();
        double drift = std::abs(session.sync().get_current_drift_ppm());
        if (!sync.valid) {
            r.push_back({"Clock sync", CheckStatus::Warn,
                "Tap-test not run. Click 'Run Tap Test' to refine offset."});
        } else if (drift > cfg.sync.max_drift_ppm) {
            r.push_back({"Clock sync", CheckStatus::Fail,
                "Drift " + std::to_string((int)drift) + " ppm > limit " +
                std::to_string((int)cfg.sync.max_drift_ppm) +
                ". Re-run tap-test."});
        } else {
            r.push_back({"Clock sync", CheckStatus::Pass,
                "Drift " + std::to_string((int)drift) + " ppm"});
        }
        if (session.sync().rearm_required()) {
            r.push_back({"Sync rearm", CheckStatus::Fail,
                "Drift exceeded auto-rearm threshold during recording. Run a fresh tap-test."});
        }

        // Subject metadata
        if (session.get_info().subject_id.empty()) {
            r.push_back({"Subject ID", CheckStatus::Fail,
                "Subject ID is empty. Enter at least an anonymised ID."});
        } else {
            r.push_back({"Subject ID", CheckStatus::Pass, session.get_info().subject_id});
        }

        // Disk space (best-effort: check /tmp for now, kept simple)
        // Skipping precise free-space check here for portability.

        return r;
    }

    bool render_and_check_blocking() {
        auto checks = run_checks();
        bool any_fail = false;
        ImGui::TextDisabled("Pre-flight checklist");
        ImGui::Separator();
        for (const auto& c : checks) {
            ImVec4 col;
            const char* tag;
            switch (c.status) {
                case CheckStatus::Pass:  col = {0.30f,0.85f,0.40f,1}; tag = "OK  "; break;
                case CheckStatus::Warn:  col = {1,0.75f,0.20f,1};      tag = "WARN"; break;
                case CheckStatus::Fail:  col = {1,0.30f,0.25f,1};      tag = "FAIL"; any_fail = true; break;
            }
            ImGui::PushStyleColor(ImGuiCol_Text, col);
            ImGui::Text("[%s]", tag);
            ImGui::PopStyleColor();
            ImGui::SameLine();
            ImGui::TextWrapped("%-22s %s", c.name.c_str(), c.detail.c_str());
        }
        return !any_fail;
    }

    /// Logs override and returns true.
    bool record_override(const std::string& reason) {
        auto& info = app_.session().mutable_info();
        info.preflight_overrides.push_back(reason);
        if (app_.session().events().is_open()) {
            app_.session().events().warn("preflight", "override",
                "Operator overrode pre-flight failure: " + reason);
        }
        Notifications::get().warn("Pre-flight override recorded: " + reason);
        return true;
    }

private:
    Application& app_;
};

} // namespace vbt

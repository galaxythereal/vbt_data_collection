#pragma once
#include "app/Application.h"
#include <imgui.h>
#include <algorithm>

namespace vbt {

class ValidationPanel {
public:
    explicit ValidationPanel(Application& app) : app_(app) {}

    void render() {
        ImGui::Begin("Validation Metrics");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        auto metrics = session.validator().compute_live(100);

        // ── Position ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Position");
        ImGui::PopStyleColor();

        float pos_rmse_mm = (float)(metrics.pos_rmse_m * 1000.0);
        ImVec4 pos_color = pos_rmse_mm < 10 ? ImVec4(0.2f,0.9f,0.3f,1) :
                           pos_rmse_mm < 20 ? ImVec4(1,0.8f,0.2f,1) :
                                              ImVec4(1,0.3f,0.2f,1);
        ImGui::TextColored(pos_color, "RMSE: %.1f mm", pos_rmse_mm);
        ImGui::Text("MAE: %.1f mm", metrics.pos_mae_m * 1000.0);
        ImGui::Text("R: %.4f", metrics.pos_correlation);

        float pq = std::clamp(1.0f - pos_rmse_mm / 30.0f, 0.0f, 1.0f);
        ImGui::ProgressBar(pq, ImVec2(-1, 0),
                           pq > 0.8f ? "Excellent" :
                           pq > 0.5f ? "Good" : "Poor");

        ImGui::Spacing();

        // ── Velocity ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Velocity");
        ImGui::PopStyleColor();

        float vel_rmse = (float)(metrics.vel_rmse_mps * 1000.0);
        ImVec4 vc = vel_rmse < 20 ? ImVec4(0.2f,0.9f,0.3f,1) :
                    vel_rmse < 50 ? ImVec4(1,0.8f,0.2f,1) :
                                    ImVec4(1,0.3f,0.2f,1);
        ImGui::TextColored(vc, "RMSE: %.1f mm/s", vel_rmse);
        ImGui::Text("R: %.4f", metrics.vel_correlation);

        ImGui::Spacing();

        // ── Tracking ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Tracking");
        ImGui::PopStyleColor();

        auto ts = session.tracker().get_stats();
        float dr = ts.detection_rate;
        ImVec4 tc = dr > 0.99f ? ImVec4(0.2f,0.9f,0.3f,1) :
                    dr > 0.95f ? ImVec4(1,0.8f,0.2f,1) :
                                 ImVec4(1,0.3f,0.2f,1);
        ImGui::TextColored(tc, "Detection: %.1f%%", dr * 100.0f);
        ImGui::Text("Frames: %lu | Lost: %lu", ts.total_frames, ts.lost_frames);
        ImGui::ProgressBar(dr, ImVec2(-1, 0),
                           dr > 0.99f ? "Excellent" :
                           dr > 0.95f ? "Good" : "Adjust");

        ImGui::Spacing();

        // ── Sync ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Sync");
        ImGui::PopStyleColor();

        auto sync = session.sync().get_sync_result();
        if (sync.valid) {
            ImGui::Text("Offset: %.1f us", sync.offset_us);
            ImGui::Text("Drift: %.1f ppm", session.sync().get_current_drift_ppm());
        } else {
            ImGui::TextDisabled("Not calibrated");
        }
    }

private:
    Application& app_;
};

} // namespace vbt

#pragma once

/**
 * @file OperatorView.h
 * @brief Full-screen "now recording" view with giant numerals.
 *
 * Toggle with F12. Shows: rep count, last MV / PV / ROM, velocity loss %,
 * stop-set recommendation banner, and a single STOP button. Designed to
 * be readable from across the gym.
 */

#include "app/Application.h"
#include "core/Session.h"
#include "processing/Autoregulation.h"
#include "utils/AudioCue.h"
#include <imgui.h>
#include <implot.h>
#include <cmath>
#include <string>

namespace vbt {

class OperatorView {
public:
    explicit OperatorView(Application& app) : app_(app) {}

    /// Render the operator view as a full-window overlay.
    /// Returns false if user pressed Escape / clicked Close.
    bool render() {
        const ImGuiViewport* vp = ImGui::GetMainViewport();
        ImGui::SetNextWindowPos(vp->WorkPos);
        ImGui::SetNextWindowSize(vp->WorkSize);
        ImGui::PushStyleVar(ImGuiStyleVar_WindowRounding, 0.0f);
        ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0.04f, 0.05f, 0.08f, 1.0f));
        ImGui::Begin("##OperatorView", nullptr,
            ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
            ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
            ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoBringToFrontOnFocus);

        auto& s = app_.session();
        auto stats = s.get_recording_stats();
        const auto& reps = s.segmenter().get_reps();

        // Header
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.75f, 1.0f, 1.0f));
        ImGui::SetWindowFontScale(2.0f);
        ImGui::Text("%s  |  %s  |  %.1f kg",
            s.get_state_string().c_str(),
            s.get_info().exercise.c_str(),
            s.get_info().total_weight_kg);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::PopStyleColor();

        ImGui::Separator();

        // Big rep counter
        ImGui::SetWindowFontScale(8.0f);
        ImGui::Text("%d", stats.rep_count);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::SameLine();
        ImGui::TextDisabled("of %d", s.get_info().target_reps);

        // Last rep velocity (huge)
        if (!reps.empty()) {
            const auto& r = reps.back();
            ImGui::Spacing();
            ImGui::TextDisabled("Mean / Peak velocity (last rep)");
            ImGui::SetWindowFontScale(5.0f);
            ImGui::TextColored(ImVec4(0.40f, 0.85f, 1.0f, 1),
                "%.2f m/s   /   %.2f m/s",
                r.mean_concentric_velocity, r.peak_concentric_velocity);
            ImGui::SetWindowFontScale(1.0f);
            ImGui::TextDisabled("ROM %.0f cm  |  Concentric %.2f s",
                r.rom_m * 100.0f,
                r.concentric.t_end_s - r.concentric.t_start_s);
        }

        // Velocity loss / autoregulation
        auto ar = s.autoreg().compute(reps);
        ImGui::Spacing();
        ImGui::Spacing();
        if (ar.reps_completed >= 2) {
            ImVec4 col = ar.velocity_loss_pct < 10 ? ImVec4(0.30f,0.85f,0.40f,1) :
                          ar.velocity_loss_pct < 20 ? ImVec4(1,0.78f,0.25f,1) :
                                                       ImVec4(1,0.30f,0.30f,1);
            ImGui::SetWindowFontScale(3.0f);
            ImGui::TextColored(col, "VL  %.0f%%", ar.velocity_loss_pct);
            ImGui::SetWindowFontScale(1.0f);
            if (ar.stop_set_recommended) {
                ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1, 0.20f, 0.20f, 1));
                ImGui::SetWindowFontScale(2.5f);
                ImGui::Text(">> STOP SET — velocity loss threshold reached <<");
                ImGui::SetWindowFontScale(1.0f);
                ImGui::PopStyleColor();
                if (!stop_alerted_) {
                    stop_alerted_ = true;
                    AudioCue::play(Cue::Warning);
                }
            } else {
                stop_alerted_ = false;
            }
        }

        // Velocity-time strip
        if (!reps.empty()) {
            ImGui::Spacing();
            std::vector<float> ys;
            for (const auto& r : reps) ys.push_back(r.mean_concentric_velocity);
            if (ImPlot::BeginPlot("##veltrend", ImVec2(-1, 180),
                                  ImPlotFlags_NoLegend | ImPlotFlags_NoTitle)) {
                ImPlot::SetupAxes("Rep", "MV (m/s)",
                    ImPlotAxisFlags_AutoFit, ImPlotAxisFlags_AutoFit);
                ImPlot::PlotLine("MV", ys.data(), (int)ys.size());
                ImPlot::PlotScatter("MV", ys.data(), (int)ys.size());
                ImPlot::EndPlot();
            }
        }

        // Big STOP button
        ImGui::Spacing();
        if (s.get_state() == SessionState::RECORDING) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.75f, 0.18f, 0.18f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.90f, 0.25f, 0.25f, 1.0f));
            if (ImGui::Button("STOP RECORDING", ImVec2(-1, 80))) {
                s.stop_recording();
                AudioCue::play(Cue::StopRecord);
            }
            ImGui::PopStyleColor(2);
        }

        ImGui::TextDisabled("F12 to exit operator view");
        ImGui::End();
        ImGui::PopStyleColor();
        ImGui::PopStyleVar();
        return true;
    }

private:
    Application& app_;
    bool stop_alerted_ = false;
};

} // namespace vbt

/**
 * @file MarkerQualityPanel.cpp
 */
#include "annotation/MarkerQualityPanel.h"
#include <implot.h>
#include <algorithm>
#include <cmath>

namespace vbt {

void MarkerQualityPanel::set_session(SessionData* s) {
    session_ = s;
    if (s) live_cfg_ = s->clean_config();
    cfg_pending_ = false;
}

void MarkerQualityPanel::render() {
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("Load a session to inspect marker quality.");
        return;
    }
    if (session_->marker().size() == 0) {
        ImGui::TextDisabled("This session has no camera/marker data.");
        return;
    }
    apply_cleaning_if_pending_();
    if (ImGui::CollapsingHeader("Cleaning controls", ImGuiTreeNodeFlags_DefaultOpen))
        render_cleaning_controls_();
    if (ImGui::CollapsingHeader("Quality histograms", ImGuiTreeNodeFlags_DefaultOpen))
        render_quality_histograms_();
    if (ImGui::CollapsingHeader("Outlier scatter (raw vz)"))
        render_outlier_scatter_();
}

void MarkerQualityPanel::render_cleaning_controls_() {
    bool ed = false;
    ed |= ImGui::SliderFloat("confidence min",  &live_cfg_.conf_min,  0.0f, 1.0f, "%.2f");
    ed |= ImGui::SliderFloat("SNR min",         &live_cfg_.snr_min,   0.0f, 20.0f, "%.2f");
    ed |= ImGui::SliderFloat("circularity min", &live_cfg_.circ_min,  0.0f, 1.0f, "%.2f");
    ed |= ImGui::SliderInt  ("Hampel window",   &live_cfg_.hampel_window, 3, 31);
    ed |= ImGui::SliderFloat("Hampel σ",        &live_cfg_.hampel_sigmas, 1.0f, 6.0f, "%.1f");
    ed |= ImGui::SliderFloat("LP cutoff (Hz)",  &live_cfg_.lp_cutoff_hz, 1.0f, 30.0f, "%.1f");
    ed |= ImGui::SliderFloat("v_max cap (m/s)", &live_cfg_.v_max_mps,    0.0f, 8.0f, "%.2f");
    if (ed) {
        cfg_pending_ = true;
        cfg_last_change_ = std::chrono::steady_clock::now();
    }
    if (cfg_pending_)
        ImGui::TextDisabled("(re-cleaning when idle…)");
}

void MarkerQualityPanel::apply_cleaning_if_pending_() {
    if (!cfg_pending_) return;
    auto now = std::chrono::steady_clock::now();
    auto idle = std::chrono::duration_cast<std::chrono::milliseconds>(now - cfg_last_change_).count();
    if (idle < 200) return;
    session_->set_clean_config(live_cfg_);
    session_->recompute_clean_signal(live_cfg_);
    cfg_pending_ = false;
}

void MarkerQualityPanel::render_quality_histograms_() {
    const auto& m = session_->marker();
    const int N = (int)m.size();
    if (N == 0) return;

    // Build accepted/rejected masks under the current thresholds.
    std::vector<float> conf_acc, conf_rej, snr_acc, snr_rej, circ_acc, circ_rej;
    conf_acc.reserve(N); snr_acc.reserve(N); circ_acc.reserve(N);
    int n_acc = 0;
    for (int i = 0; i < N; ++i) {
        bool acc = m.detected[i]
                && m.confidence[i]  >= live_cfg_.conf_min
                && m.snr[i]         >= live_cfg_.snr_min
                && m.circularity[i] >= live_cfg_.circ_min;
        if (acc) { ++n_acc;
                   conf_acc.push_back(m.confidence[i]);
                   snr_acc.push_back(m.snr[i]);
                   circ_acc.push_back(m.circularity[i]); }
        else     { conf_rej.push_back(m.confidence[i]);
                   snr_rej.push_back(m.snr[i]);
                   circ_rej.push_back(m.circularity[i]); }
    }
    float frac_acc = N > 0 ? (float)n_acc / N : 0;
    ImGui::Text("Frames: %d  ·  accepted: %d (%.1f %%)  ·  rejected: %d",
                 N, n_acc, frac_acc * 100.0f, N - n_acc);

    auto plot_hist = [&](const char* title,
                          const std::vector<float>& acc,
                          const std::vector<float>& rej,
                          float lo, float hi) {
        if (ImPlot::BeginPlot(title, ImVec2(-1, 150),
                               ImPlotFlags_None)) {
            ImPlot::SetupAxes(title, "count",
                               ImPlotAxisFlags_None,
                               ImPlotAxisFlags_AutoFit);
            ImPlot::SetupAxisLimits(ImAxis_X1, lo, hi, ImGuiCond_Always);
            ImPlot::SetupLegend(ImPlotLocation_NorthEast,
                                ImPlotLegendFlags_Outside);
            ImPlot::PushStyleColor(ImPlotCol_Fill, ImVec4(0.30f, 0.85f, 1.0f, 0.85f));
            if (!acc.empty())
                ImPlot::PlotHistogram("accepted", acc.data(), (int)acc.size(),
                                       40, 1.0, ImPlotRange(lo, hi));
            ImPlot::PopStyleColor();
            ImPlot::PushStyleColor(ImPlotCol_Fill, ImVec4(1.0f, 0.45f, 0.30f, 0.85f));
            if (!rej.empty())
                ImPlot::PlotHistogram("rejected", rej.data(), (int)rej.size(),
                                       40, 1.0, ImPlotRange(lo, hi));
            ImPlot::PopStyleColor();
            ImPlot::EndPlot();
        }
    };
    plot_hist("confidence", conf_acc, conf_rej, 0.0f, 1.0f);
    plot_hist("SNR",        snr_acc,  snr_rej,  0.0f, 20.0f);
    plot_hist("circularity",circ_acc, circ_rej, 0.0f, 1.0f);
}

void MarkerQualityPanel::render_outlier_scatter_() {
    const auto& m = session_->marker();
    if (m.size() == 0 || m.vz_clean_mps.empty()) return;
    const int N = (int)m.size();
    // Flag any sample whose raw vz (numerical derivative of raw -y_m)
    // exceeds the current cap; show those over the cleaned vz curve.
    static std::vector<float> tx, vy_clean, vy_raw, t_outlier, v_outlier;
    tx.resize(N); vy_clean.resize(N); vy_raw.resize(N);
    t_outlier.clear(); v_outlier.clear();
    double t0 = session_->t0_unified_s();
    float v_cap = live_cfg_.v_max_mps > 0 ? live_cfg_.v_max_mps : 4.0f;
    for (int i = 0; i < N; ++i) {
        tx[i] = (float)(m.unified_t_s[i] - t0);
        vy_clean[i] = m.vz_clean_mps[i];
    }
    for (int i = 1; i + 1 < N; ++i) {
        double dt = m.unified_t_s[i + 1] - m.unified_t_s[i - 1];
        float vz_raw = (dt > 1e-6) ? (-m.y_m[i + 1] + m.y_m[i - 1]) / (float)dt : 0.0f;
        vy_raw[i] = vz_raw;
        if (std::fabs(vz_raw) > v_cap) {
            t_outlier.push_back(tx[i]);
            v_outlier.push_back(vz_raw);
        }
    }
    ImGui::Text("Outliers above v_max: %d frames", (int)t_outlier.size());
    if (ImPlot::BeginPlot("Outlier inspection (raw vs cleaned vz)",
                           ImVec2(-1, 240), ImPlotFlags_None)) {
        ImPlot::SetupAxes("time (s, since session start)", "vz (m/s)",
                           ImPlotAxisFlags_None, ImPlotAxisFlags_AutoFit);
        ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");
        ImPlot::SetupAxisFormat(ImAxis_Y1, "%.2f m/s");
        ImPlot::SetupLegend(ImPlotLocation_NorthEast,
                            ImPlotLegendFlags_Outside);
        // Raw drawn first (faded grey), clean on top (bright lime), then
        // outliers as bold red markers.
        ImPlot::PushStyleColor(ImPlotCol_Line,
                                ImVec4(0.65f, 0.65f, 0.65f, 0.55f));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 1.2f);
        ImPlot::PlotLine("raw",   tx.data(), vy_raw.data(), N);
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
        ImPlot::PushStyleColor(ImPlotCol_Line,
                                ImVec4(0.40f, 0.95f, 0.40f, 1));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.4f);
        ImPlot::PlotLine("cleaned", tx.data(), vy_clean.data(), N);
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
        if (!t_outlier.empty()) {
            ImPlot::PushStyleColor(ImPlotCol_MarkerOutline,
                                    ImVec4(1, 0.4f, 0.3f, 1));
            ImPlot::PushStyleColor(ImPlotCol_MarkerFill,
                                    ImVec4(1, 0.4f, 0.3f, 1));
            ImPlot::PushStyleVar(ImPlotStyleVar_MarkerSize, 5.5f);
            ImPlot::SetNextMarkerStyle(ImPlotMarker_Circle);
            ImPlot::PlotScatter("outliers (double-click to seek)",
                                 t_outlier.data(), v_outlier.data(),
                                 (int)t_outlier.size());
            ImPlot::PopStyleVar();
            ImPlot::PopStyleColor(2);
        }
        // Double-click handler on the plot — seek to the closest outlier.
        if (ImPlot::IsPlotHovered() && ImGui::IsMouseDoubleClicked(ImGuiMouseButton_Left)) {
            ImPlotPoint p = ImPlot::GetPlotMousePos();
            // Find nearest outlier in time.
            if (!t_outlier.empty() && on_seek_) {
                int best = 0; float bd = 1e9f;
                for (int i = 0; i < (int)t_outlier.size(); ++i) {
                    float d = std::fabs(t_outlier[i] - (float)p.x);
                    if (d < bd) { bd = d; best = i; }
                }
                on_seek_(t0 + t_outlier[best]);
            }
        }
        ImPlot::EndPlot();
    }
}

} // namespace vbt

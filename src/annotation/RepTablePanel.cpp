/**
 * @file RepTablePanel.cpp
 */
#include "annotation/RepTablePanel.h"
#include <algorithm>
#include <cmath>

namespace vbt {

void RepTablePanel::render() {
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("No session loaded.");
        return;
    }
    render_toolbar_();
    ImGui::Separator();
    render_metric_summary_();
    ImGui::Separator();
    render_table_();
}

void RepTablePanel::render_toolbar_() {
    if (ImGui::Button("Recompute all metrics")) {
        session_->recompute_all_rep_metrics();
        session_->mark_reps_dirty();
    }
    ImGui::SameLine();
    if (ImGui::Button("Renumber sequential")) {
        int next = 1;
        for (auto& r : session_->mutable_reps()) r.rep_id = next++;
        session_->mark_reps_dirty();
    }
    ImGui::SameLine();
    if (ImGui::Button("Insert blank rep at end")) {
        RepAnnotation r;
        r.rep_id = (int)session_->reps().size() + 1;
        double t = session_->t0_unified_s();
        if (!session_->reps().empty()) t = session_->reps().back().rest.t_end_s;
        r.concentric.t_start_s = t;
        r.concentric.t_end_s   = t + 0.5;
        r.eccentric.t_start_s  = r.concentric.t_end_s;
        r.eccentric.t_end_s    = t + 1.0;
        r.rest.t_start_s       = r.eccentric.t_end_s;
        r.rest.t_end_s         = t + 1.5;
        r.concentric.source = r.eccentric.source = r.rest.source = "manual";
        session_->mutable_reps().push_back(r);
        session_->recompute_rep_metrics((int)session_->reps().size() - 1);
        session_->mark_reps_dirty();
    }
    ImGui::SameLine();
    if (ImGui::Button("Delete selected") && selected_ >= 0
        && selected_ < (int)session_->reps().size()) {
        session_->mutable_reps().erase(
            session_->mutable_reps().begin() + selected_);
        session_->mark_reps_dirty();
        selected_ = -1;
    }
}

void RepTablePanel::render_metric_summary_() {
    const auto& reps = session_->reps();
    if (reps.empty()) {
        ImGui::TextDisabled("No reps detected. Shift+drag the timeline to annotate manually.");
        return;
    }
    // Roll-up: mean / sd / min / max of peak velocity, ROM.
    double sum_pv = 0, sum_pv2 = 0, sum_rom = 0, sum_rom2 = 0;
    double minpv =  1e9, maxpv = -1e9;
    double minrom = 1e9, maxrom = -1e9;
    for (const auto& r : reps) {
        sum_pv  += r.peak_concentric_velocity;
        sum_pv2 += r.peak_concentric_velocity * r.peak_concentric_velocity;
        sum_rom += r.rom_m;
        sum_rom2 += r.rom_m * r.rom_m;
        minpv  = std::min<double>(minpv,  r.peak_concentric_velocity);
        maxpv  = std::max<double>(maxpv,  r.peak_concentric_velocity);
        minrom = std::min<double>(minrom, r.rom_m);
        maxrom = std::max<double>(maxrom, r.rom_m);
    }
    double n = (double)reps.size();
    double mean_pv  = sum_pv / n;
    double sd_pv    = std::sqrt(std::max(0.0, sum_pv2 / n - mean_pv * mean_pv));
    double mean_rom = sum_rom / n;
    double sd_rom   = std::sqrt(std::max(0.0, sum_rom2 / n - mean_rom * mean_rom));
    // Velocity loss: drop from rep 1 peak to rep N peak.
    double vloss_pct = 100.0 * (reps.front().peak_concentric_velocity
                                 - reps.back().peak_concentric_velocity)
                              / std::max(0.05f, reps.front().peak_concentric_velocity);

    ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1),
                       "Set summary  ·  %d reps", (int)reps.size());
    ImGui::Text("Peak vel    : %.3f ± %.3f  m/s   "
                "(min %.3f, max %.3f)",
                mean_pv, sd_pv, minpv, maxpv);
    ImGui::Text("ROM         : %.0f ± %.0f  mm    "
                "(min %.0f, max %.0f)",
                mean_rom * 1000, sd_rom * 1000,
                minrom * 1000, maxrom * 1000);
    ImGui::Text("Velocity loss (rep1 → repN): %+.1f %%", vloss_pct);
}

void RepTablePanel::render_table_() {
    const ImGuiTableFlags flags =
        ImGuiTableFlags_RowBg | ImGuiTableFlags_Borders
        | ImGuiTableFlags_Resizable | ImGuiTableFlags_ScrollY
        | ImGuiTableFlags_SizingFixedFit;
    if (!ImGui::BeginTable("##reptab", 9, flags, ImVec2(-1, 280))) return;
    ImGui::TableSetupScrollFreeze(0, 1);
    ImGui::TableSetupColumn("ID", ImGuiTableColumnFlags_WidthFixed, 36);
    ImGui::TableSetupColumn("t_start (s)");
    ImGui::TableSetupColumn("t_end (s)");
    ImGui::TableSetupColumn("conc dur");
    ImGui::TableSetupColumn("ecc dur");
    ImGui::TableSetupColumn("peak vel (m/s)");
    ImGui::TableSetupColumn("mean vel (m/s)");
    ImGui::TableSetupColumn("ROM (mm)");
    ImGui::TableSetupColumn("source");
    ImGui::TableHeadersRow();

    double t0 = session_->t0_unified_s();
    auto& reps = session_->mutable_reps();
    for (int i = 0; i < (int)reps.size(); ++i) {
        auto& r = reps[i];
        ImGui::TableNextRow();
        ImGui::PushID(i);

        ImGui::TableNextColumn();
        bool sel = (i == selected_);
        char idbuf[8];
        std::snprintf(idbuf, sizeof(idbuf), "%d", r.rep_id);
        if (ImGui::Selectable(idbuf, sel,
                              ImGuiSelectableFlags_SpanAllColumns)) {
            selected_ = i;
            if (on_select_) on_select_(i);
        }

        ImGui::TableNextColumn();
        float t_start = (float)(r.concentric.t_start_s - t0);
        if (ImGui::DragFloat("##ts", &t_start, 0.005f, 0, 0, "%.3f")) {
            double new_t = t0 + t_start;
            double max_t = r.concentric.t_end_s - 0.05;
            if (new_t < max_t) r.concentric.t_start_s = new_t;
            session_->recompute_rep_metrics(i);
            session_->mark_reps_dirty();
        }

        ImGui::TableNextColumn();
        float t_end = (float)(r.rest.t_end_s - t0);
        if (ImGui::DragFloat("##te", &t_end, 0.005f, 0, 0, "%.3f")) {
            double new_t = t0 + t_end;
            double min_t = r.rest.t_start_s + 0.001;
            if (new_t > min_t) r.rest.t_end_s = new_t;
            session_->recompute_rep_metrics(i);
            session_->mark_reps_dirty();
        }

        ImGui::TableNextColumn();
        float cdur = (float)(r.concentric.t_end_s - r.concentric.t_start_s);
        ImGui::Text("%.3f", cdur);

        ImGui::TableNextColumn();
        float edur = (float)(r.eccentric.t_end_s - r.eccentric.t_start_s);
        ImGui::Text("%.3f", edur);

        ImGui::TableNextColumn();
        ImGui::Text("%.3f", r.peak_concentric_velocity);

        ImGui::TableNextColumn();
        ImGui::Text("%.3f", r.mean_concentric_velocity);

        ImGui::TableNextColumn();
        ImGui::Text("%.0f", r.rom_m * 1000);

        ImGui::TableNextColumn();
        ImGui::TextDisabled("%s", r.concentric.source.c_str());

        ImGui::PopID();
    }
    ImGui::EndTable();
}

} // namespace vbt

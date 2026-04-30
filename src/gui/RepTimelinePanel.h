#pragma once

/**
 * @file RepTimelinePanel.h
 * @brief Horizontal timeline view of all reps with click-to-edit + bulk ops.
 *
 * Each rep is one row: concentric (green) and eccentric (orange) bars on a
 * shared time axis. Click a rep to open inline edit; right-click for the
 * context menu (delete, mark good/bad, set form rating, plausibility status).
 */

#include "app/Application.h"
#include "core/Session.h"
#include "processing/Validator.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <algorithm>
#include <cstdio>

namespace vbt {

class RepTimelinePanel {
public:
    explicit RepTimelinePanel(Application& app) : app_(app) {}

    void render_content() {
        auto& session = app_.session();
        auto& seg = session.segmenter();
        const auto& reps = seg.get_reps();

        if (reps.empty()) {
            ImGui::TextDisabled("No reps yet — start recording and move the bar.");
            return;
        }

        // Header with bulk operations
        ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1), "%d reps", (int)reps.size());
        ImGui::SameLine();
        if (ImGui::SmallButton("Drop short reps")) {
            int dropped = 0;
            // Iterate by id; we collect ids first to avoid invalidation
            std::vector<int> ids;
            for (const auto& r : reps) {
                if (r.rom_m < app_.config().plausibility.min_rom_m) ids.push_back(r.rep_id);
            }
            for (int id : ids) { seg.delete_rep(id); dropped++; }
            if (dropped) Notifications::get().info("Dropped " + std::to_string(dropped) + " short reps");
        }
        ImGui::SameLine();
        if (ImGui::SmallButton("Drop implausible")) {
            int dropped = 0;
            std::vector<int> ids;
            for (const auto& r : reps) {
                auto pr = validate_rep_plausibility(r, app_.config().plausibility);
                if (!pr.passed) ids.push_back(r.rep_id);
            }
            for (int id : ids) { seg.delete_rep(id); dropped++; }
            if (dropped) Notifications::get().info("Dropped " + std::to_string(dropped) + " implausible reps");
        }

        ImGui::Separator();

        // Compute time bounds
        double t_min = reps.front().concentric.t_start_s;
        double t_max = reps.back().eccentric.t_end_s;
        if (t_max <= t_min) t_max = t_min + 1.0;
        double span = t_max - t_min;

        ImVec2 origin = ImGui::GetCursorScreenPos();
        float avail_w = ImGui::GetContentRegionAvail().x - 4.0f;
        float row_h = 28.0f;
        float total_h = row_h * (reps.size() + 0.5f) + 24.0f;
        if (total_h > 320.0f) total_h = 320.0f;
        ImGui::BeginChild("##timeline", ImVec2(avail_w, total_h), true,
                          ImGuiWindowFlags_HorizontalScrollbar);

        ImDrawList* dl = ImGui::GetWindowDrawList();
        ImVec2 area_origin = ImGui::GetCursorScreenPos();
        float chart_w = avail_w - 16.0f;

        // X-axis ruler
        char buf[32];
        for (int s = 0; s <= (int)span + 1; s += std::max(1, (int)(span / 10))) {
            float x = area_origin.x + (s / span) * chart_w;
            dl->AddLine(ImVec2(x, area_origin.y), ImVec2(x, area_origin.y + 8),
                        IM_COL32(140,140,160,255), 1.0f);
            std::snprintf(buf, sizeof(buf), "%ds", s);
            dl->AddText(ImVec2(x + 2, area_origin.y), IM_COL32(160,160,180,255), buf);
        }

        for (size_t i = 0; i < reps.size(); ++i) {
            const auto& r = reps[i];
            float y = area_origin.y + 18.0f + (float)i * row_h;
            float cx0 = area_origin.x + (float)((r.concentric.t_start_s - t_min) / span) * chart_w;
            float cx1 = area_origin.x + (float)((r.concentric.t_end_s   - t_min) / span) * chart_w;
            float ex0 = area_origin.x + (float)((r.eccentric.t_start_s - t_min) / span) * chart_w;
            float ex1 = area_origin.x + (float)((r.eccentric.t_end_s   - t_min) / span) * chart_w;

            // Concentric bar
            dl->AddRectFilled(ImVec2(cx0, y), ImVec2(cx1, y + row_h - 6),
                              IM_COL32(60, 200, 90, 220), 4.0f);
            // Eccentric bar
            dl->AddRectFilled(ImVec2(ex0, y), ImVec2(ex1, y + row_h - 6),
                              IM_COL32(220, 130, 70, 200), 4.0f);

            // Plausibility indicator on the right
            auto pr = validate_rep_plausibility(r, app_.config().plausibility);
            ImU32 dot = pr.passed ? IM_COL32(80,200,90,255) : IM_COL32(220,80,80,255);
            dl->AddCircleFilled(ImVec2(area_origin.x + chart_w + 6, y + (row_h - 6) * 0.5f),
                                4.0f, dot);

            // Rep label
            std::snprintf(buf, sizeof(buf), "#%d  V%.2f  ROM%.0fcm",
                r.rep_id, r.peak_concentric_velocity, r.rom_m * 100.0f);
            dl->AddText(ImVec2(cx0 + 4, y + 2), IM_COL32(0,0,0,255), buf);

            // Hover tooltip on concentric bar
            ImVec2 m = ImGui::GetIO().MousePos;
            if (m.x >= cx0 && m.x <= cx1 && m.y >= y && m.y <= y + row_h) {
                ImGui::BeginTooltip();
                ImGui::Text("Rep #%d", r.rep_id);
                ImGui::Text("Mean V: %.3f m/s", r.mean_concentric_velocity);
                ImGui::Text("Peak V: %.3f m/s", r.peak_concentric_velocity);
                ImGui::Text("ROM: %.0f cm",     r.rom_m * 100.0f);
                ImGui::Text("Source: %s",       r.concentric.source.c_str());
                if (!pr.passed) {
                    ImGui::Separator();
                    ImGui::TextColored(ImVec4(1,0.4f,0.3f,1), "Plausibility issues:");
                    for (const auto& f : pr.failures) ImGui::BulletText("%s", f.c_str());
                }
                ImGui::EndTooltip();
                if (ImGui::IsMouseClicked(ImGuiMouseButton_Right)) {
                    selected_rep_id_ = r.rep_id;
                    ImGui::OpenPopup("rep_ctx");
                }
            }
        }

        if (ImGui::BeginPopup("rep_ctx")) {
            ImGui::Text("Rep #%d", selected_rep_id_);
            ImGui::Separator();
            if (ImGui::MenuItem("Delete")) {
                seg.delete_rep(selected_rep_id_);
                Notifications::get().info("Deleted rep #" + std::to_string(selected_rep_id_));
            }
            ImGui::EndPopup();
        }

        ImGui::EndChild();
    }

private:
    Application& app_;
    int selected_rep_id_ = -1;
};

} // namespace vbt

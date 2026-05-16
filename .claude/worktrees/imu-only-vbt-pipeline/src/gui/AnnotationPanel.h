#pragma once
#include "app/Application.h"
#include <imgui.h>

namespace vbt {

class AnnotationPanel {
public:
    explicit AnnotationPanel(Application& app) : app_(app) {}

    void render() {
        ImGui::Begin("Rep Annotations");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        auto& seg = session.segmenter();
        const auto& reps = seg.get_reps();
        int current_count = (int)reps.size();

        if (reps.empty()) {
            ImGui::TextDisabled("No reps detected yet.");
            ImGui::TextDisabled("Connect IMU -> Create Set -> Start Set");
            ImGui::TextDisabled("Then move the barbell to detect reps.");
            return;
        }

        // Summary header
        ImGui::TextColored(ImVec4(0.4f, 0.8f, 1.0f, 1), "%d reps detected", current_count);
        if (current_count >= 2) {
            float first_v = reps[0].peak_concentric_velocity;
            float last_v = reps.back().peak_concentric_velocity;
            if (first_v > 0.01f) {
                float vel_loss = (1.0f - last_v / first_v) * 100.0f;
                ImGui::SameLine();
                ImVec4 loss_c = vel_loss < 10 ? ImVec4(0.3f,1,0.3f,1) :
                                vel_loss < 20 ? ImVec4(1,0.8f,0.2f,1) :
                                                ImVec4(1,0.3f,0.2f,1);
                ImGui::TextColored(loss_c, "  Vel Loss: %.0f%%", vel_loss);
            }
        }

        ImGui::Separator();

        // Rep table with auto-scroll
        float table_h = ImGui::GetContentRegionAvail().y - 40;
        if (table_h < 80) table_h = 80;

        if (ImGui::BeginTable("##RepsTable", 7,
                ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg |
                ImGuiTableFlags_Resizable | ImGuiTableFlags_ScrollY |
                ImGuiTableFlags_SizingStretchProp,
                ImVec2(0, table_h))) {

            ImGui::TableSetupColumn("Rep",  ImGuiTableColumnFlags_WidthFixed, 35);
            ImGui::TableSetupColumn("Phase", ImGuiTableColumnFlags_WidthFixed, 80);
            ImGui::TableSetupColumn("V pk",    ImGuiTableColumnFlags_WidthFixed, 60);
            ImGui::TableSetupColumn("V avg",   ImGuiTableColumnFlags_WidthFixed, 60);
            ImGui::TableSetupColumn("ROM",     ImGuiTableColumnFlags_WidthFixed, 55);
            ImGui::TableSetupColumn("Dur",     ImGuiTableColumnFlags_WidthFixed, 50);
            ImGui::TableSetupColumn("Src",     ImGuiTableColumnFlags_WidthFixed, 55);
            ImGui::TableSetupScrollFreeze(0, 1);
            ImGui::TableHeadersRow();

            for (int i = 0; i < (int)reps.size(); i++) {
                const auto& r = reps[i];
                bool is_latest = (i == (int)reps.size() - 1);

                ImGui::TableNextRow();

                // Highlight latest rep
                if (is_latest) {
                    ImGui::TableSetBgColor(ImGuiTableBgTarget_RowBg0,
                        ImGui::ColorConvertFloat4ToU32(ImVec4(0.15f, 0.25f, 0.4f, 0.5f)));
                }

                ImGui::TableNextColumn();
                ImGui::Text("%d", r.rep_id);

                ImGui::TableNextColumn();
                float con_dur = (float)(r.concentric.t_end_s - r.concentric.t_start_s);
                float ecc_dur = (float)(r.eccentric.t_end_s - r.eccentric.t_start_s);
                ImGui::TextColored(ImVec4(0.3f,0.9f,0.4f,1), "C:%.1fs", con_dur);
                ImGui::SameLine();
                ImGui::TextColored(ImVec4(1,0.5f,0.3f,1), "E:%.1fs", ecc_dur);

                ImGui::TableNextColumn();
                // Color-code peak velocity
                ImVec4 vc = r.peak_concentric_velocity > 0.8f ? ImVec4(0.3f,1,0.3f,1) :
                            r.peak_concentric_velocity > 0.5f ? ImVec4(1,0.8f,0.2f,1) :
                            r.peak_concentric_velocity > 0.2f ? ImVec4(1,0.5f,0.2f,1) :
                                                                ImVec4(1,0.3f,0.2f,1);
                ImGui::TextColored(vc, "%.2f", r.peak_concentric_velocity);

                ImGui::TableNextColumn();
                ImGui::Text("%.2f", r.mean_concentric_velocity);

                ImGui::TableNextColumn();
                ImGui::Text("%.0fcm", r.rom_m * 100.0f);

                ImGui::TableNextColumn();
                float total_dur = con_dur + ecc_dur;
                ImGui::Text("%.1fs", total_dur);

                ImGui::TableNextColumn();
                if (r.concentric.source == "manual") {
                    ImGui::TextColored(ImVec4(1, 0.8f, 0.2f, 1), "Manual");
                } else if (r.concentric.source == "camera") {
                    ImGui::TextColored(ImVec4(0.3f, 0.8f, 1, 1), "Cam");
                } else {
                    ImGui::TextColored(ImVec4(0.8f, 0.6f, 1, 1), "IMU");
                }
            }

            // Auto-scroll to bottom when new reps appear
            if (current_count > last_rep_count_) {
                ImGui::SetScrollHereY(1.0f);
                last_rep_count_ = current_count;
            }

            ImGui::EndTable();
        }

        // Bottom buttons
        ImGui::Spacing();
        if (ImGui::Button("Accept All", ImVec2(100, 0))) { /* Accept auto */ }
        ImGui::SameLine();
        if (session.get_state() == SessionState::STOPPED ||
            session.get_state() == SessionState::SAVED) {
            if (ImGui::Button("Save Annotations", ImVec2(-1, 0))) {
                seg.save(session.get_session_dir() + "/annotations/rep_segments.json");
            }
        }
    }

private:
    Application& app_;
    int last_rep_count_ = 0;
};

} // namespace vbt

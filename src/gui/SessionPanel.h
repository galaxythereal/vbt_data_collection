#pragma once
#include "app/Application.h"
#include <imgui.h>
#include <string>

namespace vbt {

class SessionPanel {
public:
    explicit SessionPanel(Application& app) : app_(app) {}

    void render() {
        ImGui::Begin("Session Control");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        auto state = session.get_state();

        // ── Session Info ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Session Info");
        ImGui::PopStyleColor();

        ImGui::InputText("Subject", subject_id_, sizeof(subject_id_));

        const char* exercises[] = {"back_squat", "front_squat", "bench_press", "deadlift",
                                    "overhead_press", "barbell_row", "clean", "snatch", "other"};
        ImGui::Combo("Exercise", &exercise_idx_, exercises, IM_ARRAYSIZE(exercises));

        ImGui::InputFloat("Bar (kg)", &barbell_weight_, 0.5f, 5.0f, "%.1f");
        ImGui::InputFloat("Added (kg)", &added_weight_, 0.5f, 5.0f, "%.1f");
        float total = barbell_weight_ + added_weight_;
        ImGui::TextColored(ImVec4(0.4f, 0.8f, 1, 1), "Total: %.1f kg", total);

        ImGui::InputInt("Target Reps", &target_reps_);
        ImGui::InputInt("Set #", &set_number_);

        // RPE slider with color
        ImGui::SliderInt("RPE", &rpe_, 1, 10);
        ImGui::SameLine();
        ImVec4 rpe_c = rpe_ <= 6 ? ImVec4(0.3f,0.9f,0.3f,1) :
                        rpe_ <= 8 ? ImVec4(1,0.8f,0.2f,1) :
                                    ImVec4(1,0.3f,0.2f,1);
        ImGui::TextColored(rpe_c, "%d", rpe_);

        ImGui::InputTextMultiline("Notes", notes_, sizeof(notes_), ImVec2(-1, 45));

        ImGui::Spacing();

        // ── Recording Control ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Recording");
        ImGui::PopStyleColor();

        if (state == SessionState::IDLE || state == SessionState::SAVED) {
            if (ImGui::Button("Create Session", ImVec2(-1, 32))) {
                SessionInfo info;
                info.subject_id = subject_id_;
                info.exercise = exercises[exercise_idx_];
                info.barbell_weight_kg = barbell_weight_;
                info.added_weight_kg = added_weight_;
                info.total_weight_kg = total;
                info.target_reps = target_reps_;
                info.set_number = set_number_;
                info.rpe = rpe_;
                info.notes = notes_;
                session.create(app_.config().dataset_root, info);
            }
        }

        if (state == SessionState::CONFIGURED || state == SessionState::READY) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.15f, 0.55f, 0.20f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.20f, 0.70f, 0.25f, 1.0f));
            if (ImGui::Button("START RECORDING", ImVec2(-1, 42))) {
                session.start_recording();
            }
            ImGui::PopStyleColor(2);
        }

        if (state == SessionState::RECORDING) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.70f, 0.15f, 0.15f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.85f, 0.20f, 0.20f, 1.0f));
            if (ImGui::Button("STOP RECORDING", ImVec2(-1, 42))) {
                session.stop_recording();
            }
            ImGui::PopStyleColor(2);

            auto stats = session.get_recording_stats();
            ImGui::Spacing();
            ImGui::Text("Duration: %.1f s", stats.duration_s);
            ImGui::Text("IMU: %lu samples", stats.imu_samples);
            ImGui::Text("Camera: %lu frames", stats.camera_frames);
            ImGui::Text("Reps: %d", stats.rep_count);

            // Progress bar
            if (target_reps_ > 0) {
                float prog = (float)stats.rep_count / (float)target_reps_;
                char overlay[32];
                snprintf(overlay, sizeof(overlay), "%d / %d reps", stats.rep_count, target_reps_);
                ImGui::ProgressBar(prog, ImVec2(-1, 0), overlay);
            }
        }

        if (state == SessionState::STOPPED) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.20f, 0.35f, 0.65f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.45f, 0.80f, 1.0f));
            if (ImGui::Button("SAVE SESSION", ImVec2(-1, 36))) {
                session.save();
            }
            ImGui::PopStyleColor(2);

            ImGui::SameLine();
        }
    }

private:
    Application& app_;
    char subject_id_[64] = "S01";
    int exercise_idx_ = 0;
    float barbell_weight_ = 20.0f;
    float added_weight_ = 0.0f;
    int target_reps_ = 5;
    int set_number_ = 1;
    int rpe_ = 7;
    char notes_[256] = "";
};

} // namespace vbt

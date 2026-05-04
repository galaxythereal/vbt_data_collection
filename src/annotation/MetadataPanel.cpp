/**
 * @file MetadataPanel.cpp
 */
#include "annotation/MetadataPanel.h"
#include <cstring>

namespace vbt {

namespace {
// String input that resizes the underlying std::string when the user types.
bool input_string(const char* label, std::string& s, int max = 256) {
    char buf[1024];
    std::strncpy(buf, s.c_str(), sizeof(buf));
    buf[sizeof(buf) - 1] = 0;
    if (ImGui::InputText(label, buf, std::min<int>(max, (int)sizeof(buf)))) {
        s = buf;
        return true;
    }
    return false;
}
} // namespace

void MetadataPanel::render() {
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("Load a session to edit its metadata.");
        return;
    }
    if (session_->meta_dirty()) {
        ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f),
                           "● Unsaved metadata changes — Ctrl+S to commit");
        ImGui::Separator();
    }

    if (ImGui::CollapsingHeader("Subject & session", ImGuiTreeNodeFlags_DefaultOpen))
        render_subject_block_();
    if (ImGui::CollapsingHeader("Loading & exercise", ImGuiTreeNodeFlags_DefaultOpen))
        render_loading_block_();
    if (ImGui::CollapsingHeader("Technique"))
        render_technique_block_();
    if (ImGui::CollapsingHeader("Conditions"))
        render_conditions_block_();
    if (ImGui::CollapsingHeader("Provenance (read-only)"))
        render_provenance_block_();
    if (ImGui::CollapsingHeader("Pre-flight overrides (read-only)"))
        render_overrides_block_();
}

void MetadataPanel::render_subject_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    ed |= input_string("Subject ID",   s.subject_id);
    ed |= input_string("Operator ID",  s.operator_id);
    ed |= input_string("Session date", s.date);
    const char* tods[] = {"morning", "afternoon", "evening", ""};
    int cur = 3;
    for (int i = 0; i < 4; ++i) if (s.time_of_day == tods[i]) cur = i;
    if (ImGui::Combo("Time of day", &cur, "morning\0afternoon\0evening\0(unspecified)\0")) {
        s.time_of_day = tods[cur];
        ed = true;
    }
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_loading_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    ed |= input_string("Exercise",         s.exercise);
    ed |= input_string("Variant",          s.exercise_variant);
    ed |= input_string("Equipment",        s.equipment);
    ed |= ImGui::DragFloat("Bar mass (kg)",     &s.barbell_weight_kg, 0.1f);
    ed |= ImGui::DragFloat("Added weight (kg)", &s.added_weight_kg, 0.5f);
    s.total_weight_kg = s.barbell_weight_kg + s.added_weight_kg;
    ImGui::Text("Total: %.1f kg", s.total_weight_kg);
    ed |= ImGui::DragFloat("%% 1RM", &s.percent_1rm, 0.5f, 0, 110);
    ed |= ImGui::DragInt("Set #",       &s.set_number, 1, 1, 50);
    ed |= ImGui::DragInt("Total sets",  &s.total_sets_planned, 1, 1, 50);
    ed |= ImGui::DragInt("Target reps", &s.target_reps, 1, 1, 30);
    ed |= ImGui::SliderInt("RPE",       &s.rpe, 0, 10);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_technique_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    const char* dcs[] = {"parallel", "below_parallel", "atg", "lockout"};
    int cur = 0;
    for (int i = 0; i < 4; ++i) if (s.depth_criterion == dcs[i]) cur = i;
    if (ImGui::Combo("Depth criterion", &cur, "parallel\0below_parallel\0atg\0lockout\0")) {
        s.depth_criterion = dcs[cur]; ed = true;
    }
    ed |= input_string("Tempo prescription", s.tempo_prescription);
    ed |= ImGui::DragFloat("Rest prescription (s)", &s.rest_prescription_s,
                            1.0f, 0, 600);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_conditions_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    ed |= input_string("Location", s.location);
    ed |= ImGui::DragFloat("Temperature (°C)", &s.temperature_c, 0.5f);
    ed |= ImGui::DragFloat("Humidity (%)",     &s.humidity_pct, 1.0f, 0, 100);
    ed |= ImGui::SliderInt("Freshness (1–5)",  &s.freshness_1to5, 1, 5);
    const char* ws[] = {"yes", "no", "partial"};
    int cur = 0;
    for (int i = 0; i < 3; ++i) if (s.warmup_completed == ws[i]) cur = i;
    if (ImGui::Combo("Warmup completed", &cur, "yes\0no\0partial\0")) {
        s.warmup_completed = ws[cur]; ed = true;
    }
    ImGui::TextDisabled("Notes");
    char buf[2048];
    std::strncpy(buf, s.notes.c_str(), sizeof(buf));
    buf[sizeof(buf) - 1] = 0;
    if (ImGui::InputTextMultiline("##notes", buf, sizeof(buf), ImVec2(-1, 80))) {
        s.notes = buf; ed = true;
    }
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_provenance_block_() {
    const auto& s = session_->info();
    ImGui::TextDisabled("Build");
    ImGui::Text("  Version : %s", s.build.app_version.c_str());
    ImGui::Text("  Git SHA : %s (%s)  dirty=%s",
                 s.build.git_sha.c_str(),
                 s.build.git_branch.c_str(),
                 s.build.git_dirty.c_str());
    ImGui::Text("  Built   : %s   (%s, %s)",
                 s.build.build_timestamp.c_str(),
                 s.build.build_type.c_str(),
                 s.build.compiler.c_str());
    ImGui::Spacing();
    ImGui::TextDisabled("Calibration");
    ImGui::Text("  accel_calib       : %s",
                 s.calibration.accel_calib_path.empty()
                 ? "<none>" : s.calibration.accel_calib_path.c_str());
    ImGui::Text("  gyro bias (dps)   : (%.4f, %.4f, %.4f)",
                 s.calibration.gyro_bias_x_dps,
                 s.calibration.gyro_bias_y_dps,
                 s.calibration.gyro_bias_z_dps);
    ImGui::Text("  sync drift (ppm)  : %.2f",
                 s.calibration.sync_drift_ppm);
}

void MetadataPanel::render_overrides_block_() {
    const auto& s = session_->info();
    if (s.preflight_overrides.empty()) {
        ImGui::TextDisabled("(no overrides — pre-flight passed)");
        return;
    }
    for (const auto& o : s.preflight_overrides) {
        ImGui::BulletText("%s", o.c_str());
    }
}

} // namespace vbt

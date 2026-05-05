/**
 * @file MetadataPanel.cpp
 *
 * Full PhD-grade SessionInfo editor. Sections are individually
 * collapsible (so the operator can land on what's missing for the
 * dataset row) and an "Expand all" toggle opens everything at once
 * for end-of-session completion.
 */
#include "annotation/MetadataPanel.h"
#include <cstring>
#include <algorithm>

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

// Multiline notes input.
bool input_text_multiline(const char* label, std::string& s, ImVec2 sz) {
    char buf[2048];
    std::strncpy(buf, s.c_str(), sizeof(buf));
    buf[sizeof(buf) - 1] = 0;
    if (ImGui::InputTextMultiline(label, buf, sizeof(buf), sz)) {
        s = buf;
        return true;
    }
    return false;
}

// Combo bound to a std::string — accepts a list of options as a flat
// array, returns true if the user picked something different.
bool combo_string(const char* label, std::string& s,
                   const char* const* opts, int n) {
    int cur = 0;
    for (int i = 0; i < n; ++i) if (s == opts[i]) { cur = i; break; }
    bool ed = false;
    if (ImGui::BeginCombo(label, opts[cur])) {
        for (int i = 0; i < n; ++i) {
            bool sel = (i == cur);
            if (ImGui::Selectable(opts[i], sel)) { s = opts[i]; ed = true; }
            if (sel) ImGui::SetItemDefaultFocus();
        }
        ImGui::EndCombo();
    }
    return ed;
}

// Light-weight section header — visually separates blocks within a
// collapsing section (used in the heavier sub-snapshot blocks).
void minor_header(const char* text) {
    ImGui::Spacing();
    ImGui::TextColored(ImVec4(0.62f, 0.74f, 0.92f, 1.0f), "%s", text);
    ImGui::Separator();
}

ImGuiTreeNodeFlags hdr_flags(bool expand_all, bool default_open = false) {
    ImGuiTreeNodeFlags f = 0;
    if (expand_all || default_open) f |= ImGuiTreeNodeFlags_DefaultOpen;
    return f;
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

    ImGui::Checkbox("Expand all sections", &expand_all_);
    ImGui::SameLine();
    ImGui::TextDisabled("(forces every block open)");

    if (ImGui::CollapsingHeader("Subject & session", hdr_flags(expand_all_, true)))
        render_subject_block_();
    if (ImGui::CollapsingHeader("Subject — day-of snapshot", hdr_flags(expand_all_, true)))
        render_subject_day_snapshot_();
    if (ImGui::CollapsingHeader("Loading & exercise", hdr_flags(expand_all_, true)))
        render_loading_block_();
    if (ImGui::CollapsingHeader("Load provenance (plates, collars)", hdr_flags(expand_all_)))
        render_load_provenance_();
    if (ImGui::CollapsingHeader("Technique", hdr_flags(expand_all_)))
        render_technique_block_();
    if (ImGui::CollapsingHeader("Training context (block / set sequence)", hdr_flags(expand_all_)))
        render_training_context_();
    if (ImGui::CollapsingHeader("Gear & implements", hdr_flags(expand_all_)))
        render_gear_block_();
    if (ImGui::CollapsingHeader("Safety setup", hdr_flags(expand_all_)))
        render_safety_block_();
    if (ImGui::CollapsingHeader("Environment", hdr_flags(expand_all_)))
        render_environment_block_();
    if (ImGui::CollapsingHeader("Subjective quality", hdr_flags(expand_all_)))
        render_quality_block_();
    if (ImGui::CollapsingHeader("Conditions & notes", hdr_flags(expand_all_)))
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
    static const char* tods[] = {"morning", "afternoon", "evening", ""};
    ed |= combo_string("Time of day", s.time_of_day, tods, 4);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_subject_day_snapshot_() {
    auto& s = session_->mutable_info().subject_snapshot;
    bool ed = false;

    minor_header("Anthropometrics & training history");
    static const char* sexes[] = {"male", "female", "other", "decline", "unspecified"};
    ed |= combo_string("Sex", s.sex, sexes, 5);
    ed |= ImGui::DragInt("Age (years)",                    &s.age_years, 1, 0, 100);
    ed |= ImGui::DragFloat("Body mass (kg)",               &s.body_mass_kg, 0.1f, 0, 250);
    ed |= ImGui::DragFloat("Height (cm)",                  &s.height_cm,  0.5f, 0, 240);
    ed |= ImGui::DragInt("Training experience (years)",    &s.training_experience_years, 1, 0, 60);
    static const char* sides[] = {"left", "right", "ambidextrous"};
    ed |= combo_string("Dominant side", s.dominant_side, sides, 3);
    ed |= ImGui::DragFloat("Baseline 1RM for this lift (kg)", &s.baseline_1rm_kg, 1.0f, 0, 500);

    minor_header("Acute readiness");
    ed |= ImGui::DragFloat("Sleep last night (h)",         &s.sleep_hours_last_night, 0.25f, 0, 16);
    ed |= ImGui::SliderInt("Sleep quality (1–5)",          &s.sleep_quality_1to5, 1, 5);
    ed |= ImGui::SliderInt("Stress level (1–5)",           &s.stress_level_1to5, 1, 5);
    ed |= ImGui::SliderInt("Motivation (1–5)",             &s.motivation_1to5, 1, 5);
    ed |= ImGui::SliderInt("Soreness (1–10)",              &s.soreness_1to10, 0, 10);
    ed |= input_string("Soreness locations",               s.soreness_locations);
    ed |= ImGui::SliderInt("Fatigue (1–5)",                &s.fatigue_1to5, 1, 5);

    minor_header("Nutrition & stimulants");
    ed |= ImGui::DragFloat("Caffeine (mg)",                &s.caffeine_mg, 5.0f, 0, 2000);
    ed |= ImGui::DragInt("Last meal (min ago)",            &s.last_meal_minutes_ago, 5, 0, 1440);
    ed |= ImGui::DragFloat("Hydration today (ml)",         &s.hydration_ml_today, 50.0f, 0, 10000);
    ed |= ImGui::Checkbox("Pre-workout taken",             &s.pre_workout_taken);
    if (s.pre_workout_taken)
        ed |= input_string("Pre-workout brand",            s.pre_workout_brand);

    minor_header("Health");
    static const char* injuries[] = {"none", "minor", "managed", "major"};
    ed |= combo_string("Injury status", s.injury_status, injuries, 4);
    if (s.injury_status != "none")
        ed |= input_string("Injury notes",                 s.injury_notes);
    ed |= input_string("Medication status",                s.medication_status);
    static const char* phases[] = {"n/a", "decline", "follicular", "ovulatory", "luteal", "menstrual"};
    ed |= combo_string("Menstrual phase (optional)", s.menstrual_phase, phases, 6);

    minor_header("Consent & governance");
    ed |= input_string("Consent version",                  s.consent_version);
    ed |= input_string("Ethics protocol",                  s.ethics_protocol);
    static const char* tiers[] = {"public", "restricted", "lab_only"};
    ed |= combo_string("Data sharing tier", s.data_sharing_tier, tiers, 3);

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

void MetadataPanel::render_load_provenance_() {
    auto& s = session_->mutable_info().load_provenance;
    bool ed = false;
    ImGui::TextDisabled("Plate breakdown (per side, kg) — list 'em heavy → light");
    int erase = -1;
    for (int i = 0; i < (int)s.plates_per_side_kg.size(); ++i) {
        ImGui::PushID(i);
        float v = s.plates_per_side_kg[i];
        if (ImGui::DragFloat("##plate", &v, 0.5f, 0, 50, "%.2f kg")) {
            s.plates_per_side_kg[i] = v; ed = true;
        }
        ImGui::SameLine();
        if (ImGui::SmallButton("×")) erase = i;
        ImGui::PopID();
    }
    if (erase >= 0) {
        s.plates_per_side_kg.erase(s.plates_per_side_kg.begin() + erase);
        ed = true;
    }
    if (ImGui::SmallButton("+ add plate")) {
        s.plates_per_side_kg.push_back(20.0f);
        ed = true;
    }
    ImGui::SameLine();
    if (!s.plates_per_side_kg.empty()) {
        float sum_per_side = 0;
        for (float p : s.plates_per_side_kg) sum_per_side += p;
        ImGui::TextDisabled("(2× per-side = %.1f kg)", 2 * sum_per_side);
    }
    ed |= ImGui::DragFloat("Collar mass (kg, each)",  &s.collar_mass_kg_each, 0.1f, 0, 5);
    ed |= ImGui::Checkbox("Plates calibrated against scale", &s.plates_calibrated);
    static const char* methods[] = {"unknown", "manufacturer", "scale_verified"};
    ed |= combo_string("Calibration method", s.plate_calibration_method, methods, 3);
    ed |= ImGui::DragFloat("Bar mass measured (kg, 0=default)", &s.bar_mass_measured_kg,
                            0.1f, 0, 35);
    ed |= ImGui::Checkbox("Asymmetric loading",       &s.asymmetric_loading);
    ed |= input_string("Loading notes",                s.loading_notes);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_technique_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    static const char* dcs[] = {"parallel", "below_parallel", "atg", "lockout"};
    ed |= combo_string("Depth criterion", s.depth_criterion, dcs, 4);
    ed |= input_string("Tempo prescription", s.tempo_prescription);
    ed |= ImGui::DragFloat("Rest prescription (s)", &s.rest_prescription_s, 1.0f, 0, 600);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_training_context_() {
    auto& s = session_->mutable_info().training_context;
    bool ed = false;
    static const char* goals[] = {
        "strength", "hypertrophy", "power", "endurance", "peaking", "deload"
    };
    ed |= combo_string("Goal", s.goal, goals, 6);
    static const char* phases[] = {
        "accumulation", "intensification", "realization", "deload", "test"
    };
    ed |= combo_string("Block phase", s.block_phase, phases, 5);
    ed |= ImGui::DragInt("Mesocycle week",   &s.mesocycle_week, 1, 1, 12);
    ed |= ImGui::DragInt("Microcycle day",   &s.microcycle_day, 1, 1, 14);
    ed |= ImGui::DragInt("Days since last session",       &s.days_since_last_session, 1, 0, 30);
    ed |= ImGui::DragInt("Days since last (this lift)",   &s.days_since_last_session_same_lift, 1, 0, 90);
    ed |= ImGui::DragInt("Working sets done today (prior to this set)",
                         &s.working_sets_completed_today, 1, 0, 50);
    ed |= ImGui::DragInt("Warm-up sets done today",
                         &s.warmup_sets_completed_today, 1, 0, 20);
    ed |= ImGui::DragFloat("Prior session volume (kg, this lift)",
                            &s.prior_session_volume_kg, 50.0f, 0, 100000);

    minor_header("Set type flags");
    ed |= ImGui::Checkbox("To failure",  &s.to_failure);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Drop set",   &s.drop_set);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Cluster",    &s.cluster_set);
    ed |= ImGui::Checkbox("Pause",       &s.pause_set);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Tempo",      &s.tempo_set);

    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_gear_block_() {
    auto& s = session_->mutable_info().gear;
    bool ed = false;
    ed |= ImGui::Checkbox("Belt",          &s.belt);
    if (s.belt) {
        ImGui::SameLine();
        static const char* bts[] = {"lever", "prong", "velcro"};
        ed |= combo_string("##belt_type", s.belt_type, bts, 3);
    }
    ed |= ImGui::Checkbox("Wrist wraps",   &s.wrist_wraps);
    ImGui::SameLine();
    ed |= ImGui::Checkbox("Knee sleeves",  &s.knee_sleeves);
    ed |= ImGui::Checkbox("Knee wraps",    &s.knee_wraps);
    ImGui::SameLine();
    ed |= ImGui::Checkbox("Lifting straps", &s.lifting_straps);
    ed |= ImGui::Checkbox("Chalk",         &s.chalk);
    ImGui::SameLine();
    ed |= ImGui::Checkbox("Lifting shoes", &s.lifting_shoes);
    if (s.lifting_shoes) {
        static const char* sts[] = {
            "flats", "heeled_lifters", "deadlift_slippers", "cross_trainers"
        };
        ed |= combo_string("Shoe type", s.shoe_type, sts, 4);
    }
    ed |= ImGui::Checkbox("Spotter present", &s.spotter_present);
    ImGui::SameLine();
    ed |= ImGui::Checkbox("Coach present",   &s.coach_present);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_safety_block_() {
    auto& s = session_->mutable_info().safety;
    bool ed = false;
    static const char* racks[] = {
        "squat_rack", "half_rack", "power_cage", "smith", "free", ""
    };
    ed |= combo_string("Rack type", s.rack_type, racks, 6);
    ed |= ImGui::Checkbox("Safety pins set",  &s.safety_pins_set);
    if (s.safety_pins_set)
        ed |= ImGui::DragFloat("Pin height (m)", &s.safety_pin_height_m, 0.01f, 0, 2.5f);
    ed |= ImGui::Checkbox("Safety arms used", &s.safety_arms_used);
    ImGui::SameLine();
    ed |= ImGui::Checkbox("Bumper plates",    &s.bumper_plates);
    ed |= ImGui::Checkbox("Platform used",    &s.platform_used);
    static const char* floors[] = {
        "rubber", "wood", "concrete", "dropped_lifting_platform", ""
    };
    ed |= combo_string("Flooring", s.flooring, floors, 5);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_environment_block_() {
    auto& s = session_->mutable_info().environment;
    bool ed = false;
    static const char* lights[] = {
        "indoor_fluorescent", "indoor_LED", "indoor_incandescent",
        "natural_daylight", "mixed", "low_light"
    };
    ed |= combo_string("Ambient lighting", s.ambient_lighting, lights, 6);
    ed |= ImGui::DragInt("Ambient lux (0=unmeasured)", &s.ambient_lux, 50, 0, 100000);
    ed |= ImGui::DragInt("Music BPM (0=none)",         &s.music_bpm, 1, 0, 220);
    ed |= ImGui::Checkbox("Distractions present",      &s.distractions_present);
    if (s.distractions_present)
        ed |= input_string("Distraction notes",         s.distraction_notes);
    ed |= ImGui::SliderInt("Gym busyness (1–5)",       &s.gym_busyness_1to5, 1, 5);
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_quality_block_() {
    auto& s = session_->mutable_info().quality;
    bool ed = false;
    static const char* scales[] = {"rpe_1to10", "rir", "borg_6to20"};
    ed |= combo_string("RPE scale", s.rpe_scale, scales, 3);
    ed |= ImGui::DragInt("Actual RIR",                &s.actual_rir, 1, 0, 15);
    ed |= ImGui::SliderInt("Form quality (1–5)",      &s.form_quality_1to5, 1, 5);
    ed |= ImGui::SliderInt("Felt difficulty (1–5)",   &s.felt_difficulty_1to5, 1, 5);
    ed |= ImGui::SliderInt("Confidence (1–5)",        &s.confidence_1to5, 1, 5);
    ed |= ImGui::SliderInt("Session quality (1–5)",   &s.session_quality_1to5, 1, 5);
    ImGui::TextDisabled("Technique breakdown notes");
    ed |= input_text_multiline("##tech_notes", s.technique_breakdown_notes,
                                ImVec2(-1, 60));
    ImGui::TextDisabled("Performance anomalies");
    ed |= input_text_multiline("##anom", s.performance_anomalies,
                                ImVec2(-1, 60));
    if (ed) session_->mark_meta_dirty();
}

void MetadataPanel::render_conditions_block_() {
    auto& s = session_->mutable_info();
    bool ed = false;
    ed |= input_string("Location", s.location);
    ed |= ImGui::DragFloat("Temperature (°C)", &s.temperature_c, 0.5f);
    ed |= ImGui::DragFloat("Humidity (%)",     &s.humidity_pct, 1.0f, 0, 100);
    ed |= ImGui::SliderInt("Freshness (1–5)",  &s.freshness_1to5, 1, 5);
    static const char* ws[] = {"yes", "no", "partial"};
    ed |= combo_string("Warmup completed", s.warmup_completed, ws, 3);
    ImGui::TextDisabled("Notes");
    ed |= input_text_multiline("##notes", s.notes, ImVec2(-1, 80));
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

/**
 * @file SessionInfoForm.cpp
 *
 * Implementation of the shared SessionInfo editor. Lifted (mostly
 * verbatim) from the studio's old MetadataPanel.cpp so that the same
 * widget set powers both pre-recording entry and post-recording
 * editing. No global / static state — each call mutates the input
 * reference in place.
 */
#include "gui/SessionInfoForm.h"
#include "utils/Uuid.h"
#include <algorithm>
#include <cstring>

namespace vbt::session_info_form {

namespace {

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

void minor_header(const char* text) {
    ImGui::Spacing();
    ImGui::TextColored(ImVec4(0.62f, 0.74f, 0.92f, 1.0f), "%s", text);
    ImGui::Separator();
}

} // namespace

bool render_subject_pinned(SessionInfo& s) {
    auto& snap = s.subject_snapshot;
    bool ed = false;

    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.78f, 1, 1));
    ImGui::TextUnformatted("Subject");
    ImGui::PopStyleColor();

    char ubuf[64];
    std::snprintf(ubuf, sizeof(ubuf), "%s", s.subject_uuid.c_str());
    ImGui::PushItemWidth(280);
    if (ImGui::InputText("UUID", ubuf, sizeof(ubuf))) {
        s.subject_uuid = ubuf; ed = true;
    }
    ImGui::PopItemWidth();
    ImGui::SameLine();
    if (ImGui::SmallButton("regen")) {
        s.subject_uuid = make_uuid_v4();
        ed = true;
    }
    ImGui::SameLine();
    if (ImGui::SmallButton("copy")) {
        ImGui::SetClipboardText(s.subject_uuid.c_str());
    }

    ed |= input_string("Name",       s.subject_name);
    ed |= input_string("Subject ID", s.subject_id);
    ImGui::TextDisabled("(UUID is the stable cross-session key. "
                         "Reuse the same UUID for the same person.)");

    static const char* sexes[] = {"male", "female", "other", "decline", "unspecified"};
    ed |= combo_string("Sex", snap.sex, sexes, 5);
    ed |= ImGui::DragInt("Age (years)",                    &snap.age_years, 1, 0, 100);
    ed |= ImGui::DragFloat("Body mass (kg)",               &snap.body_mass_kg, 0.1f, 0, 250);
    ed |= ImGui::DragFloat("Height (cm)",                  &snap.height_cm,  0.5f, 0, 240);
    static const char* sides[] = {"left", "right", "ambidextrous"};
    ed |= combo_string("Dominant side", snap.dominant_side, sides, 3);
    return ed;
}

bool render_subject_identity(SessionInfo& s) {
    bool ed = false;
    if (!s.session_id.empty())
        ImGui::TextDisabled("session_id : %s", s.session_id.c_str());
    ImGui::TextDisabled("schema     : v%d", s.schema_version);
    ed |= input_string("Operator ID",  s.operator_id);
    ed |= input_string("Session date", s.date);
    static const char* tods[] = {"morning", "afternoon", "evening", ""};
    ed |= combo_string("Time of day", s.time_of_day, tods, 4);
    return ed;
}

bool render_subject_day_snapshot(SubjectDaySnapshot& s) {
    bool ed = false;

    minor_header("Training history");
    ed |= ImGui::DragInt("Training experience (years)",    &s.training_experience_years, 1, 0, 60);
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
    return ed;
}

bool render_sets_block(SessionInfo& info, double t0_unified_s) {
    auto& sets = info.sets;
    bool ed = false;

    ImGui::TextDisabled("Each row is a working set within this session "
                         "(reps reference set_id). Sets are chronological.");

    int erase = -1;
    for (int i = 0; i < (int)sets.size(); ++i) {
        ImGui::PushID(i);
        auto& st = sets[i];
        char hdr[64];
        std::snprintf(hdr, sizeof(hdr),
                       "Set %d  ·  %.1f kg × %d reps",
                       st.set_id, st.total_weight_kg, st.target_reps);
        if (ImGui::TreeNodeEx(hdr,
            (i == 0 ? ImGuiTreeNodeFlags_DefaultOpen : 0))) {
            ed |= ImGui::DragInt("set_id", &st.set_id, 1, 1, 50);
            ed |= ImGui::DragFloat("Bar mass (kg)",     &st.barbell_weight_kg, 0.1f);
            ed |= ImGui::DragFloat("Added weight (kg)", &st.added_weight_kg, 0.5f);
            st.total_weight_kg = st.barbell_weight_kg + st.added_weight_kg;
            ImGui::Text("Total: %.1f kg", st.total_weight_kg);
            ed |= ImGui::DragFloat("%% 1RM", &st.percent_1rm, 0.5f, 0, 110);
            ed |= ImGui::DragInt("Target reps",    &st.target_reps, 1, 1, 30);
            ed |= ImGui::DragInt("Completed reps", &st.completed_reps, 1, 0, 30);
            ed |= ImGui::SliderInt("RPE",          &st.rpe, 0, 10);
            ed |= ImGui::DragInt("Actual RIR",     &st.actual_rir, 1, 0, 15);
            ed |= ImGui::Checkbox("To failure",  &st.to_failure);
            ImGui::SameLine(); ed |= ImGui::Checkbox("Drop",     &st.drop_set);
            ImGui::SameLine(); ed |= ImGui::Checkbox("Cluster",  &st.cluster_set);
            ed |= ImGui::Checkbox("Pause",       &st.pause_set);
            ImGui::SameLine(); ed |= ImGui::Checkbox("Tempo",    &st.tempo_set);
            ed |= input_string("Notes", st.notes);

            // Time range
            if (t0_unified_s > 0.0) {
                float ts = (float)(st.t_start_unified_s - t0_unified_s);
                float te = (float)(st.t_end_unified_s   - t0_unified_s);
                if (ImGui::DragFloat("t_start (s, rel)", &ts, 0.05f)) {
                    st.t_start_unified_s = t0_unified_s + ts; ed = true;
                }
                if (ImGui::DragFloat("t_end (s, rel)",   &te, 0.05f)) {
                    st.t_end_unified_s   = t0_unified_s + te; ed = true;
                }
            }
            if (ImGui::SmallButton("Delete this set")) erase = i;
            ImGui::TreePop();
        }
        ImGui::PopID();
    }

    if (erase >= 0) {
        sets.erase(sets.begin() + erase);
        ed = true;
    }
    if (ImGui::SmallButton("+ Add set")) {
        SetInfo s;
        s.set_id = sets.empty() ? 1 : sets.back().set_id + 1;
        if (!sets.empty()) {
            s.barbell_weight_kg = sets.back().barbell_weight_kg;
            s.added_weight_kg   = sets.back().added_weight_kg;
            s.total_weight_kg   = sets.back().total_weight_kg;
            s.percent_1rm       = sets.back().percent_1rm;
            s.target_reps       = sets.back().target_reps;
        }
        sets.push_back(s);
        ed = true;
    }
    return ed;
}

bool render_loading_block(SessionInfo& s) {
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
    return ed;
}

bool render_load_provenance(LoadProvenance& s) {
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
    return ed;
}

bool render_technique_block(SessionInfo& s) {
    bool ed = false;
    static const char* dcs[] = {"parallel", "below_parallel", "atg", "lockout"};
    ed |= combo_string("Depth criterion", s.depth_criterion, dcs, 4);
    ed |= input_string("Tempo prescription", s.tempo_prescription);
    ed |= ImGui::DragFloat("Rest prescription (s)", &s.rest_prescription_s, 1.0f, 0, 600);
    return ed;
}

bool render_training_context(TrainingContext& s) {
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
    ed |= ImGui::DragInt("Working sets done today",       &s.working_sets_completed_today, 1, 0, 50);
    ed |= ImGui::DragInt("Warm-up sets done today",       &s.warmup_sets_completed_today, 1, 0, 20);
    ed |= ImGui::DragFloat("Prior session volume (kg, this lift)",
                            &s.prior_session_volume_kg, 50.0f, 0, 100000);

    minor_header("Set type flags");
    ed |= ImGui::Checkbox("To failure",  &s.to_failure);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Drop set",   &s.drop_set);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Cluster",    &s.cluster_set);
    ed |= ImGui::Checkbox("Pause",       &s.pause_set);
    ImGui::SameLine(); ed |= ImGui::Checkbox("Tempo",      &s.tempo_set);
    return ed;
}

bool render_gear_block(GearAndImplements& s) {
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
    return ed;
}

bool render_safety_block(SafetySetup& s) {
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
    return ed;
}

bool render_environment_block(EnvironmentDetails& s) {
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
    return ed;
}

bool render_quality_block(SubjectiveQuality& s) {
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
    return ed;
}

bool render_conditions_block(SessionInfo& s) {
    bool ed = false;
    ed |= input_string("Location", s.location);
    ed |= ImGui::DragFloat("Temperature (°C)", &s.temperature_c, 0.5f);
    ed |= ImGui::DragFloat("Humidity (%)",     &s.humidity_pct, 1.0f, 0, 100);
    ed |= ImGui::SliderInt("Freshness (1–5)",  &s.freshness_1to5, 1, 5);
    static const char* ws[] = {"yes", "no", "partial"};
    ed |= combo_string("Warmup completed", s.warmup_completed, ws, 3);
    ImGui::TextDisabled("Notes");
    ed |= input_text_multiline("##notes", s.notes, ImVec2(-1, 80));
    return ed;
}

void render_provenance_block(const SessionInfo& s) {
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

void render_overrides_block(const SessionInfo& s) {
    if (s.preflight_overrides.empty()) {
        ImGui::TextDisabled("(no overrides — pre-flight passed)");
        return;
    }
    for (const auto& o : s.preflight_overrides) {
        ImGui::BulletText("%s", o.c_str());
    }
}

bool render_full_form(SessionInfo& s, double t0_unified_s, bool expand_all) {
    bool ed = false;
    auto hf = [expand_all](bool default_open) {
        return (expand_all || default_open) ? ImGuiTreeNodeFlags_DefaultOpen : 0;
    };

    if (render_subject_pinned(s)) ed = true;
    ImGui::Spacing();

    if (ImGui::CollapsingHeader("Sets in this session", hf(true)))
        ed |= render_sets_block(s, t0_unified_s);
    if (ImGui::CollapsingHeader("Subject — readiness & history", hf(true)))
        ed |= render_subject_day_snapshot(s.subject_snapshot);
    if (ImGui::CollapsingHeader("Session identity", hf(false)))
        ed |= render_subject_identity(s);
    if (ImGui::CollapsingHeader("Loading & exercise", hf(true)))
        ed |= render_loading_block(s);
    if (ImGui::CollapsingHeader("Load provenance (plates, collars)", hf(false)))
        ed |= render_load_provenance(s.load_provenance);
    if (ImGui::CollapsingHeader("Technique", hf(false)))
        ed |= render_technique_block(s);
    if (ImGui::CollapsingHeader("Training context (block phase, set flags)", hf(false)))
        ed |= render_training_context(s.training_context);
    if (ImGui::CollapsingHeader("Gear & implements", hf(false)))
        ed |= render_gear_block(s.gear);
    if (ImGui::CollapsingHeader("Safety setup", hf(false)))
        ed |= render_safety_block(s.safety);
    if (ImGui::CollapsingHeader("Environment", hf(false)))
        ed |= render_environment_block(s.environment);
    if (ImGui::CollapsingHeader("Subjective quality", hf(false)))
        ed |= render_quality_block(s.quality);
    if (ImGui::CollapsingHeader("Conditions & notes", hf(false)))
        ed |= render_conditions_block(s);
    if (ImGui::CollapsingHeader("Provenance (read-only)"))
        render_provenance_block(s);
    if (ImGui::CollapsingHeader("Pre-flight overrides (read-only)"))
        render_overrides_block(s);
    return ed;
}

} // namespace vbt::session_info_form

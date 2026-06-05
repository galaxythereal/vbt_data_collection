#pragma once

/**
 * @file SessionPanel.h
 * @brief Recording-side set control.
 *
 * The operator workflow is one recording per working set. The on-disk
 * schema still uses SessionInfo/session_dir for compatibility, but the
 * UI intentionally speaks in sets and only shows metadata needed at
 * collection time.
 */

#include "app/Application.h"
#include "gui/SessionInfoForm.h"
#include "processing/StillnessGate.h"
#include "utils/Uuid.h"
#include "utils/SubjectRegistry.h"
#include <imgui.h>
#include <nlohmann/json.hpp>
#include <cstring>
#include <fstream>
#include <string>
#include <vector>

namespace vbt {

class SessionPanel {
public:
    explicit SessionPanel(Application& app) : app_(app) {
        // Mint a UUID and auto-pick the next anonymised S## by scanning
        // the dataset_root. Operator can override either at any time;
        // pasting an existing UUID re-fills name + ID from the registry.
        info_.subject_uuid = make_uuid_v4();
        info_.subject_id   = next_subject_id(app_.config().dataset_root);
        prev_uuid_ = info_.subject_uuid;
        refresh_subjects_();
    }

    void render() {
        ImGui::Begin("Set Control");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        auto state = session.get_state();
        // Mirror live → local for every state where a session exists. We
        // used to gate this on RECORDING, but that left STOPPED state with
        // a stale local view, and the unconditional "session.mutable_info()
        // = info_" in the SAVE handler then wiped any post-session
        // calibration interval the operator had just committed. Running the
        // sync in CONFIGURED/READY/RECORDING/STOPPED keeps the panel's
        // draft in step with system-managed fields (calibration_intervals,
        // imu_snapshot, camera_snapshot, time_sync_check, sets).
        if (state != SessionState::IDLE && state != SessionState::SAVED) {
            sync_with_live_session_(session);
        }

        // ── Subject identity (UUID + Name + ID, with auto-fill) ──
        render_subject_identity_strip_();

        // ── Stillness / per-set calibration strip ──
        render_calibration_strip_(session, state);

        // ── Recording controls ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Recording");
        ImGui::PopStyleColor();
        render_recording_controls_();

        ImGui::Spacing();

        // ── Full SessionInfo form ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Set details");
        ImGui::PopStyleColor();
        if (render_operator_metadata_form_()) {
            // Before Create, local edits stay in info_. Once a set exists,
            // push edits into the live object so Save uses exactly what the
            // operator sees.
            if (state != SessionState::IDLE && state != SessionState::SAVED) {
                session.mutable_info() = info_;
            }
        }

        if (state != SessionState::IDLE && state != SessionState::SAVED) {
            // Re-sync live → local BEFORE the per-frame push-back. Anything
            // that happened during this frame on the session side
            // (calibration_interval commit from the strip, set advance,
            // post-recording stats etc.) needs to land in local first or
            // it gets wiped by the whole-struct overwrite below. This is
            // the second half of the calibration_intervals-empty bug.
            sync_with_live_session_(session);
            sync_single_set_from_fields_();
            session.mutable_info() = info_;
        }
    }

    bool consume_preflight_request() {
        bool r = request_preflight_;
        request_preflight_ = false;
        return r;
    }

private:
    void render_subject_identity_strip_() {
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Subject");
        ImGui::PopStyleColor();

        if (ImGui::SmallButton("Refresh")) refresh_subjects_();
        if (ImGui::SmallButton("+ New")) new_subject_();
        ImGui::TextDisabled("%zu previous", subject_records_.size());

        std::string preview = "New subject";
        if (selected_subject_ >= 0 && selected_subject_ < (int)subject_records_.size()) {
            preview = subject_label_(subject_records_[selected_subject_]);
        } else if (!info_.subject_name.empty() || !info_.subject_id.empty()) {
            preview = subject_label_from_info_();
        }
        if (ImGui::BeginCombo("Previous subjects", preview.c_str())) {
            if (ImGui::Selectable("+ New subject", selected_subject_ < 0)) {
                selected_subject_ = -1;
                new_subject_();
            }
            for (int i = 0; i < (int)subject_records_.size(); ++i) {
                const auto label = subject_label_(subject_records_[i]);
                bool sel = (i == selected_subject_);
                if (ImGui::Selectable(label.c_str(), sel)) {
                    selected_subject_ = i;
                    apply_subject_record_(subject_records_[i]);
                }
                if (sel) ImGui::SetItemDefaultFocus();
            }
            ImGui::EndCombo();
        }
        if (selected_subject_ >= 0 && selected_subject_ < (int)subject_records_.size()
            && ImGui::SmallButton("Load latest metadata")) {
            load_previous_metadata_(subject_records_[selected_subject_].latest_session);
        }

        char ubuf[64];
        std::snprintf(ubuf, sizeof(ubuf), "%s", info_.subject_uuid.c_str());
        ImGui::PushItemWidth(-1);
        if (ImGui::InputText("UUID", ubuf, sizeof(ubuf))) {
            info_.subject_uuid = ubuf;
        }
        ImGui::PopItemWidth();
        if (ImGui::SmallButton("copy##sp")) {
            ImGui::SetClipboardText(info_.subject_uuid.c_str());
        }
        ImGui::TextDisabled("UUID");

        // If the UUID changed (paste / edit), look it up in the dataset
        // registry and pre-fill name + S## from the most recent
        // matching session — or auto-bump S## if it's a brand-new one.
        if (info_.subject_uuid != prev_uuid_) {
            auto match = lookup_subject_by_uuid(app_.config().dataset_root,
                                                  info_.subject_uuid);
            if (match) {
                apply_subject_record_(*match);
            }
            prev_uuid_ = info_.subject_uuid;
        }

        ImGui::PushItemWidth(-1);
        char nbuf[256];
        std::snprintf(nbuf, sizeof(nbuf), "%s", info_.subject_name.c_str());
        if (ImGui::InputText("Name", nbuf, sizeof(nbuf))) info_.subject_name = nbuf;
        char ibuf[64];
        std::snprintf(ibuf, sizeof(ibuf), "%s", info_.subject_id.c_str());
        if (ImGui::InputText("Subject ID", ibuf, sizeof(ibuf))) info_.subject_id = ibuf;

        ImGui::DragFloat("Weight kg", &info_.subject_snapshot.body_mass_kg,
                         0.5f, 0.0f, 300.0f, "%.1f");
        ImGui::DragFloat("Height cm", &info_.subject_snapshot.height_cm,
                         0.5f, 0.0f, 250.0f, "%.1f");
        ImGui::DragInt("Age (years)", &info_.subject_snapshot.age_years,
                       1, 0, 120);

        ImGui::DragInt("Experience y", &info_.subject_snapshot.training_experience_years,
                       1, 0, 80);
        ImGui::PopItemWidth();
    }

    void render_recording_controls_() {
        auto& session = app_.session();
        auto state = session.get_state();

        if (state == SessionState::IDLE || state == SessionState::SAVED) {
            if (ImGui::Button("Create Set", ImVec2(-1, 32))) {
                // Apply exercise profile defaults if known.
                if (auto* prof = find_exercise_profile(app_.config(), info_.exercise)) {
                    app_.config().rep_seg.lowpass_cutoff_hz   = prof->lowpass_cutoff_hz;
                    app_.config().rep_seg.velocity_start_thresh = prof->velocity_start_thresh;
                    app_.config().rep_seg.velocity_rest_thresh  = prof->velocity_rest_thresh;
                    app_.config().rep_seg.min_rep_displacement_m = prof->min_rep_displacement_m;
                    app_.config().rep_seg.peak_window_s = prof->peak_window_s;
                    app_.config().rep_seg.prominence_fraction = prof->prominence_fraction;
                    app_.config().rep_seg.min_concentric_peak_mps = prof->min_concentric_peak_mps;
                    app_.config().rep_seg.setup_ignore_s = prof->setup_ignore_s;
                    session.autoreg().set_threshold(prof->velocity_loss_threshold_pct);
                }
                sync_single_set_from_fields_();
                session.autoreg().set_load_kg(info_.total_weight_kg);
                session.create(app_.config().dataset_root, info_, app_.config().bids_layout);
            }
        }

        if (state == SessionState::CONFIGURED || state == SessionState::READY) {
            const bool can_start = session.has_pre_session_calibration();
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.15f, 0.55f, 0.20f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.20f, 0.70f, 0.25f, 1.0f));
            ImGui::BeginDisabled(!can_start);
            if (ImGui::Button("START SET (Space)", ImVec2(-1, 42))) {
                request_preflight_ = true;
            }
            ImGui::EndDisabled();
            ImGui::PopStyleColor(2);
            ImGui::TextDisabled(can_start
                ? "Space → pre-flight check → record this set"
                : "Capture pre-set calibration before recording.");
        }

        if (state == SessionState::RECORDING) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.70f, 0.15f, 0.15f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.85f, 0.20f, 0.20f, 1.0f));
            if (ImGui::Button("STOP SET", ImVec2(-1, 42))) {
                session.stop_recording();
            }
            ImGui::PopStyleColor(2);

            auto stats = session.get_recording_stats();
            int cur_set = session.current_set_id();
            int reps_in_cur_set = 0;
            for (const auto& r : session.segmenter().get_reps())
                if (r.set_id == cur_set) ++reps_in_cur_set;

            ImGui::Spacing();
            ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1),
                                "Recording set %d  ·  %d reps so far",
                                cur_set, reps_in_cur_set);
            ImGui::Text("Duration: %.1f s", stats.duration_s);
            ImGui::Text("IMU: %lu samples", stats.imu_samples);
            ImGui::Text("Camera: %lu frames", stats.camera_frames);
            ImGui::Text("Reps total: %d", stats.rep_count);

            // Per-set progress bar
            int target = info_.target_reps;
            if (!info_.sets.empty()) target = info_.sets.back().target_reps;
            if (target > 0) {
                float prog = (float)reps_in_cur_set / (float)target;
                char overlay[48];
                std::snprintf(overlay, sizeof(overlay),
                               "set %d: %d / %d reps", cur_set, reps_in_cur_set, target);
                ImGui::ProgressBar(prog, ImVec2(-1, 0), overlay);
            }
        }

        if (state == SessionState::STOPPED) {
            const bool can_save = session.has_post_session_calibration();
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.20f, 0.35f, 0.65f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.45f, 0.80f, 1.0f));
            ImGui::BeginDisabled(!can_save);
            if (ImGui::Button("SAVE SET", ImVec2(-1, 36))) {
                // Mirror our local edits into the live session before save
                sync_single_set_from_fields_();
                session.mutable_info() = info_;
                session.save();
            }
            ImGui::EndDisabled();
            ImGui::PopStyleColor(2);
            if (!can_save) {
                ImGui::TextDisabled("Capture post-set calibration before saving.");
            }
        }
    }

    bool render_operator_metadata_form_() {
        bool ed = false;

        if (!app_.config().exercise_profiles.empty()) {
            ImGui::PushItemWidth(-1);
            int cur = -1;
            for (int i = 0; i < (int)app_.config().exercise_profiles.size(); ++i) {
                if (app_.config().exercise_profiles[i].name == info_.exercise) {
                    cur = i;
                    break;
                }
            }
            const char* preview = cur >= 0
                ? app_.config().exercise_profiles[cur].display_name.c_str()
                : info_.exercise.c_str();
            if (ImGui::BeginCombo("Exercise", preview)) {
                for (int i = 0; i < (int)app_.config().exercise_profiles.size(); ++i) {
                    const auto& p = app_.config().exercise_profiles[i];
                    bool sel = (i == cur);
                    if (ImGui::Selectable(p.display_name.c_str(), sel)) {
                        info_.exercise = p.name;
                        ed = true;
                    }
                    if (sel) ImGui::SetItemDefaultFocus();
                }
                ImGui::EndCombo();
            }
            ImGui::PopItemWidth();
        } else {
            ImGui::PushItemWidth(-1);
            char exercise[128];
            std::snprintf(exercise, sizeof(exercise), "%s", info_.exercise.c_str());
            if (ImGui::InputText("Exercise", exercise, sizeof(exercise))) {
                info_.exercise = exercise;
                ed = true;
            }
            ImGui::PopItemWidth();
        }

        ImGui::PushItemWidth(-1);
        char variant[128];
        std::snprintf(variant, sizeof(variant), "%s", info_.exercise_variant.c_str());
        if (ImGui::InputText("Variant", variant, sizeof(variant))) {
            info_.exercise_variant = variant;
            ed = true;
        }
        ImGui::PopItemWidth();

        ImGui::PushItemWidth(-1);
        ed |= ImGui::DragFloat("Bar kg", &info_.barbell_weight_kg, 0.5f, 0.0f, 100.0f, "%.1f");
        ed |= ImGui::DragFloat("Added kg", &info_.added_weight_kg, 0.5f, 0.0f, 500.0f, "%.1f");
        info_.total_weight_kg = info_.barbell_weight_kg + info_.added_weight_kg;
        ImGui::Text("Total load: %.1f kg", info_.total_weight_kg);

        ed |= ImGui::DragInt("Target reps", &info_.target_reps, 1, 1, 100);
        ed |= ImGui::SliderInt("RPE", &info_.rpe, 0, 10);
        ImGui::PopItemWidth();

        sync_single_set_from_fields_();
        return ed;
    }

    void sync_single_set_from_fields_() {
        info_.total_weight_kg = info_.barbell_weight_kg + info_.added_weight_kg;
        if (info_.sets.empty()) info_.sets.push_back(SetInfo{});
        if (info_.sets.size() > 1) info_.sets.resize(1);
        auto& st = info_.sets.front();
        st.set_id = 1;
        st.barbell_weight_kg = info_.barbell_weight_kg;
        st.added_weight_kg = info_.added_weight_kg;
        st.total_weight_kg = info_.total_weight_kg;
        st.percent_1rm = info_.percent_1rm;
        st.target_reps = info_.target_reps;
        st.rpe = info_.rpe;
    }

    void refresh_subjects_() {
        subject_records_ = scan_subject_registry(app_.config().dataset_root);
        selected_subject_ = -1;
        for (int i = 0; i < (int)subject_records_.size(); ++i) {
            if (subject_records_[i].subject_uuid == info_.subject_uuid) {
                selected_subject_ = i;
                break;
            }
        }
    }

    std::string subject_label_(const SubjectRecord& r) const {
        std::string label = r.subject_id.empty() ? "(no ID)" : r.subject_id;
        if (!r.subject_name.empty()) label += "  ·  " + r.subject_name;
        if (!r.latest_session.empty()) {
            label += "  ·  " + r.latest_session.filename().string();
        }
        return label;
    }

    std::string subject_label_from_info_() const {
        std::string label = info_.subject_id.empty() ? "(no ID)" : info_.subject_id;
        if (!info_.subject_name.empty()) label += "  ·  " + info_.subject_name;
        return label;
    }

    void new_subject_() {
        selected_subject_ = -1;
        info_.subject_uuid = make_uuid_v4();
        info_.subject_id   = next_subject_id(app_.config().dataset_root);
        info_.subject_name.clear();
        info_.subject_snapshot = SubjectDaySnapshot{};
        prev_uuid_ = info_.subject_uuid;
    }

    void apply_subject_record_(const SubjectRecord& r) {
        info_.subject_uuid = r.subject_uuid;
        info_.subject_id   = r.subject_id;
        info_.subject_name = r.subject_name;
        prev_uuid_ = info_.subject_uuid;
        load_previous_metadata_(r.latest_session);
    }

    void load_previous_metadata_(const std::filesystem::path& session_dir) {
        if (session_dir.empty()) return;
        std::ifstream f(session_dir / "metadata.json");
        if (!f.is_open()) return;
        try {
            nlohmann::json j;
            f >> j;
            SessionInfo prev = j.get<SessionInfo>();

            const auto keep_session_id = info_.session_id;
            const auto keep_date = info_.date;
            const auto keep_build = info_.build;
            const auto keep_calibration = info_.calibration;
            const auto keep_intervals = info_.calibration_intervals;
            const auto keep_imu = info_.imu_snapshot;
            const auto keep_camera = info_.camera_snapshot;
            const auto keep_sync = info_.time_sync_check;
            const auto keep_overrides = info_.preflight_overrides;

            info_ = prev;
            info_.session_id = keep_session_id;
            info_.date = keep_date;
            info_.build = keep_build;
            info_.calibration = keep_calibration;
            info_.calibration_intervals = keep_intervals;
            info_.imu_snapshot = keep_imu;
            info_.camera_snapshot = keep_camera;
            info_.time_sync_check = keep_sync;
            info_.preflight_overrides = keep_overrides;
            if (!info_.sets.empty()) {
                const auto first = info_.sets.front();
                info_.barbell_weight_kg = first.barbell_weight_kg;
                info_.added_weight_kg = first.added_weight_kg;
                info_.total_weight_kg = first.total_weight_kg;
                info_.target_reps = first.target_reps;
                info_.rpe = first.rpe;
            }
            info_.sets.clear();
            sync_single_set_from_fields_();

            prev_uuid_ = info_.subject_uuid;
        } catch (...) {
            // Keep the current draft if a legacy metadata file cannot be
            // deserialized cleanly.
        }
    }

    /// Pre-/post-recording stillness strip. Polls IMUReader::get_latest_sample
    /// at frame rate so we can drive a StillnessGate even when no recording is
    /// active. During recording the live Session owns its own gate, fed from
    /// the IMU callback — we just read its current state.
    void render_calibration_strip_(Session& session, SessionState state) {
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Stillness / per-set calibration");
        ImGui::PopStyleColor();

        const StillnessGate* gate_to_show = nullptr;
        if (state == SessionState::RECORDING) {
            gate_to_show = &session.stillness_gate();
        } else {
            // Drive the local gate from the latest IMU sample. One sample per
            // frame is plenty — the rolling 200-sample window settles in
            // ~3 s of idle UI repaint at 60 Hz, enough for the operator to
            // see the gate light up before they hit "Capture pre-session".
            if (session.imu().is_running()) {
                IMUSample s = session.imu().get_latest_sample();
                if (s.valid && s.host_timestamp_s != last_polled_host_t_) {
                    local_gate_.feed(s.host_timestamp_s,
                                      s.accel_x_g,  s.accel_y_g,  s.accel_z_g,
                                      s.gyro_x_dps, s.gyro_y_dps, s.gyro_z_dps);
                    last_polled_host_t_ = s.host_timestamp_s;
                }
            }
            gate_to_show = &local_gate_;
        }

        // Status row. Colour the chip green when the gate has been passing
        // long enough for a calibration interval to be committed.
        const float min_dur = (state == SessionState::RECORDING) ? 2.0f : 5.0f;
        const bool ready = gate_to_show->is_still()
                        && gate_to_show->pass_duration_s() >= min_dur;
        ImVec4 chip_col = ready
            ? ImVec4(0.30f, 0.85f, 0.30f, 1.0f)   // green: ready to commit
            : (gate_to_show->is_still()
                ? ImVec4(0.95f, 0.85f, 0.20f, 1.0f) // amber: still but not long enough
                : ImVec4(0.85f, 0.40f, 0.40f, 1.0f)); // red: moving
        ImGui::PushStyleColor(ImGuiCol_Text, chip_col);
        ImGui::Text("● %s", gate_to_show->is_still() ? "STILL" : "MOTION");
        ImGui::PopStyleColor();
        ImGui::Text("std %.4fg  gyro %.2fdps  %.1fs",
                     gate_to_show->accel_mag_std(),
                     gate_to_show->gyro_mag_mean(),
                     gate_to_show->pass_duration_s());

        // Context-appropriate commit button.
        const auto& ivs = session.get_info().calibration_intervals;
        bool have_pre = false, have_post = false;
        for (const auto& iv : ivs) {
            if (iv.type == "pre_session"  && iv.passed_gate) have_pre = true;
            if (iv.type == "post_session" && iv.passed_gate) have_post = true;
        }

        if (state == SessionState::IDLE) {
            ImGui::BeginDisabled(true);
            ImGui::Button("Capture pre-set calibration", ImVec2(-1, 28));
            ImGui::EndDisabled();
            ImGui::TextDisabled("Create a set first, then capture pre-set calibration.");
        } else if (state != SessionState::RECORDING && state != SessionState::STOPPED
            && state != SessionState::SAVED) {
            ImGui::BeginDisabled(!ready);
            if (ImGui::Button(have_pre
                  ? "Re-capture pre-set calibration"
                  : "Capture pre-set calibration", ImVec2(-1, 28))) {
                session.commit_calibration_interval(*gate_to_show, "pre_session", 0);
            }
            ImGui::EndDisabled();
            if (!have_pre) {
                ImGui::TextColored(ImVec4(1, 0.85f, 0.30f, 1),
                    "Pre-set calibration required before recording (>=%.0f s still).",
                    min_dur);
            }
        } else if (state == SessionState::RECORDING) {
            const int cur_set = session.current_set_id();
            char btn[64];
            std::snprintf(btn, sizeof(btn),
                           "Capture in-set stillness check (set %d)",
                           cur_set);
            ImGui::BeginDisabled(!ready);
            if (ImGui::Button(btn, ImVec2(-1, 28))) {
                session.commit_calibration_interval(*gate_to_show,
                                                     "inter_set", cur_set);
            }
            ImGui::EndDisabled();
            ImGui::TextDisabled(
                "Tip: pause for >=2 s after a rep — the gate auto-reports "
                "stillness from the live IMU stream.");
        } else {  // STOPPED / SAVED
            ImGui::BeginDisabled(!ready || state == SessionState::SAVED);
            if (ImGui::Button(have_post
                  ? "Re-capture post-set calibration"
                  : "Capture post-set calibration", ImVec2(-1, 28))) {
                session.commit_calibration_interval(*gate_to_show, "post_session", 0);
            }
            ImGui::EndDisabled();
            if (!have_post && state == SessionState::STOPPED) {
                ImGui::TextColored(ImVec4(1, 0.85f, 0.30f, 1),
                    "Post-set calibration required before save (>=%.0f s still).",
                    min_dur);
            }
        }

        // Compact roll-up of intervals committed so far this session.
        if (!ivs.empty()) {
            ImGui::TextDisabled("%zu interval(s) committed:", ivs.size());
            for (const auto& iv : ivs) {
                ImGui::BulletText("%-12s set=%d  dur=%.2fs  %s",
                                   iv.type.c_str(), iv.linked_set_id,
                                   iv.duration_s, iv.passed_gate ? "✓" : "✗");
            }
        }
        ImGui::Spacing();
    }

    void sync_with_live_session_(Session& session) {
        // Mirror live → local for every field the session itself manages.
        // Anything missing here gets WIPED next time the user edits the form,
        // because the recording-time form propagation does
        //   `session.mutable_info() = info_;`
        // which is a whole-struct overwrite. Every system-managed field
        // therefore has to round-trip through this function.
        const auto& live = session.get_info();
        info_.session_id     = live.session_id;
        info_.date           = live.date;
        info_.build          = live.build;
        info_.calibration    = live.calibration;
        info_.sets           = live.sets;
        info_.preflight_overrides = live.preflight_overrides;
        info_.schema_version = live.schema_version;
        // System-managed fields added in schema v5. Forgetting any of these
        // here is the bug that wiped calibration_intervals from every May-10
        // session on save.
        info_.imu_snapshot          = live.imu_snapshot;
        info_.camera_snapshot       = live.camera_snapshot;
        info_.calibration_intervals = live.calibration_intervals;
        info_.time_sync_check       = live.time_sync_check;
    }

    Application& app_;
    SessionInfo info_;          // draft — shipped to session.create() on Create
    std::string  prev_uuid_;    // for change-detection on the UUID input
    bool         request_preflight_ = false;
    std::vector<SubjectRecord> subject_records_;
    int          selected_subject_ = -1;

    // Pre-/post-recording stillness gate. The Session keeps its own gate
    // for the live recording stream; this one runs against polled latest
    // IMU samples so the operator gets stillness feedback BEFORE Record
    // is pressed and AFTER Stop is pressed.
    StillnessGate local_gate_;
    double        last_polled_host_t_ = 0.0;
};

} // namespace vbt

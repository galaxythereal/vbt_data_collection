#pragma once

/**
 * @file SessionPanel.h
 * @brief Recording-side session control + full PhD-grade metadata entry.
 *
 * This is the ONE panel the operator interacts with before/during/after a
 * recording. It owns a draft `SessionInfo` (default-constructed — every
 * field carries the documented sentinel until the operator types over
 * it) and exposes:
 *   • Subject identity (UUID auto-generated, S## auto-incremented,
 *     UUID-paste autofills name + ID from prior sessions)
 *   • The same shared SessionInfo form used by the annotation studio,
 *     so every PhD-grade field is reachable from the main GUI
 *   • Recording controls + multi-set "Next set →" workflow
 */

#include "app/Application.h"
#include "gui/SessionInfoForm.h"
#include "utils/Uuid.h"
#include "utils/SubjectRegistry.h"
#include <imgui.h>
#include <cstring>
#include <string>

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
    }

    void render() {
        ImGui::Begin("Session Control");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        auto state = session.get_state();
        if (state == SessionState::RECORDING) {
            // While recording, the live session owns the truth — point
            // our editor at it so any in-flight edits propagate to the
            // metadata.json that gets written on stop.
            sync_with_live_session_(session);
        }

        // ── Subject identity (UUID + Name + ID, with auto-fill) ──
        render_subject_identity_strip_();

        // ── Recording controls ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Recording");
        ImGui::PopStyleColor();
        render_recording_controls_();

        ImGui::Spacing();

        // ── Full SessionInfo form ──
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
        ImGui::SeparatorText("Session metadata (all fields default to null/unspecified)");
        ImGui::PopStyleColor();
        ImGui::Checkbox("Expand all sections", &expand_all_);
        ImGui::SameLine();
        ImGui::TextDisabled("(every block at once)");

        // Form lives in its own scroll region so the recording controls
        // above stay anchored when the operator scrolls the long form.
        ImGui::BeginChild("##session_info_form",
                           ImVec2(0, std::max(220.0f, ImGui::GetContentRegionAvail().y)),
                           true);  // border
        if (session_info_form::render_full_form(info_, /*t0=*/0.0, expand_all_)) {
            // Local edits stay in info_ until session.create() ships
            // them. While recording, also propagate live so the on-disk
            // metadata.json reflects the latest value.
            if (state == SessionState::RECORDING) {
                session.mutable_info() = info_;
            }
        }
        ImGui::EndChild();
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

        char ubuf[64];
        std::snprintf(ubuf, sizeof(ubuf), "%s", info_.subject_uuid.c_str());
        ImGui::PushItemWidth(280);
        if (ImGui::InputText("UUID", ubuf, sizeof(ubuf))) {
            info_.subject_uuid = ubuf;
        }
        ImGui::PopItemWidth();
        ImGui::SameLine();
        if (ImGui::SmallButton("copy##sp")) {
            ImGui::SetClipboardText(info_.subject_uuid.c_str());
        }

        // If the UUID changed (paste / edit), look it up in the dataset
        // registry and pre-fill name + S## from the most recent
        // matching session — or auto-bump S## if it's a brand-new one.
        if (info_.subject_uuid != prev_uuid_) {
            auto match = lookup_subject_by_uuid(app_.config().dataset_root,
                                                  info_.subject_uuid);
            if (match) {
                info_.subject_id   = match->subject_id;
                info_.subject_name = match->subject_name;
            }
            prev_uuid_ = info_.subject_uuid;
        }

        if (ImGui::Button("+ New subject")) {
            info_.subject_uuid = make_uuid_v4();
            info_.subject_id   = next_subject_id(app_.config().dataset_root);
            info_.subject_name.clear();
            prev_uuid_ = info_.subject_uuid;
        }
        ImGui::SameLine();
        ImGui::TextDisabled("(re-recording same person? paste their UUID)");
    }

    void render_recording_controls_() {
        auto& session = app_.session();
        auto state = session.get_state();

        if (state == SessionState::IDLE || state == SessionState::SAVED) {
            if (ImGui::Button("Create Session", ImVec2(-1, 32))) {
                // Apply exercise profile defaults if known.
                if (auto* prof = find_exercise_profile(app_.config(), info_.exercise)) {
                    app_.config().rep_seg.lowpass_cutoff_hz   = prof->lowpass_cutoff_hz;
                    app_.config().rep_seg.velocity_start_thresh = prof->velocity_start_thresh;
                    app_.config().rep_seg.velocity_rest_thresh  = prof->velocity_rest_thresh;
                    app_.config().rep_seg.min_rep_displacement_m = prof->min_rep_displacement_m;
                    session.autoreg().set_threshold(prof->velocity_loss_threshold_pct);
                }
                session.autoreg().set_load_kg(info_.total_weight_kg);
                session.create(app_.config().dataset_root, info_, app_.config().bids_layout);
            }
        }

        if (state == SessionState::CONFIGURED || state == SessionState::READY) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.15f, 0.55f, 0.20f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.20f, 0.70f, 0.25f, 1.0f));
            if (ImGui::Button("START RECORDING (Space)", ImVec2(-1, 42))) {
                request_preflight_ = true;
            }
            ImGui::PopStyleColor(2);
            ImGui::TextDisabled("Space → pre-flight check → record");
        }

        if (state == SessionState::RECORDING) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.70f, 0.15f, 0.15f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.85f, 0.20f, 0.20f, 1.0f));
            if (ImGui::Button("STOP RECORDING", ImVec2(-1, 42))) {
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
                                "● Recording set %d  ·  %d reps so far",
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

            // Next-set staging
            ImGui::Spacing();
            ImGui::SeparatorText("Next set");
            ImGui::DragFloat("Next bar (kg)",     &next_set_bar_kg_,    0.5f);
            ImGui::DragFloat("Next added (kg)",   &next_set_added_kg_,  0.5f);
            float next_total = next_set_bar_kg_ + next_set_added_kg_;
            ImGui::TextDisabled("Next total: %.1f kg", next_total);
            ImGui::DragInt("Next target reps",  &next_set_target_reps_, 1, 1, 30);
            ImGui::SliderInt("Last set RPE",    &last_set_rpe_, 1, 10);
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.20f, 0.50f, 0.20f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.65f, 0.25f, 1.0f));
            if (ImGui::Button("Next set →", ImVec2(-1, 36))) {
                if (!session.get_info().sets.empty()) {
                    session.mutable_info().sets.back().rpe = last_set_rpe_;
                }
                SetInfo nx;
                nx.barbell_weight_kg = next_set_bar_kg_;
                nx.added_weight_kg   = next_set_added_kg_;
                nx.total_weight_kg   = next_total;
                nx.target_reps       = next_set_target_reps_;
                session.advance_set(nx);
            }
            ImGui::PopStyleColor(2);
        }

        if (state == SessionState::STOPPED) {
            ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.20f, 0.35f, 0.65f, 1.0f));
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.45f, 0.80f, 1.0f));
            if (ImGui::Button("SAVE SESSION", ImVec2(-1, 36))) {
                // Mirror our local edits into the live session before save
                session.mutable_info() = info_;
                session.save();
            }
            ImGui::PopStyleColor(2);
        }
    }

    void sync_with_live_session_(Session& session) {
        // Pull live fields the session itself updates (session_id, build,
        // sets[].t_start/end, completed_reps) so the form shows them.
        const auto& live = session.get_info();
        info_.session_id    = live.session_id;
        info_.date          = live.date;
        info_.build         = live.build;
        info_.calibration   = live.calibration;
        info_.sets          = live.sets;
        info_.preflight_overrides = live.preflight_overrides;
        info_.schema_version = live.schema_version;
    }

    Application& app_;
    SessionInfo info_;          // draft — shipped to session.create() on Create
    std::string  prev_uuid_;    // for change-detection on the UUID input
    bool         request_preflight_ = false;
    bool         expand_all_   = false;

    // Per-multi-set staging while RECORDING.
    float next_set_bar_kg_      = 20.0f;
    float next_set_added_kg_    = 0.0f;
    int   next_set_target_reps_ = 5;
    int   last_set_rpe_         = 7;
};

} // namespace vbt

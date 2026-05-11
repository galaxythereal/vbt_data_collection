#pragma once

/**
 * @file SessionInfoForm.h
 * @brief Pure ImGui rendering of the SessionInfo struct.
 *
 * Free functions used by both the recording-side SessionPanel
 * (pre-recording entry) and the annotation studio's MetadataPanel
 * (post-recording editing). Each function is "body only" — callers
 * wrap them in CollapsingHeaders / Childs as fits their layout.
 *
 * Every input is bound to a SessionInfo (or sub-struct) reference and
 * returns true iff the user mutated it. Callers translate that into a
 * dirty flag (Session::write_metadata, SessionData::mark_meta_dirty).
 *
 * No fields are required up-front — every field defaults to the value
 * baked into the struct definition in app/Config.h. The operator types
 * only what they care about; everything else carries the documented
 * "unspecified" / "unknown" / 0 sentinel through to disk.
 */

#include "app/Config.h"
#include <imgui.h>
#include <string>

namespace vbt::session_info_form {

// ─── Section bodies (no CollapsingHeader; caller wraps) ─────────────
// Pinned at the top of the panel — always visible.
bool render_subject_pinned(SessionInfo& s);
// Session-level identity: operator, date, time of day, session_id (RO).
bool render_subject_identity(SessionInfo& s);
// Day-of acute readiness, nutrition, health, consent.
bool render_subject_day_snapshot(SubjectDaySnapshot& s);
// Multi-set editor. `t0_unified_s` is the session's wall-clock t0; if 0
// the per-set time fields are shown as absolute Unix-epoch seconds.
bool render_sets_block(SessionInfo& s, double t0_unified_s);
// Top-level loading & RPE & target-rep fields. These are kept for
// backward-compat; multi-set sessions should drive these from sets[0].
// `profiles` populates the Exercise dropdown; pass an empty span for a
// freeform text input fallback.
bool render_loading_block(SessionInfo& s,
                           const std::vector<ExerciseProfile>& profiles = {});
bool render_load_provenance(LoadProvenance& s);
bool render_technique_block(SessionInfo& s);
bool render_training_context(TrainingContext& s);
bool render_gear_block(GearAndImplements& s);
bool render_safety_block(SafetySetup& s);
bool render_environment_block(EnvironmentDetails& s);
bool render_quality_block(SubjectiveQuality& s);
bool render_conditions_block(SessionInfo& s);

// Read-only provenance & override-trail blocks.
void render_provenance_block(const SessionInfo& s);
void render_overrides_block(const SessionInfo& s);

/// Render every block, wrapped in CollapsingHeaders. Used by the
/// recording-side SessionPanel where the full form lives in a scroll
/// region and the user picks what to fill. Returns true if any value
/// was edited this frame.
bool render_full_form(SessionInfo& s,
                       double t0_unified_s = 0.0,
                       bool expand_all = false,
                       const std::vector<ExerciseProfile>& profiles = {});

} // namespace vbt::session_info_form

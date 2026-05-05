#pragma once

/**
 * @file MetadataPanel.h
 * @brief Editor for the session's SessionInfo (metadata.json).
 *
 * Inputs are bound directly to the SessionInfo fields; any edit marks the
 * session's meta_dirty flag. Save is initiated by the studio shell, not
 * this panel — this keeps the dirty/save lifecycle in one place. Provides
 * read-only sub-views for build provenance, calibration provenance, and
 * preflight overrides since those are written by the recorder, not the
 * annotator.
 */

#include "annotation/SessionData.h"
#include <imgui.h>

namespace vbt {

class MetadataPanel {
public:
    MetadataPanel() = default;
    void set_session(SessionData* s) { session_ = s; }
    void render();

private:
    void render_subject_block_();
    void render_subject_day_snapshot_();
    void render_loading_block_();
    void render_load_provenance_();
    void render_technique_block_();
    void render_training_context_();
    void render_gear_block_();
    void render_safety_block_();
    void render_environment_block_();
    void render_quality_block_();
    void render_conditions_block_();
    void render_provenance_block_();
    void render_overrides_block_();

    /// "Show all" toggle expands every section in one click — ergonomic
    /// shortcut for completing a fresh session post-recording.
    bool expand_all_ = false;

    SessionData* session_ = nullptr;
};

} // namespace vbt

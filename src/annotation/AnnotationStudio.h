#pragma once

/**
 * @file AnnotationStudio.h
 * @brief Top-level "post-recording" mode: session library + multi-panel
 *        annotation workspace.
 *
 * Replaces the legacy ReplayMode. Layout (default):
 *   ┌─ Sessions (left) ─┬─ Video (centre top) ─────────┬─ Metadata (right) ─┐
 *   │                   ├─ Timeline (centre middle) ──┤                    │
 *   │                   ├─ Rep Table (bottom-left) ────┴─ Marker quality ──┤
 *   └───────────────────┴──────────────────────────────────────────────────┘
 *
 * Owns the SessionData, VideoCache, all panels, and the shared playhead
 * time. Persistence is invoked from here so dirty/save lifecycle is in
 * one place.
 *
 * Hotkeys:
 *   Ctrl+S          save dirty changes
 *   Ctrl+R          reload from disk (discards unsaved edits — confirm)
 *   Z / X           previous / next rep
 *   Space           play/pause video
 *   ←/→             step ±1 frame
 *   PageUp/PageDown previous / next rep (alias)
 */

#include "annotation/SessionData.h"
#include "annotation/SessionLibrary.h"
#include "annotation/VideoCache.h"
#include "annotation/TimelinePanel.h"
#include "annotation/VideoPanel.h"
#include "annotation/RepTablePanel.h"
#include "annotation/MetadataPanel.h"
#include "annotation/MarkerQualityPanel.h"
#include <memory>
#include <string>

namespace vbt {

class Application;

class AnnotationStudio {
public:
    explicit AnnotationStudio(Application& app);

    /// Window visibility (drives MainWindow's open/close).
    void open();
    void close() { is_open_ = false; }
    bool is_open() const { return is_open_; }

    void render();

private:
    // ── Layout helpers ─────────────────────────────────────────────
    void render_session_browser_();
    void render_workspace_();
    void render_save_dialog_();
    void render_unsaved_warning_();

    // ── Actions ────────────────────────────────────────────────────
    void load_session_(const std::filesystem::path& dir);
    void save_();
    void reload_();
    void seek_(double t_unified_s);
    void select_rep_(int rep_index);

    Application&     app_;
    bool             is_open_       = false;
    std::string      filter_query_;
    int              selected_session_idx_ = -1;

    SessionLibrary   library_;
    SessionData      session_;
    VideoCache       video_;

    TimelinePanel       timeline_;
    VideoPanel          video_panel_;
    RepTablePanel       rep_table_;
    MetadataPanel       meta_panel_;
    MarkerQualityPanel  quality_panel_;

    /// Single source of truth for the playhead. Each render pass collects
    /// the latest value from whichever panel last touched it.
    double           playhead_t_s_ = 0.0;

    SessionLoadDiag  last_diag_;
    bool             show_save_dialog_ = false;
    std::string      save_note_;
};

} // namespace vbt

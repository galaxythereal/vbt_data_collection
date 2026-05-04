#pragma once

/**
 * @file RepTablePanel.h
 * @brief Editable per-rep table — the structured view onto the same
 *        annotations the TimelinePanel renders graphically.
 *
 * Displays one row per rep with:
 *   ID · t_start · t_end · concentric_dur · ecc_dur · peak_v · mean_v · ROM · source
 *
 * Inline edits (drag-edit a t_start cell, type a peak_v override) flow
 * into SessionData; the timeline view picks them up on the next render.
 *
 * Selecting a row centres the timeline on that rep and seeks the video
 * to its concentric start.
 */

#include "annotation/SessionData.h"
#include <imgui.h>
#include <functional>

namespace vbt {

class RepTablePanel {
public:
    RepTablePanel() = default;

    void set_session(SessionData* s) { session_ = s; selected_ = -1; }

    /// Callback fired when user picks a rep row (so the studio can centre
    /// the timeline + seek the video). Argument is the rep index in
    /// session->reps().
    void set_select_cb(std::function<void(int)> cb) { on_select_ = std::move(cb); }

    void render();

    int selected_index() const { return selected_; }

private:
    void render_toolbar_();
    void render_table_();
    void render_metric_summary_();

    SessionData* session_ = nullptr;
    int          selected_ = -1;
    std::function<void(int)> on_select_;
};

} // namespace vbt

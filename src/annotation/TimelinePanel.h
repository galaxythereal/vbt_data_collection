#pragma once

/**
 * @file TimelinePanel.h
 * @brief Interactive position + velocity timeline with rep bands.
 *
 * The signature view of the annotation studio. Two stacked ImPlot axes
 * sharing the same x-range:
 *   • Top:    position (camera, m)
 *   • Bottom: velocity (camera derivative, m/s) + IMU |a|−1 (g) overlay
 *
 * Rep boundaries render as colored bands (concentric blue, eccentric red,
 * rest grey). The bands are click-and-drag editable: grab an edge with the
 * left mouse, drag to retime; release to commit. A vertical "playhead"
 * marker tracks the currently-displayed video frame and is the single
 * source of truth for time in the studio (other panels read it).
 *
 * Interactions:
 *   • LMB on a band edge       → drag-edit
 *   • LMB on empty timeline    → move playhead
 *   • Shift+LMB drag           → create a new rep at the dragged span
 *   • RMB on a rep             → context menu (delete, split, merge)
 *   • Mouse wheel              → zoom x; ImPlot handles middle-drag pan
 *   • Z / X / N / Space        → navigate prev/next rep, new rep, play/pause
 */

#include "annotation/SessionData.h"
#include "processing/RepSegmenter.h"
#include <imgui.h>
#include <vector>
#include <optional>

namespace vbt {

class TimelinePanel {
public:
    TimelinePanel() = default;

    /// Bind to a session. Resets view + selection.
    void set_session(SessionData* s);

    /// Render the panel inside an existing ImGui::Begin/End. Returns the
    /// playhead time in unified seconds (== 0 if no session).
    double render(double playhead_t_s);

    // ─── External controls ──────────────────────────────────────────
    /// Recenter view on a rep. Index < 0 means "fit all reps".
    void center_on_rep(int rep_index);
    /// Override the playhead — the video panel calls this when the user
    /// types a frame number, etc.
    void set_playhead(double t_s) { playhead_t_s_ = t_s; }

    /// Index of the rep currently selected in the table (so we draw it
    /// highlighted). −1 = no selection.
    void set_selected_rep(int rep_index) { selected_rep_ = rep_index; }
    int  selected_rep() const { return selected_rep_; }

    /// True if the user dragged anything since the last render — caller
    /// (AnnotationStudio) passes this to mark_reps_dirty().
    bool consume_dirty_flag() {
        bool d = made_edit_; made_edit_ = false; return d;
    }

private:
    /// Push studio-wide plot style overrides (line weight, grid, colors,
    /// tick density). Paired with pop_plot_style_(). Centralized so every
    /// chart in the studio looks the same.
    void push_plot_style_();
    void pop_plot_style_();
    float saved_line_weight_  = 1.0f;
    float saved_marker_size_  = 4.0f;
    ImVec2 saved_plot_padding_ = ImVec2(10, 10);

    SessionData* session_ = nullptr;
    double playhead_t_s_  = 0.0;
    int    selected_rep_  = -1;

    // View bookkeeping — ImPlot axis range. We override on session change
    // and on center_on_rep; otherwise ImPlot manages it.
    bool   force_view_   = false;
    double view_t_min_   = 0.0;
    double view_t_max_   = 0.0;

    // Drag state for boundary editing.
    enum class DragKind { None, ConcentricStart, ConcentricEnd,
                                EccentricEnd, RestEnd };
    int       drag_rep_index_ = -1;
    DragKind  drag_kind_      = DragKind::None;

    // Shift+drag rep creation in progress.
    std::optional<double> shift_drag_t0_;

    bool made_edit_ = false;

    // ─── Helpers ────────────────────────────────────────────────────
    void render_position_plot_(double t0, double t1, float height);
    void render_velocity_plot_(double t0, double t1, float height);
    void render_rep_bands_();
    void render_playhead_(double t0, double t1);
    void handle_rep_context_menu_(int rep_index);
    /// Detect which boundary (if any) is within `tol_px` of the mouse
    /// position. Returns drag_rep_index_ + drag_kind_ via reference.
    void hit_test_boundaries_(double mouse_t, double tol_t,
                              int& out_rep, DragKind& out_kind) const;
    void commit_drag_(double new_t);
};

} // namespace vbt

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
#include <functional>
#include <array>

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

    /// AnnotationStudio sets this to push an undo snapshot the moment a
    /// rep boundary drag starts. Without it, undo would have to track
    /// per-pixel updates from DragLineX which would saturate the stack.
    void set_edit_begin_cb(std::function<void()> cb) { on_edit_begin_ = std::move(cb); }

    /// When focus mode is on, the studio passes the selected rep's
    /// time-range each frame so the panel can pin its X axis to it.
    void set_forced_view(double t_min, double t_max) {
        view_t_min_ = t_min; view_t_max_ = t_max; force_view_ = true;
    }

private:
    // Drag state for boundary editing — declared up here because several
    // member-variable declarations below use the type.
    enum class DragKind { None, ConcentricStart, ConcentricEnd,
                                TopRestEnd, EccentricEnd, RestEnd };
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
    std::function<void()> on_edit_begin_;
    /// Coalesces frame-by-frame DragLineX changes into one undo entry —
    /// flips false again on mouse-up so the next drag is a fresh step.
    bool   pushed_this_drag_ = false;
    /// Most-recently interacted boundary — set by DragLineX when the
    /// user drags it. Arrow keys (when the timeline is hovered) nudge
    /// this boundary so users can fine-tune by ±5 ms / ±50 ms (Shift)
    /// without going back to the mouse.
    int      last_handle_rep_  = -1;
    DragKind last_handle_kind_ = DragKind::None;
    /// Render the keyboard hint banner under the toolbar.
    void render_keyboard_hint_();
    /// Process arrow-key nudges on the most recent boundary.
    void process_keyboard_nudge_();
    /// Apply a delta-seconds nudge to one specific boundary, with
    /// neighbour-clamping (so we can't invert phases).
    void apply_nudge_(int rep_i, DragKind kind, double dt);

    // View bookkeeping — ImPlot axis range. We override on session change
    // and on center_on_rep; otherwise ImPlot manages it.
    bool   force_view_   = false;
    double view_t_min_   = 0.0;
    double view_t_max_   = 0.0;

    // ConcentricEnd is paired with TopRestStart (same time value),
    // TopRestEnd with EccentricStart, EccentricEnd with RestStart. The
    // 5 draggable lines per rep move these paired points in lockstep.
    int       drag_rep_index_ = -1;
    DragKind  drag_kind_      = DragKind::None;
    /// Set true while the mouse is over a draggable handle so the cursor
    /// can be flipped to ResizeEW.
    bool      hover_handle_   = false;
    /// Toggle to show / hide detection overlays (zero-cross dots, peak
    /// triangles). Bound to a checkbox in the timeline toolbar.
    bool      show_zerocross_ = true;
    bool      show_peaks_     = true;
    bool      show_fsync_     = false;
    /// Snap a dragged boundary to the nearest velocity zero-crossing
    /// when the Alt key is held.
    bool      snap_to_zerocross_ = true;

    // Shift+drag rep creation in progress.
    std::optional<double> shift_drag_t0_;

    bool made_edit_ = false;

    // ─── Helpers ────────────────────────────────────────────────────
    void render_position_plot_(double t0, double t1, float height);
    void render_velocity_plot_(double t0, double t1, float height);
    void render_rep_bands_();
    void render_playhead_(double t0, double t1);
    /// Draw visible draggable handles (filled circles + chevrons) at
    /// every editable rep boundary, in pixel coordinates so the hit zone
    /// is constant regardless of the X-axis zoom level.
    void render_drag_handles_(double t0_session);
    /// Render the rep boundaries as ImPlot::DragLineX widgets — the
    /// canonical way to expose a draggable vertical line in ImPlot. Each
    /// line carries a unique id; ImPlot handles hit detection and the
    /// drag delta itself, so we just write the new value back if it
    /// changed (and clamp to neighbouring boundaries to stop the user
    /// from inverting phases). Replaces the custom hit-test that the
    /// user reported as broken.
    void render_imp_drag_lines_(double t0_session);
    /// Render zero-crossing dots, peak-velocity triangles, and FSYNC
    /// tick marks when their respective overlay toggles are on.
    void render_detection_overlay_(double t0_session, bool is_velocity_axis);
    void render_hover_tooltip_(double t0_session);
    void render_toolbar_();
    void handle_rep_context_menu_(int rep_index);
    /// Detect which boundary (if any) is within `tol_px` of the mouse
    /// position. Returns drag_rep_index_ + drag_kind_ via reference.
    void hit_test_handle_pixels_(const ImVec2& mouse_px, double t0_session,
                                  int& out_rep, DragKind& out_kind) const;
    void commit_drag_(double new_t);
    /// Snap a candidate time to the nearest cleaned-vz zero crossing
    /// within ±max_dt_s if snap_to_zerocross_ is enabled.
    double maybe_snap_(double t) const;
};

} // namespace vbt

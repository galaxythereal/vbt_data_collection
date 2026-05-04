#pragma once

/**
 * @file MarkerQualityPanel.h
 * @brief Marker-tracking outlier analysis and threshold tuning.
 *
 * Three companion views:
 *   1. **Cleaning controls** — sliders bound to MarkerCleanConfig. Edits
 *      are debounced (recompute only after 200 ms idle) to keep the
 *      ImPlot redraw at 60 fps even on long sessions.
 *   2. **Quality histograms** — confidence, SNR, circularity, signed
 *      across {accepted, rejected by current thresholds}. Lets the user
 *      see at a glance what fraction of frames the gate is dropping.
 *   3. **Outlier scatter** — raw vz vs rolling-MAD residual. Hover to
 *      see the frame timestamp; double-click to seek the video to that
 *      frame.
 *
 * The panel emits `seek_request_t_s` when the user double-clicks an
 * outlier; the studio shell wires this to the timeline/video.
 */

#include "annotation/SessionData.h"
#include <imgui.h>
#include <chrono>
#include <optional>
#include <functional>

namespace vbt {

class MarkerQualityPanel {
public:
    MarkerQualityPanel() = default;

    void set_session(SessionData* s);

    /// Callback invoked when the user double-clicks an outlier in the
    /// scatter; argument is the unified-time-s the studio should seek to.
    void set_seek_cb(std::function<void(double)> cb) { on_seek_ = std::move(cb); }

    void render();

private:
    void render_cleaning_controls_();
    void render_quality_histograms_();
    void render_outlier_scatter_();
    void apply_cleaning_if_pending_();

    SessionData* session_ = nullptr;
    MarkerCleanConfig live_cfg_;
    bool   cfg_pending_ = false;
    std::chrono::steady_clock::time_point cfg_last_change_;
    std::function<void(double)> on_seek_;
};

} // namespace vbt

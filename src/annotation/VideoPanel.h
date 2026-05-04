#pragma once

/**
 * @file VideoPanel.h
 * @brief Video viewport with marker overlay and rep-aware playback.
 *
 * Reads from the studio's VideoCache, draws the IR frame in an
 * ImGui::Image, and overlays:
 *   • The 2D marker pixel (pixel_u/pixel_v) at the current frame.
 *   • A bounding ring colored by detection confidence.
 *   • A trail of the marker's last ~30 frames (motion ribbon).
 *   • The current rep's phase label at top-left.
 *
 * Playback transport mirrors a video editor: play/pause, step ±1 frame
 * (left/right arrow), jump to next/prev rep (PageUp/PageDown), set
 * playback speed (0.25× / 1× / 4×).
 */

#include "annotation/SessionData.h"
#include "annotation/VideoCache.h"
#include <imgui.h>
#include <chrono>

namespace vbt {

class VideoPanel {
public:
    VideoPanel() = default;

    void set_session(SessionData* s, VideoCache* cache);

    /// Renders inside an existing ImGui::Begin/End. The caller passes the
    /// timeline-driven `playhead_t_s` (wall-clock); the panel may bump it
    /// when the user hits play, returning the new value.
    double render(double playhead_t_s, double t0_unified_s);

    /// True if the user is in play mode and the panel is advancing the
    /// playhead on its own.
    bool is_playing() const { return playing_; }

private:
    void draw_marker_overlay_(const ImVec2& image_pos, const ImVec2& image_size);
    void draw_phase_badge_(const ImVec2& image_pos);
    int  current_rep_index_at_(double t_s) const;

    SessionData* session_ = nullptr;
    VideoCache*  cache_   = nullptr;

    double playhead_t_s_ = 0.0;
    bool   playing_       = false;
    float  speed_         = 1.0f;
    std::chrono::steady_clock::time_point last_tick_;
};

} // namespace vbt

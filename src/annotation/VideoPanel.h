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
 * Playback transport mirrors a video editor: play/pause, step ±1 frame,
 * step ±1 second, jump to next/prev rep, and set playback speed.
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
    void draw_phase_badge_(const ImVec2& image_pos, float image_w);
    int  current_rep_index_at_(double t_s) const;
    double rep_start_(const RepAnnotation& rep) const;
    double rep_end_(const RepAnnotation& rep) const;
    void seek_relative_seconds_(double dt_s);
    void seek_relative_frames_(int frame_delta);

    SessionData* session_ = nullptr;
    VideoCache*  cache_   = nullptr;

    double playhead_t_s_ = 0.0;
    bool   playing_       = false;
    float  speed_         = 1.0f;
    std::chrono::steady_clock::time_point last_tick_;
};

} // namespace vbt

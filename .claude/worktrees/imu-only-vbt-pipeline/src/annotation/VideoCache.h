#pragma once

/**
 * @file VideoCache.h
 * @brief OpenCV-backed seekable IR video reader for the annotation studio.
 *
 * Wraps cv::VideoCapture with two convenience layers:
 *   1. **Index-aligned seek**: video_frames.csv carries a unified_time_s
 *      per frame; given a timestamp, we look up the closest frame index
 *      and seek the capture there. Falls back to nearest-Iframe + sequential
 *      decode for codecs without exact seek (mp4v).
 *   2. **GL texture upload**: holds a single GL texture id and re-uploads
 *      the most recently decoded frame. Drawn into the VideoPanel via
 *      ImGui::Image. Mono IR is uploaded as RED + swizzle so it renders
 *      grayscale without converting to BGR on every frame.
 *
 * The cache is single-threaded — opening a new session resets the
 * VideoCapture. We don't preload frames; seek-and-decode on every
 * timestamp change is fast enough for scrubbing at 30 fps GUI rate.
 */

#include "annotation/SessionData.h"
#include <opencv2/core.hpp>
#include <opencv2/videoio.hpp>
#include <GLFW/glfw3.h>
#include <filesystem>

namespace vbt {

class VideoCache {
public:
    VideoCache() = default;
    ~VideoCache();

    /// Open ir_video.mp4 from a session's camera/ subdirectory and bind
    /// it to the VideoIndex for time→frame lookup. Closes any prior video.
    bool open(const std::filesystem::path& session_dir, const VideoIndex& index);
    void close();
    bool is_open() const { return cap_.isOpened(); }

    int  width()  const { return frame_w_; }
    int  height() const { return frame_h_; }
    int  frame_count() const { return frame_count_; }

    /// Seek to the frame whose unified_t_s is closest to `t_s`, decode it,
    /// and return a const reference to the cached frame. The returned Mat
    /// stays valid until the next seek call.
    const cv::Mat* seek_to_time(double t_s);

    /// True iff we successfully decoded a frame on the most recent seek.
    bool current_frame_valid() const { return !current_frame_.empty(); }

    /// Returns the current frame's index (within the video file, equal to
    /// the cv::VideoCapture's CAP_PROP_POS_FRAMES on the previous decode).
    int  current_frame_index() const { return current_frame_idx_; }

    /// GL texture for the current frame; uploads on first call after a
    /// successful seek. ImGui::Image expects (void*)(intptr_t)id.
    unsigned int gl_texture();

private:
    bool decode_frame_at_(int frame_idx);

    const VideoIndex* index_ = nullptr;
    cv::VideoCapture  cap_;
    cv::Mat           current_frame_;       // last decoded frame (BGR or 8U single)
    int               current_frame_idx_ = -1;
    int               frame_w_ = 0;
    int               frame_h_ = 0;
    int               frame_count_ = 0;

    unsigned int      texture_id_ = 0;
    bool              texture_dirty_ = true;
};

} // namespace vbt

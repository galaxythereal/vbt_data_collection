#pragma once

/**
 * @file SessionVideo.h
 * @brief The session's IR video, seekable by the same frame index the annotation uses.
 *
 * WHY THE INDEX IS THE SAME NUMBER. camera/marker_positions.csv carries one row per video
 * frame and camera/video_frames.csv carries one row per video frame, both in write order.
 * The annotation counts frames the same way, so rep boundary frame N is video frame N.
 * Nothing has to be matched by timestamp, and there is no chance of a half-frame offset
 * between what the plot says and what the reviewer is looking at.
 *
 * The reviewer needs this to judge the reps no algorithm can fix: a person walking across
 * the marker, the bar being set down instead of lifted. Those are visible in one glance at
 * the frame and invisible in the track.
 */

#include <filesystem>
#include <string>

#include <opencv2/core.hpp>
#include <opencv2/videoio.hpp>

namespace vbt::offline {

class SessionVideo {
public:
    SessionVideo() = default;
    ~SessionVideo();

    SessionVideo(const SessionVideo&)            = delete;
    SessionVideo& operator=(const SessionVideo&) = delete;

    /// Open <session>/camera/ir_video.mp4. Closes whatever was open. A session with no
    /// video is not an error: is_open() is simply false and the panel says so.
    bool open(const std::filesystem::path& session_dir);
    void close();
    bool is_open() const { return cap_.isOpened(); }

    int  width()  const { return w_; }
    int  height() const { return h_; }
    int  frame_count() const { return n_; }

    /// Decode the frame the annotation calls `frame`. Cheap to call every UI frame: it
    /// returns immediately if that frame is already decoded.
    bool seek(int frame);
    int  current_frame() const { return cur_; }

    /// GL texture for the decoded frame, uploaded on demand.
    /// ImGui::Image takes (ImTextureID)(intptr_t)id.
    unsigned int texture();

private:
    bool decode_(int frame);

    cv::VideoCapture cap_;
    cv::Mat          img_;
    cv::Mat          rgb_;   ///< img_ widened to 3 channels for upload
    std::string      path_;
    int              cur_ = -1;
    int              w_ = 0, h_ = 0, n_ = 0;
    unsigned int     tex_ = 0;
    bool             tex_dirty_ = true;
};

} // namespace vbt::offline

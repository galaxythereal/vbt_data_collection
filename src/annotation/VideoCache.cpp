/**
 * @file VideoCache.cpp
 */
#include "annotation/VideoCache.h"
#include <spdlog/spdlog.h>

#ifndef GL_TEXTURE_SWIZZLE_RGBA
#define GL_TEXTURE_SWIZZLE_RGBA 0x8E46
#endif

namespace vbt {

VideoCache::~VideoCache() { close(); }

void VideoCache::close() {
    if (cap_.isOpened()) cap_.release();
    if (texture_id_ != 0) {
        glDeleteTextures(1, &texture_id_);
        texture_id_ = 0;
    }
    current_frame_.release();
    current_frame_idx_ = -1;
    frame_w_ = frame_h_ = frame_count_ = 0;
    index_ = nullptr;
}

bool VideoCache::open(const std::filesystem::path& session_dir, const VideoIndex& index) {
    close();
    auto path = session_dir / "camera" / "ir_video.mp4";
    if (!std::filesystem::exists(path)) {
        spdlog::warn("VideoCache: no video file at {}", path.string());
        return false;
    }
    if (!cap_.open(path.string())) {
        spdlog::error("VideoCache: cv::VideoCapture failed to open {}", path.string());
        return false;
    }
    frame_w_     = (int)cap_.get(cv::CAP_PROP_FRAME_WIDTH);
    frame_h_     = (int)cap_.get(cv::CAP_PROP_FRAME_HEIGHT);
    frame_count_ = (int)cap_.get(cv::CAP_PROP_FRAME_COUNT);
    index_ = &index;
    spdlog::info("VideoCache: opened {} ({}x{}, {} frames, index has {} entries)",
                 path.string(), frame_w_, frame_h_, frame_count_, index.size());
    return true;
}

bool VideoCache::decode_frame_at_(int frame_idx) {
    if (!cap_.isOpened()) return false;
    if (frame_idx < 0 || (frame_count_ > 0 && frame_idx >= frame_count_))
        frame_idx = std::max(0, std::min(frame_idx, frame_count_ - 1));
    // mp4v doesn't have exact frame seek but supports sequential read after
    // CAP_PROP_POS_FRAMES set. For modest forward jumps it's fast; for
    // reverse jumps we ask CAP_PROP_POS_FRAMES to do the keyframe rewind.
    if (frame_idx != current_frame_idx_ + 1) {
        cap_.set(cv::CAP_PROP_POS_FRAMES, (double)frame_idx);
    }
    cv::Mat frame;
    if (!cap_.read(frame) || frame.empty()) {
        // Some codecs return empty after a long jump; retry once.
        cap_.set(cv::CAP_PROP_POS_FRAMES, (double)frame_idx);
        if (!cap_.read(frame) || frame.empty()) return false;
    }
    current_frame_ = std::move(frame);
    current_frame_idx_ = frame_idx;
    texture_dirty_ = true;
    return true;
}

const cv::Mat* VideoCache::seek_to_time(double t_s) {
    if (!cap_.isOpened() || !index_ || index_->size() == 0) return nullptr;
    int frame_idx = index_->nearest_to(t_s);
    if (frame_idx == current_frame_idx_) return &current_frame_;
    if (!decode_frame_at_(frame_idx)) return nullptr;
    return &current_frame_;
}

unsigned int VideoCache::gl_texture() {
    if (current_frame_.empty()) return 0;
    if (texture_id_ == 0) glGenTextures(1, &texture_id_);
    glBindTexture(GL_TEXTURE_2D, texture_id_);
    if (texture_dirty_) {
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        if (current_frame_.channels() == 1) {
            GLint swizzle[] = {GL_RED, GL_RED, GL_RED, GL_ONE};
            glTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_RGBA, swizzle);
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB,
                         current_frame_.cols, current_frame_.rows, 0,
                         GL_RED, GL_UNSIGNED_BYTE, current_frame_.ptr());
        } else {
            // mp4v decodes BGR three-channel even when the source was Y8.
            glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB,
                         current_frame_.cols, current_frame_.rows, 0,
                         GL_BGR, GL_UNSIGNED_BYTE, current_frame_.ptr());
        }
        texture_dirty_ = false;
    }
    return texture_id_;
}

} // namespace vbt

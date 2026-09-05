#include "offline/SessionVideo.h"

#include <algorithm>

#include <GLFW/glfw3.h>

#include <opencv2/imgproc.hpp>

namespace fs = std::filesystem;

namespace vbt::offline {

SessionVideo::~SessionVideo() { close(); }

bool SessionVideo::open(const fs::path& session_dir) {
    close();
    const fs::path p = session_dir / "camera" / "ir_video.mp4";
    std::error_code ec;
    if (!fs::exists(p, ec)) return false;
    if (!cap_.open(p.string())) return false;
    path_ = p.string();
    w_ = (int)cap_.get(cv::CAP_PROP_FRAME_WIDTH);
    h_ = (int)cap_.get(cv::CAP_PROP_FRAME_HEIGHT);
    n_ = (int)cap_.get(cv::CAP_PROP_FRAME_COUNT);
    cur_ = -1;
    return true;
}

void SessionVideo::close() {
    if (tex_) { glDeleteTextures(1, &tex_); tex_ = 0; }
    if (cap_.isOpened()) cap_.release();
    img_.release();
    cur_ = -1; w_ = h_ = n_ = 0; tex_dirty_ = true; path_.clear();
}

bool SessionVideo::decode_(int frame) {
    if (!cap_.isOpened()) return false;
    if (n_ > 0) frame = std::max(0, std::min(frame, n_ - 1));
    else        frame = std::max(0, frame);

    // mp4v has no exact frame seek, but reading forward after setting POS_FRAMES works.
    // Stepping to the very next frame is the common case while scrubbing, and asking for
    // a seek there would make the decoder rewind to a keyframe for nothing.
    if (frame != cur_ + 1) cap_.set(cv::CAP_PROP_POS_FRAMES, (double)frame);

    cv::Mat f;
    if (!cap_.read(f) || f.empty()) {
        // some builds return empty right after a long jump; one retry settles it
        cap_.set(cv::CAP_PROP_POS_FRAMES, (double)frame);
        if (!cap_.read(f) || f.empty()) return false;
    }
    img_ = std::move(f);
    cur_ = frame;
    tex_dirty_ = true;
    return true;
}

bool SessionVideo::seek(int frame) {
    if (!cap_.isOpened()) return false;
    if (n_ > 0) frame = std::max(0, std::min(frame, n_ - 1));
    if (frame == cur_ && !img_.empty()) return true;
    return decode_(frame);
}

unsigned int SessionVideo::texture() {
    if (img_.empty()) return 0;
    if (tex_ == 0) glGenTextures(1, &tex_);
    glBindTexture(GL_TEXTURE_2D, tex_);
    if (tex_dirty_) {
        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_S, GL_CLAMP_TO_EDGE);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_WRAP_T, GL_CLAMP_TO_EDGE);
        // Texture swizzle is GL 3.3; this context is 2.1 on macOS, so mono IR is widened
        // to three channels here rather than in the sampler.
        if (img_.channels() == 1) cv::cvtColor(img_, rgb_, cv::COLOR_GRAY2BGR);
        else                      rgb_ = img_;
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, rgb_.cols, rgb_.rows, 0,
                     GL_BGR, GL_UNSIGNED_BYTE, rgb_.ptr());
        tex_dirty_ = false;
    }
    return tex_;
}

} // namespace vbt::offline

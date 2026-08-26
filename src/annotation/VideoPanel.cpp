/**
 * @file VideoPanel.cpp
 */
#include "annotation/VideoPanel.h"
#include <algorithm>
#include <cmath>

namespace vbt {

void VideoPanel::set_session(SessionData* s, VideoCache* cache) {
    session_ = s;
    cache_   = cache;
    playhead_t_s_ = (s && s->is_loaded()) ? s->t0_unified_s() : 0.0;
    playing_ = false;
}

double VideoPanel::render(double playhead_t_s, double t0_session) {
    playhead_t_s_ = playhead_t_s;
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("Load a session to view the IR video.");
        return playhead_t_s_;
    }

    if (ImGui::IsKeyPressed(ImGuiKey_Space, false) && !ImGui::GetIO().WantTextInput)
        playing_ = !playing_;

    // Auto-advance.
    if (playing_) {
        auto now = std::chrono::steady_clock::now();
        if (last_tick_.time_since_epoch().count() == 0) last_tick_ = now;
        double dt = std::chrono::duration<double>(now - last_tick_).count();
        last_tick_ = now;
        playhead_t_s_ += dt * speed_;
        if (playhead_t_s_ > session_->t_end_unified_s()) {
            playhead_t_s_ = session_->t_end_unified_s();
            playing_ = false;
        }
    } else {
        last_tick_ = {};
    }

    // Decode frame.
    if (cache_ && cache_->is_open()) {
        cache_->seek_to_time(playhead_t_s_);
        unsigned int tex = cache_->gl_texture();
        if (tex != 0 && cache_->current_frame_valid()) {
            float avail_w = std::max(1.0f, ImGui::GetContentRegionAvail().x);
            float avail_h = std::max(1.0f, ImGui::GetContentRegionAvail().y);
            float w = (float)cache_->width();
            float h = (float)cache_->height();
            float aspect = h > 0.0f ? w / h : 16.0f / 9.0f;
            float disp_w = avail_w;
            float disp_h = avail_w / aspect;
            if (disp_h > avail_h) {
                disp_h = avail_h;
                disp_w = avail_h * aspect;
            }
            ImVec2 cursor = ImGui::GetCursorPos();
            if (disp_w < avail_w)
                ImGui::SetCursorPosX(cursor.x + (avail_w - disp_w) * 0.5f);
            if (disp_h < avail_h)
                ImGui::SetCursorPosY(cursor.y + (avail_h - disp_h) * 0.5f);
            ImVec2 pos = ImGui::GetCursorScreenPos();
            ImGui::Image((void*)(intptr_t)tex, ImVec2(disp_w, disp_h));
            const ImVec2 after_image_cursor = ImGui::GetCursorScreenPos();
            draw_marker_overlay_(pos, ImVec2(disp_w, disp_h));
            draw_phase_badge_(pos, disp_w);

            auto* draw = ImGui::GetWindowDrawList();
            const ImVec2 controls_pos(pos.x + 8.0f, pos.y + 8.0f);
            const float controls_w = std::min(std::max(1.0f, disp_w - 16.0f), 360.0f);
            const float controls_h = ImGui::GetFrameHeight() + 8.0f;
            draw->AddRectFilled(
                ImVec2(controls_pos.x - 4.0f, controls_pos.y - 4.0f),
                ImVec2(controls_pos.x + controls_w, controls_pos.y + controls_h),
                IM_COL32(0, 0, 0, 135),
                4.0f);
            ImGui::SetCursorScreenPos(controls_pos);
            ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(4.0f, 1.0f));
            if (ImGui::SmallButton(playing_ ? "Pause##video_overlay" : "Play##video_overlay"))
                playing_ = !playing_;
            ImGui::SameLine();
            ImGui::SetNextItemWidth(52.0f);
            ImGui::SliderFloat("##video_speed", &speed_, 0.1f, 8.0f, "%.1fx",
                                ImGuiSliderFlags_Logarithmic);
            ImGui::SameLine();
            if (ImGui::SmallButton("|<<##video_prev_rep")) {
                int idx = current_rep_index_at_(playhead_t_s_);
                if (idx > 0)        playhead_t_s_ = rep_start_(session_->reps()[idx - 1]);
                else if (idx == 0)  playhead_t_s_ = rep_start_(session_->reps()[0]);
            }
            ImGui::SameLine();
            if (ImGui::SmallButton(">>|##video_next_rep")) {
                int idx = current_rep_index_at_(playhead_t_s_);
                int n   = (int)session_->reps().size();
                if (idx >= 0 && idx + 1 < n)
                    playhead_t_s_ = rep_start_(session_->reps()[idx + 1]);
            }
            ImGui::SameLine();
            if (ImGui::SmallButton("-1s##video_back_s")) seek_relative_seconds_(-1.0);
            ImGui::SameLine();
            if (ImGui::SmallButton("<##video_back_f")) seek_relative_frames_(-1);
            ImGui::SameLine();
            if (ImGui::SmallButton(">##video_next_f")) seek_relative_frames_(1);
            ImGui::SameLine();
            if (ImGui::SmallButton("+1s##video_forward_s")) seek_relative_seconds_(1.0);
            ImGui::SameLine();
            ImGui::TextDisabled("f %d/%d %.2fs",
                cache_ ? cache_->current_frame_index() : -1,
                cache_ ? cache_->frame_count() : 0,
                playhead_t_s_ - t0_session);
            ImGui::PopStyleVar();
            ImGui::SetCursorScreenPos(after_image_cursor);
        } else {
            ImGui::TextDisabled("Decoding frame…");
        }
    } else {
        ImGui::TextDisabled("No video bound to this session.");
    }
    return playhead_t_s_;
}

int VideoPanel::current_rep_index_at_(double t_s) const {
    if (!session_) return -1;
    const auto& reps = session_->reps();
    for (int i = 0; i < (int)reps.size(); ++i) {
        if (t_s >= rep_start_(reps[i]) && t_s <= rep_end_(reps[i])) return i;
    }
    return -1;
}

double VideoPanel::rep_start_(const RepAnnotation& rep) const {
    if (rep.t_start_s > 0.0) return rep.t_start_s;
    return std::min(rep.concentric.t_start_s, rep.eccentric.t_start_s);
}

double VideoPanel::rep_end_(const RepAnnotation& rep) const {
    if (rep.t_end_s > 0.0) return rep.t_end_s;
    return std::max({rep.concentric.t_end_s, rep.eccentric.t_end_s, rep.rest.t_end_s});
}

void VideoPanel::seek_relative_seconds_(double dt_s) {
    if (!session_ || !session_->is_loaded()) return;
    playhead_t_s_ = std::clamp(playhead_t_s_ + dt_s,
                               session_->t0_unified_s(),
                               session_->t_end_unified_s());
}

void VideoPanel::seek_relative_frames_(int frame_delta) {
    if (!session_ || !session_->is_loaded() || !cache_ || !cache_->is_open()) return;
    const auto& vi = session_->video_index();
    if (vi.size() == 0) return;
    int cur = cache_->current_frame_index();
    if (cur < 0) cur = vi.nearest_to(playhead_t_s_);
    if (cur < 0) return;
    const int next = std::clamp(cur + frame_delta, 0, (int)vi.size() - 1);
    playhead_t_s_ = vi.unified_t_s[next];
}

void VideoPanel::draw_marker_overlay_(const ImVec2& image_pos, const ImVec2& image_size) {
    if (!session_ || !cache_) return;
    const auto& m = session_->marker();
    if (m.size() == 0) return;
    // Find marker row closest to the current video frame's timestamp.
    double ts = playhead_t_s_;
    auto it = std::lower_bound(m.unified_t_s.begin(), m.unified_t_s.end(), ts);
    if (it == m.unified_t_s.end()) return;
    int idx = (int)(it - m.unified_t_s.begin());
    if (idx > 0 && std::fabs(m.unified_t_s[idx - 1] - ts)
                   < std::fabs(m.unified_t_s[idx] - ts)) idx--;
    if (idx < 0 || idx >= (int)m.size()) return;
    if (!m.detected[idx]) return;

    // Map IR-frame pixel coords (848x480 native) into the displayed image.
    float sx = image_size.x / (float)cache_->width();
    float sy = image_size.y / (float)cache_->height();
    auto draw = ImGui::GetWindowDrawList();

    // Trail.
    int trail_len = 30;
    int t0i = std::max(0, idx - trail_len);
    ImVec2 prev{};
    bool have_prev = false;
    for (int k = t0i; k <= idx; ++k) {
        if (!m.detected[k]) { have_prev = false; continue; }
        ImVec2 p(image_pos.x + m.pixel_u[k] * sx,
                 image_pos.y + m.pixel_v[k] * sy);
        float a = (float)(k - t0i) / std::max(1, idx - t0i);
        ImU32 col = IM_COL32(255, 220, 80, (int)(40 + 180 * a));
        if (have_prev) draw->AddLine(prev, p, col, 2.0f);
        prev = p;
        have_prev = true;
    }

    // Marker ring with confidence color.
    ImVec2 c(image_pos.x + m.pixel_u[idx] * sx,
             image_pos.y + m.pixel_v[idx] * sy);
    float conf = m.confidence[idx];
    ImU32 col;
    if      (conf >= 0.55f) col = IM_COL32(80,  255, 100, 230);
    else if (conf >= 0.40f) col = IM_COL32(255, 220, 60,  230);
    else                    col = IM_COL32(255, 80,  60,  230);
    draw->AddCircle(c, 14.0f, col, 24, 2.5f);
    draw->AddCircleFilled(c, 3.0f, col);

    // Quality pill above the ring.
    char buf[96];
    std::snprintf(buf, sizeof(buf),
                  "conf=%.2f  snr=%.1f  circ=%.2f  src=%s",
                  m.confidence[idx], m.snr[idx], m.circularity[idx],
                  m.depth_source[idx].c_str());
    ImVec2 tsz = ImGui::CalcTextSize(buf);
    ImVec2 tp(c.x - tsz.x / 2, c.y - 30);
    draw->AddRectFilled(ImVec2(tp.x - 4, tp.y - 2),
                        ImVec2(tp.x + tsz.x + 4, tp.y + tsz.y + 2),
                        IM_COL32(0, 0, 0, 180), 4.0f);
    draw->AddText(tp, IM_COL32(255, 255, 255, 240), buf);
}

void VideoPanel::draw_phase_badge_(const ImVec2& image_pos, float image_w) {
    if (!session_) return;
    const char* phase = "—";
    ImU32 col = IM_COL32(120, 120, 120, 220);
    int idx = current_rep_index_at_(playhead_t_s_);
    if (idx >= 0) {
        const auto& r = session_->reps()[idx];
        if (r.phase_order == "eccentric_first") {
            if (r.top_rest.t_end_s > r.top_rest.t_start_s + 1e-3
                && playhead_t_s_ <= r.top_rest.t_end_s) {
                phase = "TOP REST"; col = IM_COL32(150, 120, 255, 220);
            } else if (playhead_t_s_ <= r.eccentric.t_end_s) {
                phase = "ECCENTRIC"; col = IM_COL32(255, 100, 80, 220);
            } else if (r.bottom_rest.t_end_s > r.bottom_rest.t_start_s + 1e-3
                       && playhead_t_s_ <= r.bottom_rest.t_end_s) {
                phase = "BOTTOM REST"; col = IM_COL32(80, 180, 210, 220);
            } else if (playhead_t_s_ <= r.concentric.t_end_s) {
                phase = "CONCENTRIC"; col = IM_COL32(80, 140, 255, 220);
            } else {
                phase = "REST"; col = IM_COL32(160, 160, 160, 220);
            }
        } else {
            if (playhead_t_s_ <= r.concentric.t_end_s) {
                phase = "CONCENTRIC"; col = IM_COL32(80, 140, 255, 220);
            } else if (r.top_rest.t_end_s > r.top_rest.t_start_s + 1e-3
                       && playhead_t_s_ <= r.top_rest.t_end_s) {
                phase = "TOP REST"; col = IM_COL32(150, 120, 255, 220);
            } else if (playhead_t_s_ <= r.eccentric.t_end_s) {
                phase = "ECCENTRIC"; col = IM_COL32(255, 100, 80, 220);
            } else {
                phase = "REST"; col = IM_COL32(160, 160, 160, 220);
            }
        }
    }
    char text[64];
    std::snprintf(text, sizeof(text), "%s  R%d", phase,
                  idx >= 0 ? session_->reps()[idx].rep_id : -1);
    ImVec2 sz = ImGui::CalcTextSize(text);
    auto draw = ImGui::GetWindowDrawList();
    // Top-RIGHT of the video. The playback transport (play/pause, speed, frame step)
    // is anchored at the top-LEFT, so a left-anchored badge sat underneath it.
    const float pad = 10.0f, bw = sz.x + 16, bh = sz.y + 8;
    ImVec2 a(image_pos.x + image_w - pad - bw, image_pos.y + pad);
    ImVec2 b(a.x + bw, a.y + bh);
    draw->AddRectFilled(a, b, col, 4.0f);
    draw->AddText(ImVec2(a.x + 8, a.y + 4), IM_COL32(255, 255, 255, 240), text);
}

} // namespace vbt

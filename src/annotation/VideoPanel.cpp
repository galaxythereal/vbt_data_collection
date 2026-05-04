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

    // Transport bar.
    if (ImGui::Button(playing_ ? "Pause (Space)" : "Play (Space)"))
        playing_ = !playing_;
    if (ImGui::IsKeyPressed(ImGuiKey_Space, false)) playing_ = !playing_;
    ImGui::SameLine();
    ImGui::SetNextItemWidth(120);
    ImGui::SliderFloat("speed", &speed_, 0.1f, 8.0f, "%.2fx",
                        ImGuiSliderFlags_Logarithmic);
    ImGui::SameLine();
    if (ImGui::Button("|<<")) {
        // Jump to start of previous rep.
        int idx = current_rep_index_at_(playhead_t_s_);
        if (idx > 0)        playhead_t_s_ = session_->reps()[idx - 1].concentric.t_start_s;
        else if (idx == 0)  playhead_t_s_ = session_->reps()[0].concentric.t_start_s;
    }
    ImGui::SameLine();
    if (ImGui::Button(">>|")) {
        int idx = current_rep_index_at_(playhead_t_s_);
        int n   = (int)session_->reps().size();
        if (idx >= 0 && idx + 1 < n)
            playhead_t_s_ = session_->reps()[idx + 1].concentric.t_start_s;
    }
    ImGui::SameLine();
    // Step ±1 frame.
    if (ImGui::Button("<")) {
        if (cache_ && cache_->is_open()) {
            int cur = cache_->current_frame_index();
            const auto& vi = session_->video_index();
            if (cur > 0 && (size_t)(cur - 1) < vi.size())
                playhead_t_s_ = vi.unified_t_s[cur - 1];
        }
    }
    ImGui::SameLine();
    if (ImGui::Button(">")) {
        if (cache_ && cache_->is_open()) {
            int cur = cache_->current_frame_index();
            const auto& vi = session_->video_index();
            if (cur >= 0 && (size_t)(cur + 1) < vi.size())
                playhead_t_s_ = vi.unified_t_s[cur + 1];
        }
    }
    ImGui::SameLine();
    ImGui::TextDisabled("frame %d / %d   |   t = %.3f s",
        cache_ ? cache_->current_frame_index() : -1,
        cache_ ? cache_->frame_count() : 0,
        playhead_t_s_ - t0_session);

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
            float avail_w = ImGui::GetContentRegionAvail().x;
            float avail_h = std::max(120.0f, ImGui::GetContentRegionAvail().y - 40.0f);
            float w = (float)cache_->width();
            float h = (float)cache_->height();
            float aspect = w / h;
            float disp_w = avail_w;
            float disp_h = avail_w / aspect;
            if (disp_h > avail_h) {
                disp_h = avail_h;
                disp_w = avail_h * aspect;
            }
            ImVec2 pos = ImGui::GetCursorScreenPos();
            ImGui::Image((void*)(intptr_t)tex, ImVec2(disp_w, disp_h));
            draw_marker_overlay_(pos, ImVec2(disp_w, disp_h));
            draw_phase_badge_(pos);
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
        if (t_s >= reps[i].concentric.t_start_s
            && t_s <= reps[i].rest.t_end_s) return i;
    }
    return -1;
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

void VideoPanel::draw_phase_badge_(const ImVec2& image_pos) {
    if (!session_) return;
    const char* phase = "—";
    ImU32 col = IM_COL32(120, 120, 120, 220);
    int idx = current_rep_index_at_(playhead_t_s_);
    if (idx >= 0) {
        const auto& r = session_->reps()[idx];
        if      (playhead_t_s_ <= r.concentric.t_end_s) {
            phase = "CONCENTRIC"; col = IM_COL32(80, 140, 255, 220);
        } else if (playhead_t_s_ <= r.eccentric.t_end_s) {
            phase = "ECCENTRIC";  col = IM_COL32(255, 100, 80, 220);
        } else {
            phase = "REST";       col = IM_COL32(160, 160, 160, 220);
        }
    }
    char text[64];
    std::snprintf(text, sizeof(text), "%s  R%d", phase,
                  idx >= 0 ? session_->reps()[idx].rep_id : -1);
    ImVec2 sz = ImGui::CalcTextSize(text);
    auto draw = ImGui::GetWindowDrawList();
    ImVec2 a(image_pos.x + 10, image_pos.y + 10);
    ImVec2 b(a.x + sz.x + 16, a.y + sz.y + 8);
    draw->AddRectFilled(a, b, col, 4.0f);
    draw->AddText(ImVec2(a.x + 8, a.y + 4), IM_COL32(255, 255, 255, 240), text);
}

} // namespace vbt

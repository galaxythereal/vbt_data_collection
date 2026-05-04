/**
 * @file TimelinePanel.cpp
 */
#include "annotation/TimelinePanel.h"
#include <implot.h>
#include <algorithm>
#include <cmath>

namespace vbt {

void TimelinePanel::set_session(SessionData* s) {
    session_ = s;
    drag_rep_index_ = -1;
    drag_kind_ = DragKind::None;
    shift_drag_t0_.reset();
    made_edit_ = false;
    force_view_ = true;
    if (s && s->is_loaded()) {
        view_t_min_ = s->t0_unified_s();
        view_t_max_ = s->t_end_unified_s();
        playhead_t_s_ = view_t_min_;
    } else {
        view_t_min_ = view_t_max_ = playhead_t_s_ = 0.0;
    }
}

void TimelinePanel::center_on_rep(int rep_index) {
    if (!session_ || !session_->is_loaded()) return;
    if (rep_index < 0 || rep_index >= (int)session_->reps().size()) {
        view_t_min_ = session_->t0_unified_s();
        view_t_max_ = session_->t_end_unified_s();
    } else {
        const auto& r = session_->reps()[rep_index];
        double margin = 0.5 * std::max(0.5, r.rest.t_end_s - r.concentric.t_start_s);
        view_t_min_ = r.concentric.t_start_s - margin;
        view_t_max_ = r.rest.t_end_s + margin;
        playhead_t_s_ = 0.5 * (view_t_min_ + view_t_max_);
    }
    force_view_ = true;
}

double TimelinePanel::render(double playhead_t_s) {
    if (playhead_t_s != playhead_t_s_) playhead_t_s_ = playhead_t_s;
    if (!session_ || !session_->is_loaded()) {
        ImGui::TextDisabled("No session loaded");
        return 0.0;
    }
    // Clean signal must be ready before we plot.
    if (session_->mutable_marker().clean_dirty)
        session_->recompute_clean_signal(session_->clean_config());

    double t0 = session_->t0_unified_s();
    double t1 = session_->t_end_unified_s();

    if (force_view_) {
        ImPlot::SetNextAxesLimits(view_t_min_, view_t_max_,
                                  -2, 2, ImGuiCond_Always);
        force_view_ = false;
    }

    push_plot_style_();
    // Fill the parent child evenly between the two charts; the parent gives
    // us a fixed total height in AnnotationStudio so half-half is stable.
    const float avail_h = ImGui::GetContentRegionAvail().y;
    const float chart_h = std::max(120.0f, (avail_h - 24.0f) * 0.5f);
    render_position_plot_(t0, t1, chart_h);
    render_velocity_plot_(t0, t1, chart_h);
    pop_plot_style_();
    return playhead_t_s_;
}

void TimelinePanel::push_plot_style_() {
    auto& s = ImPlot::GetStyle();
    // Save just the values we override so pop restores them. ImPlot doesn't
    // have a generic Push for every var, so we mutate-and-restore.
    saved_line_weight_  = s.LineWeight;
    saved_marker_size_  = s.MarkerSize;
    saved_plot_padding_ = s.PlotPadding;
    s.LineWeight  = 2.4f;          // chunky, readable lines
    s.MarkerSize  = 4.0f;
    s.PlotPadding = ImVec2(10, 8);
}
void TimelinePanel::pop_plot_style_() {
    auto& s = ImPlot::GetStyle();
    s.LineWeight  = saved_line_weight_;
    s.MarkerSize  = saved_marker_size_;
    s.PlotPadding = saved_plot_padding_;
}

void TimelinePanel::render_position_plot_(double t0_session, double t1_session,
                                          float height) {
    if (!ImPlot::BeginPlot("Position (camera, world up)",
                            ImVec2(-1, height),
                            ImPlotFlags_NoLegend)) return;
    ImPlot::SetupAxes("time (s, since session start)",
                      "position up (m)",
                      ImPlotAxisFlags_None,
                      ImPlotAxisFlags_AutoFit);
    ImPlot::SetupAxisLimits(ImAxis_X1, view_t_min_ - t0_session,
                                       view_t_max_ - t0_session,
                                       force_view_ ? ImGuiCond_Always : ImGuiCond_Once);
    ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");
    ImPlot::SetupAxisFormat(ImAxis_Y1, "%.2f m");

    // Render bands FIRST so the position curve sits on top.
    render_rep_bands_();

    const auto& m = session_->marker();
    if (m.size() > 0 && !m.pos_up_clean_m.empty()) {
        static std::vector<float> xs, ys;
        xs.resize(m.size()); ys.resize(m.size());
        for (size_t i = 0; i < m.size(); ++i) {
            xs[i] = (float)(m.unified_t_s[i] - t0_session);
            ys[i] = m.pos_up_clean_m[i];
        }
        // Bright cyan with bold weight; clear against the dark plot bg.
        ImPlot::PushStyleColor(ImPlotCol_Line, ImVec4(0.30f, 0.85f, 1.00f, 1));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.6f);
        ImPlot::PlotLine("position",
                         xs.data(), ys.data(), (int)xs.size());
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
    }
    render_playhead_(view_t_min_ - t0_session, view_t_max_ - t0_session);

    if (ImPlot::IsPlotHovered()) {
        ImPlotPoint mp = ImPlot::GetPlotMousePos();
        double mouse_t = mp.x + t0_session;
        if (ImGui::IsMouseClicked(ImGuiMouseButton_Left) && !ImGui::GetIO().KeyShift) {
            int hit_rep; DragKind hit_kind;
            hit_test_boundaries_(mouse_t, 0.06, hit_rep, hit_kind);
            if (hit_kind != DragKind::None) {
                drag_rep_index_ = hit_rep;
                drag_kind_      = hit_kind;
            } else {
                playhead_t_s_ = mouse_t;
            }
        }
        if (ImGui::IsMouseDown(ImGuiMouseButton_Left)
            && drag_kind_ != DragKind::None) {
            commit_drag_(mouse_t);
        }
        if (ImGui::IsMouseReleased(ImGuiMouseButton_Left)) {
            drag_kind_ = DragKind::None;
            drag_rep_index_ = -1;
        }
        if (ImGui::GetIO().KeyShift) {
            if (ImGui::IsMouseClicked(ImGuiMouseButton_Left))
                shift_drag_t0_ = mouse_t;
            if (ImGui::IsMouseReleased(ImGuiMouseButton_Left) && shift_drag_t0_) {
                double a = std::min(*shift_drag_t0_, mouse_t);
                double b = std::max(*shift_drag_t0_, mouse_t);
                if (b - a > 0.2 && session_) {
                    RepAnnotation r;
                    r.rep_id = (int)session_->reps().size() + 1;
                    r.concentric.t_start_s = a;
                    r.concentric.t_end_s   = a + 0.4 * (b - a);
                    r.eccentric.t_start_s  = r.concentric.t_end_s;
                    r.eccentric.t_end_s    = a + 0.85 * (b - a);
                    r.rest.t_start_s       = r.eccentric.t_end_s;
                    r.rest.t_end_s         = b;
                    r.concentric.source = r.eccentric.source = r.rest.source = "manual";
                    session_->mutable_reps().push_back(r);
                    session_->recompute_rep_metrics((int)session_->reps().size() - 1);
                    session_->mark_reps_dirty();
                    made_edit_ = true;
                }
                shift_drag_t0_.reset();
            }
        }
    }

    ImPlot::EndPlot();
}

void TimelinePanel::render_velocity_plot_(double t0_session, double t1_session,
                                          float height) {
    if (!ImPlot::BeginPlot("Velocity (camera derivative + IMU |a|−1 overlay)",
                            ImVec2(-1, height),
                            ImPlotFlags_None)) return;
    ImPlot::SetupAxes("time (s)", "velocity (m/s)",
                       ImPlotAxisFlags_None,
                       ImPlotAxisFlags_AutoFit);
    ImPlot::SetupAxisLimits(ImAxis_X1, view_t_min_ - t0_session,
                                       view_t_max_ - t0_session,
                                       force_view_ ? ImGuiCond_Always : ImGuiCond_Once);
    ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");
    ImPlot::SetupAxisFormat(ImAxis_Y1, "%.2f m/s");
    // Second Y axis for the IMU |a|-1 trace so it doesn't get squashed by
    // the velocity range (and vice-versa). Hidden axis label keeps the
    // visual focus on velocity.
    ImPlot::SetupAxis(ImAxis_Y2, "|a|−1 (g)",
                       ImPlotAxisFlags_AuxDefault | ImPlotAxisFlags_Opposite);
    ImPlot::SetupAxisFormat(ImAxis_Y2, "%.2f g");
    ImPlot::SetupLegend(ImPlotLocation_NorthEast,
                        ImPlotLegendFlags_Outside);

    render_rep_bands_();

    const auto& m = session_->marker();
    if (m.size() > 0 && !m.vz_clean_mps.empty()) {
        static std::vector<float> xs, ys;
        xs.resize(m.size()); ys.resize(m.size());
        for (size_t i = 0; i < m.size(); ++i) {
            xs[i] = (float)(m.unified_t_s[i] - t0_session);
            ys[i] = m.vz_clean_mps[i];
        }
        // Bright lime; thicker; shaded fill to zero so direction reads at
        // a glance.
        ImPlot::PushStyleColor(ImPlotCol_Line,
                                ImVec4(0.40f, 0.95f, 0.40f, 1));
        ImPlot::PushStyleColor(ImPlotCol_Fill,
                                ImVec4(0.40f, 0.95f, 0.40f, 0.18f));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.6f);
        ImPlot::PlotShaded("velocity",
                            xs.data(), ys.data(), (int)xs.size(), 0.0);
        ImPlot::PlotLine("velocity",
                         xs.data(), ys.data(), (int)xs.size());
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor(2);
    }
    const auto& imu = session_->imu();
    if (imu.size() > 0) {
        static std::vector<float> xs, ys;
        const size_t step = std::max((size_t)1, imu.size() / 5000);
        xs.clear(); ys.clear();
        xs.reserve(imu.size() / step + 1);
        ys.reserve(imu.size() / step + 1);
        for (size_t i = 0; i < imu.size(); i += step) {
            float a = std::sqrt(imu.ax_g[i]*imu.ax_g[i]
                              + imu.ay_g[i]*imu.ay_g[i]
                              + imu.az_g[i]*imu.az_g[i]) - 1.0f;
            xs.push_back((float)(imu.unified_t_s[i] - t0_session));
            ys.push_back(a);
        }
        ImPlot::SetAxis(ImAxis_Y2);
        ImPlot::PushStyleColor(ImPlotCol_Line,
                                ImVec4(1.00f, 0.65f, 0.25f, 0.85f));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 1.4f);
        ImPlot::PlotLine("|a|−1 (g, IMU)",
                         xs.data(), ys.data(), (int)xs.size());
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
    }
    ImPlot::SetAxis(ImAxis_Y1);
    render_playhead_(view_t_min_ - t0_session, view_t_max_ - t0_session);

    ImPlot::EndPlot();
}

void TimelinePanel::render_rep_bands_() {
    if (!session_) return;
    double t0 = session_->t0_unified_s();
    const auto& reps = session_->reps();
    // Two-element scratch arrays reused across all bands. Top/bottom
    // are huge constants so the band fills the entire vertical plot
    // range regardless of axis auto-fit.
    float xs[2], lo[2] = {-1e6f, -1e6f}, hi[2] = { 1e6f,  1e6f};
    for (int i = 0; i < (int)reps.size(); ++i) {
        const auto& r = reps[i];
        bool sel = (i == selected_rep_);
        auto draw = [&](double a, double b, ImVec4 col, const char* tag) {
            if (b <= a) return;
            xs[0] = (float)(a - t0);
            xs[1] = (float)(b - t0);
            ImPlot::PushStyleColor(ImPlotCol_Fill, col);
            std::string id = std::string(tag) + std::to_string(i);
            ImPlot::PlotShaded(("##" + id).c_str(),
                                xs, lo, hi, 2);
            ImPlot::PopStyleColor();
        };
        // Slightly bolder fill alphas so bands read at a glance even on
        // dark backgrounds; selected rep glows brighter still.
        ImVec4 c_conc = ImVec4(0.25f, 0.55f, 1.00f, sel ? 0.40f : 0.20f);
        ImVec4 c_ecc  = ImVec4(1.00f, 0.42f, 0.32f, sel ? 0.40f : 0.20f);
        ImVec4 c_rest = ImVec4(0.55f, 0.55f, 0.55f, sel ? 0.25f : 0.12f);
        draw(r.concentric.t_start_s, r.concentric.t_end_s, c_conc, "conc");
        draw(r.eccentric.t_start_s,  r.eccentric.t_end_s,  c_ecc,  "ecc");
        if (r.rest.t_end_s > r.rest.t_start_s)
            draw(r.rest.t_start_s, r.rest.t_end_s, c_rest, "rest");

        // Bright outline at each editable boundary so users can see what's
        // grabbable. Skip if the boundary is offscreen.
        auto vline = [&](double t, ImVec4 col, float thickness) {
            float x = (float)(t - t0);
            float ys_top = (float)ImPlot::GetPlotLimits().Y.Max;
            float ys_bot = (float)ImPlot::GetPlotLimits().Y.Min;
            ImVec2 a = ImPlot::PlotToPixels(ImPlotPoint(x, ys_top));
            ImVec2 b = ImPlot::PlotToPixels(ImPlotPoint(x, ys_bot));
            ImGui::GetWindowDrawList()->AddLine(a, b,
                ImGui::ColorConvertFloat4ToU32(col), thickness);
        };
        vline(r.concentric.t_start_s, ImVec4(0.55f, 0.85f, 1.0f, sel?0.9f:0.55f), 1.5f);
        vline(r.concentric.t_end_s,   ImVec4(1.0f,  0.65f, 0.45f, sel?0.9f:0.55f), 1.5f);
        vline(r.eccentric.t_end_s,    ImVec4(0.85f, 0.85f, 0.85f, sel?0.9f:0.45f), 1.2f);
        if (r.rest.t_end_s > r.rest.t_start_s)
            vline(r.rest.t_end_s,
                   ImVec4(0.55f, 0.55f, 0.55f, sel?0.7f:0.35f), 1.0f);

        // Rep number label at the top of the concentric band.
        if (r.concentric.t_end_s > r.concentric.t_start_s) {
            ImPlotPoint pos((r.concentric.t_start_s + r.concentric.t_end_s) * 0.5 - t0,
                            ImPlot::GetPlotLimits().Y.Max * 0.93);
            ImVec2 px = ImPlot::PlotToPixels(pos);
            char tag[16];
            std::snprintf(tag, sizeof(tag), "R%d", r.rep_id);
            ImVec2 sz = ImGui::CalcTextSize(tag);
            ImVec2 a(px.x - sz.x * 0.5f - 5, px.y - 2);
            ImVec2 b(px.x + sz.x * 0.5f + 5, px.y + sz.y + 2);
            ImGui::GetWindowDrawList()->AddRectFilled(a, b,
                IM_COL32(0, 0, 0, sel ? 230 : 170), 4.0f);
            ImGui::GetWindowDrawList()->AddText(
                ImVec2(px.x - sz.x * 0.5f, px.y),
                IM_COL32(255, 255, 255, 240), tag);
        }
    }
}

void TimelinePanel::render_playhead_(double xmin, double xmax) {
    if (!session_) return;
    double t0 = session_->t0_unified_s();
    double xph = playhead_t_s_ - t0;
    if (xph < xmin || xph > xmax) return;
    // Drawn directly with the foreground draw list so the playhead is
    // always crisp and 2-pixel-thick — going through PlotLine made it
    // get clipped by the auto-fit Y range.
    auto* dl = ImGui::GetWindowDrawList();
    double y_top = ImPlot::GetPlotLimits().Y.Max;
    double y_bot = ImPlot::GetPlotLimits().Y.Min;
    ImVec2 a = ImPlot::PlotToPixels(ImPlotPoint(xph, y_top));
    ImVec2 b = ImPlot::PlotToPixels(ImPlotPoint(xph, y_bot));
    dl->AddLine(a, b, IM_COL32(255, 235, 80, 240), 2.0f);
    // Small triangle at the top so the playhead is unmistakable.
    dl->AddTriangleFilled(
        ImVec2(a.x - 6, a.y), ImVec2(a.x + 6, a.y),
        ImVec2(a.x,     a.y + 8), IM_COL32(255, 235, 80, 240));
}

void TimelinePanel::hit_test_boundaries_(double mouse_t, double tol_t,
                                          int& out_rep, DragKind& out_kind) const {
    out_rep = -1;
    out_kind = DragKind::None;
    if (!session_) return;
    const auto& reps = session_->reps();
    double best = tol_t;
    auto consider = [&](int i, double t, DragKind k) {
        double d = std::fabs(mouse_t - t);
        if (d < best) {
            best = d; out_rep = i; out_kind = k;
        }
    };
    for (int i = 0; i < (int)reps.size(); ++i) {
        const auto& r = reps[i];
        consider(i, r.concentric.t_start_s, DragKind::ConcentricStart);
        consider(i, r.concentric.t_end_s,   DragKind::ConcentricEnd);
        consider(i, r.eccentric.t_end_s,    DragKind::EccentricEnd);
        consider(i, r.rest.t_end_s,         DragKind::RestEnd);
    }
}

void TimelinePanel::commit_drag_(double new_t) {
    if (!session_) return;
    if (drag_rep_index_ < 0
        || drag_rep_index_ >= (int)session_->reps().size()) return;
    auto& r = session_->mutable_reps()[drag_rep_index_];
    auto clamp_min = [](double& x, double lo) { if (x < lo) x = lo; };
    auto clamp_max = [](double& x, double hi) { if (x > hi) x = hi; };
    switch (drag_kind_) {
        case DragKind::ConcentricStart:
            clamp_max(new_t, r.concentric.t_end_s - 0.05);
            r.concentric.t_start_s = new_t;
            break;
        case DragKind::ConcentricEnd:
            clamp_min(new_t, r.concentric.t_start_s + 0.05);
            clamp_max(new_t, r.eccentric.t_end_s - 0.05);
            r.concentric.t_end_s   = new_t;
            r.eccentric.t_start_s  = new_t;
            break;
        case DragKind::EccentricEnd:
            clamp_min(new_t, r.eccentric.t_start_s + 0.05);
            clamp_max(new_t, r.rest.t_end_s - 0.001);
            r.eccentric.t_end_s = new_t;
            r.rest.t_start_s    = new_t;
            break;
        case DragKind::RestEnd:
            clamp_min(new_t, r.rest.t_start_s + 0.001);
            r.rest.t_end_s = new_t;
            break;
        default: return;
    }
    session_->recompute_rep_metrics(drag_rep_index_);
    session_->mark_reps_dirty();
    made_edit_ = true;
}

void TimelinePanel::handle_rep_context_menu_(int rep_index) {
    if (rep_index < 0 || !session_) return;
    if (ImGui::BeginPopupContextItem(("##rep_ctx" + std::to_string(rep_index)).c_str())) {
        if (ImGui::MenuItem("Delete rep")) {
            session_->mutable_reps().erase(session_->mutable_reps().begin() + rep_index);
            session_->mark_reps_dirty();
            made_edit_ = true;
        }
        if (ImGui::MenuItem("Re-compute metrics")) {
            session_->recompute_rep_metrics(rep_index);
            session_->mark_reps_dirty();
            made_edit_ = true;
        }
        ImGui::EndPopup();
    }
}

} // namespace vbt

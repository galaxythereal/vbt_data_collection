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
        double rs = r.t_start_s > 0.0 ? r.t_start_s
                                      : std::min(r.concentric.t_start_s, r.eccentric.t_start_s);
        double re = r.t_end_s > 0.0 ? r.t_end_s : r.rest.t_end_s;
        double margin = 0.5 * std::max(0.5, re - rs);
        view_t_min_ = rs - margin;
        view_t_max_ = re + margin;
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
    {
        auto& m = session_->mutable_marker();
        const bool need_clean = m.clean_dirty
                             || m.pos_up_clean_m.size() != m.size()
                             || m.vz_clean_mps.size()   != m.size();
        if (need_clean)
            session_->recompute_clean_signal(session_->clean_config());
    }

    double t0 = session_->t0_unified_s();
    double t1 = session_->t_end_unified_s();

    if (last_handle_rep_ >= 0) render_keyboard_hint_();
    process_keyboard_nudge_();
    keep_force_view_next_frame_ = false;
    // NOTE: Do NOT call ImPlot::SetNextAxesLimits here.
    // It only applies to the *next* BeginPlot (Position), and our x-axis in
    // both plots is "seconds since session start" (t - t0_session). Setting
    // absolute unified-time limits here makes the position plot appear stuck
    // and the x-axis labels look like nonsense.

    push_plot_style_();
    // Toolbar/hint take a little vertical room; show proposals, position,
    // and velocity together so reviewers can trace phase labels against
    // both camera-derived signals without switching context.
    const float avail_h = std::max(1.0f, ImGui::GetContentRegionAvail().y);
    const float plot_gap = ImGui::GetStyle().ItemSpacing.y * 2.0f;
    const float usable_h = std::max(1.0f, avail_h - plot_gap);
    float rep_h = std::clamp(usable_h * 0.30f, 72.0f, 180.0f);
    float signal_h = (usable_h - rep_h) * 0.5f;
    if (signal_h < 58.0f) {
        rep_h = std::max(48.0f, usable_h * 0.26f);
        signal_h = std::max(32.0f, (usable_h - rep_h) * 0.5f);
    }
    const float plots_h = rep_h + signal_h * 2.0f;
    if (plots_h > usable_h && plots_h > 0.0f) {
        const float scale = usable_h / plots_h;
        rep_h = std::max(1.0f, rep_h * scale);
        signal_h = std::max(1.0f, signal_h * scale);
    }
    hover_handle_ = false;
    if (ImPlot::BeginAlignedPlots("##annotation_timeline_plots")) {
        render_rep_trace_plot_(t0, t1, rep_h);
        render_position_plot_(t0, t1, signal_h);
        render_velocity_plot_(t0, t1, signal_h);
        ImPlot::EndAlignedPlots();
    }
    if (hover_handle_)
        ImGui::SetMouseCursor(ImGuiMouseCursor_ResizeEW);
    pop_plot_style_();
    // Force-view ranges should apply to both plots for this frame.
    // We clear the flag after rendering so SetupAxisLimits(..., Always)
    // is used exactly once unless the studio re-asserts it (focus mode).
    if (!keep_force_view_next_frame_)
        force_view_ = false;
    return playhead_t_s_;
}

void TimelinePanel::render_keyboard_hint_() {
    if (last_handle_rep_ < 0 || !session_) {
        return;
    }
    const auto& reps = session_->reps();
    if (last_handle_rep_ >= (int)reps.size()) {
        last_handle_rep_ = -1;
        last_handle_kind_ = DragKind::None;
        return;
    }
    const char* tag = "?";
    switch (last_handle_kind_) {
        case DragKind::ConcentricStart: tag = "concentric START"; break;
        case DragKind::ConcentricEnd:   tag = "concentric END";   break;
        case DragKind::TopRestEnd:      tag = "top-rest END";     break;
        case DragKind::EccentricEnd:    tag = "eccentric END";    break;
        case DragKind::RestEnd:         tag = "rest END";         break;
        default: break;
    }
    ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f),
                        "R%d %s  | arrows nudge, Shift bigger, Esc clears",
                        reps[last_handle_rep_].rep_id, tag);
}

void TimelinePanel::process_keyboard_nudge_() {
    if (last_handle_rep_ < 0 || !session_) return;
    if (ImGui::GetIO().WantTextInput) return;
    if (ImGui::IsKeyPressed(ImGuiKey_Escape, false)) {
        last_handle_rep_ = -1;
        last_handle_kind_ = DragKind::None;
        return;
    }
    bool left  = ImGui::IsKeyPressed(ImGuiKey_LeftArrow,  true);
    bool right = ImGui::IsKeyPressed(ImGuiKey_RightArrow, true);
    if (!left && !right) return;
    double base = ImGui::GetIO().KeyShift ? 0.050 : 0.005;
    double dt   = (left ? -base : base);
    if (on_edit_begin_ && !pushed_this_drag_) {
        on_edit_begin_();
        pushed_this_drag_ = true;
    }
    apply_nudge_(last_handle_rep_, last_handle_kind_, dt);
    if (!ImGui::IsKeyDown(ImGuiKey_LeftArrow)
        && !ImGui::IsKeyDown(ImGuiKey_RightArrow))
        pushed_this_drag_ = false;
}

void TimelinePanel::apply_nudge_(int rep_i, DragKind kind, double dt) {
    if (!session_) return;
    auto& reps = session_->mutable_reps();
    if (rep_i < 0 || rep_i >= (int)reps.size()) return;
    auto& r = reps[rep_i];
    auto clamp = [](double& v, double lo, double hi) {
        if (v < lo) v = lo;
        if (v > hi) v = hi;
    };
    switch (kind) {
        case DragKind::ConcentricStart:
            r.concentric.t_start_s += dt;
            clamp(r.concentric.t_start_s, -1e9, r.concentric.t_end_s - 0.05);
            break;
        case DragKind::ConcentricEnd: {
            const bool collapsed = r.top_rest.t_end_s - r.top_rest.t_start_s < 0.005;
            r.concentric.t_end_s += dt;
            double hi = collapsed ? r.eccentric.t_end_s - 0.05
                                  : r.top_rest.t_end_s - 0.001;
            clamp(r.concentric.t_end_s, r.concentric.t_start_s + 0.05, hi);
            r.top_rest.t_start_s = r.concentric.t_end_s;
            if (collapsed) {
                r.top_rest.t_end_s    = r.concentric.t_end_s;
                r.eccentric.t_start_s = r.concentric.t_end_s;
            }
            break;
        }
        case DragKind::TopRestEnd:
            r.top_rest.t_end_s += dt;
            clamp(r.top_rest.t_end_s,
                  r.top_rest.t_start_s + 0.001, r.eccentric.t_end_s - 0.05);
            r.eccentric.t_start_s = r.top_rest.t_end_s;
            break;
        case DragKind::EccentricEnd: {
            const bool collapsed = r.rest.t_end_s - r.rest.t_start_s < 0.005;
            r.eccentric.t_end_s += dt;
            double hi = collapsed ? 1e9 : r.rest.t_end_s - 0.001;
            clamp(r.eccentric.t_end_s, r.eccentric.t_start_s + 0.05, hi);
            r.rest.t_start_s = r.eccentric.t_end_s;
            if (collapsed) r.rest.t_end_s = r.eccentric.t_end_s;
            break;
        }
        case DragKind::RestEnd:
            r.rest.t_end_s += dt;
            clamp(r.rest.t_end_s, r.rest.t_start_s + 0.001, 1e9);
            break;
        default: return;
    }
    session_->recompute_rep_metrics(rep_i);
    session_->mark_reps_dirty();
    made_edit_ = true;
}

void TimelinePanel::render_toolbar_() {
    ImGui::Checkbox("zero", &show_zerocross_);
    ImGui::SameLine();
    ImGui::Checkbox("peaks", &show_peaks_);
    ImGui::SameLine();
    // No fsync/IMU control — the Step-7 labeling UI is camera-only.
    ImGui::Checkbox("snap", &snap_to_zerocross_);
}

double TimelinePanel::maybe_snap_(double t) const {
    if (!snap_to_zerocross_ || !ImGui::GetIO().KeyAlt) return t;
    if (!session_) return t;
    const auto& m = session_->marker();
    if (m.zero_crossings.empty()) return t;
    double best_dt = 0.15;  // 150 ms search window
    double best_t  = t;
    for (int idx : m.zero_crossings) {
        double dt = std::fabs(m.unified_t_s[idx] - t);
        if (dt < best_dt) { best_dt = dt; best_t = m.unified_t_s[idx]; }
    }
    return best_t;
}

bool TimelinePanel::apply_boundary_wheel_zoom_(double t0_session,
                                               double focus_x_plot) {
    auto& io = ImGui::GetIO();
    if (io.MouseWheel == 0.0f) return false;
    if (ImGui::IsMouseDown(ImGuiMouseButton_Left)) return false;
    // If the plot itself is hovered, ImPlot's own wheel handling already
    // ran at BeginPlot time. This fallback is for boundary drag widgets that
    // own hover and otherwise leave the line under the cursor feeling stuck.
    if (ImPlot::IsPlotHovered()) return false;

    const ImPlotRect limits = ImPlot::GetPlotLimits();
    const double cur_min = limits.X.Min + t0_session;
    const double cur_max = limits.X.Max + t0_session;
    const double span = cur_max - cur_min;
    if (!(span > 1e-6)) return false;

    const double focus = focus_x_plot + t0_session;
    double factor = io.MouseWheel > 0.0f ? 0.87 : 1.15;
    constexpr double kMinSpanS = 0.020;
    if (span * factor < kMinSpanS)
        factor = kMinSpanS / span;

    view_t_min_ = focus - (focus - cur_min) * factor;
    view_t_max_ = focus + (cur_max - focus) * factor;
    force_view_ = true;
    keep_force_view_next_frame_ = true;
    return true;
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

void TimelinePanel::render_rep_trace_plot_(double t0_session, double t1_session,
                                           float height) {
    (void)t1_session;
    if (!ImPlot::BeginPlot("Proposal reps / phases",
                            ImVec2(-1, height),
                            ImPlotFlags_NoLegend | ImPlotFlags_NoMouseText)) return;
    ImPlot::SetupAxes("time (s, since session start)",
                      nullptr,
                      ImPlotAxisFlags_None,
                      ImPlotAxisFlags_NoDecorations | ImPlotAxisFlags_Lock);
    ImPlot::SetupAxisLimits(ImAxis_X1, view_t_min_ - t0_session,
                                       view_t_max_ - t0_session,
                                       force_view_ ? ImGuiCond_Always : ImGuiCond_Once);
    ImPlot::SetupAxisLimits(ImAxis_Y1, 0.0, 1.0, ImGuiCond_Always);
    ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");

    render_rep_bands_();
    render_imp_drag_lines_(t0_session);
    render_playhead_();

    if (ImPlot::IsPlotHovered()
        && ImGui::IsMouseClicked(ImGuiMouseButton_Left)
        && !ImGui::GetIO().KeyShift
        && !ImGui::IsAnyItemActive()
        && !ImGui::IsAnyItemHovered()) {
        ImPlotPoint mp = ImPlot::GetPlotMousePos();
        playhead_t_s_ = mp.x + t0_session;
    }

    sync_view_from_current_plot_(t0_session);
    ImPlot::EndPlot();
}

void TimelinePanel::render_position_plot_(double t0_session, double t1_session,
                                          float height) {
    const bool have_trace = session_ && session_->has_trace();
    if (!ImPlot::BeginPlot(have_trace ? "Position (pipeline s — labeling reference)"
                                      : "Position (camera, world up)",
                            ImVec2(-1, height),
                            ImPlotFlags_NoLegend)) return;
    ImPlot::SetupAxes("time (s, since session start)",
                      have_trace ? "s — segmentation coord (m)" : "position up (m)",
                      ImPlotAxisFlags_None,
                      ImPlotAxisFlags_AutoFit);
    ImPlot::SetupAxisLimits(ImAxis_X1, view_t_min_ - t0_session,
                                       view_t_max_ - t0_session,
                                       force_view_ ? ImGuiCond_Always : ImGuiCond_Once);
    ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");
    ImPlot::SetupAxisFormat(ImAxis_Y1, "%.2f m");

    const auto& m = session_->marker();
    if (have_trace) {
        // D2: the pipeline gravity-aligned `s` (trace.csv) is the labeling
        // reference — the signal the exported frame labels describe.
        const auto& tr = session_->trace();
        static std::vector<float> xs, ys;
        xs.resize(tr.size()); ys.resize(tr.size());
        for (size_t i = 0; i < tr.size(); ++i) {
            xs[i] = (float)(session_->time_for_frame(tr[i].frame_idx) - t0_session);
            ys[i] = (float)tr[i].s;
        }
        ImPlot::PushStyleColor(ImPlotCol_Line, ImVec4(0.30f, 0.85f, 1.00f, 1));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.6f);
        ImPlot::PlotLine("s (pipeline)", xs.data(), ys.data(), (int)xs.size());
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
    } else if (m.size() > 0 && m.pos_up_clean_m.size() == m.size()) {
        // fallback (from-scratch, no prefill): studio's own cleaned signal.
        static std::vector<float> xs, ys;
        xs.resize(m.size()); ys.resize(m.size());
        for (size_t i = 0; i < m.size(); ++i) {
            xs[i] = (float)(m.unified_t_s[i] - t0_session);
            ys[i] = m.pos_up_clean_m[i];
        }
        ImPlot::PushStyleColor(ImPlotCol_Line, ImVec4(0.30f, 0.85f, 1.00f, 1));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.6f);
        ImPlot::PlotLine("position", xs.data(), ys.data(), (int)xs.size());
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor();
    }
    render_detection_overlay_(t0_session, /*is_velocity_axis=*/false);
    render_playhead_();
    render_hover_tooltip_(t0_session);

    // Click on empty plot moves the playhead. Critically, we have to
    // suppress this when a DragLineX is being held — otherwise the very
    // first frame of a drag also fires this handler and the playhead
    // jumps to the cursor, which made the lines appear to "click but
    // not drag". ImGui::IsAnyItemActive() returns true while ImPlot's
    // internal drag-tool button is held, so it's the right gate here.
    if (ImPlot::IsPlotHovered()
        && ImGui::IsMouseClicked(ImGuiMouseButton_Left)
        && !ImGui::GetIO().KeyShift
        && !ImGui::IsAnyItemActive()
        && !ImGui::IsAnyItemHovered()) {
        ImPlotPoint mp = ImPlot::GetPlotMousePos();
        playhead_t_s_ = mp.x + t0_session;
    }
    // Shift+drag rep creation (legacy; toolbar Insert button is preferred).
    if (ImPlot::IsPlotHovered() && ImGui::GetIO().KeyShift) {
        ImPlotPoint mp = ImPlot::GetPlotMousePos();
        double mouse_t = mp.x + t0_session;
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
                // keep GT attributes aligned (append a default for the new rep)
                session_->mutable_gt_attrs().push_back(GtAttr{});
                session_->ensure_gt_attrs_aligned();
                session_->recompute_rep_metrics((int)session_->reps().size() - 1);
                session_->mark_reps_dirty();
                made_edit_ = true;
            }
            shift_drag_t0_.reset();
        }
    }

    sync_view_from_current_plot_(t0_session);
    ImPlot::EndPlot();
}

void TimelinePanel::render_velocity_plot_(double t0_session, double t1_session,
                                          float height) {
    // Camera-only labeling UI: NO IMU |a|−1 / fsync overlay (Step-7 correction).
    const bool have_trace = session_ && session_->has_trace();
    if (!ImPlot::BeginPlot(have_trace ? "Velocity (pipeline v — labeling reference)"
                                      : "Velocity (camera derivative)",
                            ImVec2(-1, height),
                            ImPlotFlags_NoLegend)) return;
    ImPlot::SetupAxes("time (s)", "velocity (m/s)",
                       ImPlotAxisFlags_None,
                       ImPlotAxisFlags_AutoFit);
    ImPlot::SetupAxisLimits(ImAxis_X1, view_t_min_ - t0_session,
                                       view_t_max_ - t0_session,
                                       force_view_ ? ImGuiCond_Always : ImGuiCond_Once);
    ImPlot::SetupAxisFormat(ImAxis_X1, "%.1f s");
    ImPlot::SetupAxisFormat(ImAxis_Y1, "%.2f m/s");

    const auto& m = session_->marker();
    const float* vx = nullptr; const float* vy = nullptr; int vn = 0;
    static std::vector<float> xs, ys;
    if (have_trace) {
        const auto& tr = session_->trace();
        xs.resize(tr.size()); ys.resize(tr.size());
        for (size_t i = 0; i < tr.size(); ++i) {
            xs[i] = (float)(session_->time_for_frame(tr[i].frame_idx) - t0_session);
            ys[i] = (float)tr[i].v;
        }
        vx = xs.data(); vy = ys.data(); vn = (int)xs.size();
    } else if (m.size() > 0 && m.vz_clean_mps.size() == m.size()) {
        xs.resize(m.size()); ys.resize(m.size());
        for (size_t i = 0; i < m.size(); ++i) {
            xs[i] = (float)(m.unified_t_s[i] - t0_session);
            ys[i] = m.vz_clean_mps[i];
        }
        vx = xs.data(); vy = ys.data(); vn = (int)xs.size();
    }
    if (vn > 0) {
        ImPlot::PushStyleColor(ImPlotCol_Line, ImVec4(0.40f, 0.95f, 0.40f, 1));
        ImPlot::PushStyleColor(ImPlotCol_Fill, ImVec4(0.40f, 0.95f, 0.40f, 0.18f));
        ImPlot::PushStyleVar(ImPlotStyleVar_LineWeight, 2.6f);
        ImPlot::PlotShaded("velocity", vx, vy, vn, 0.0);
        ImPlot::PlotLine("velocity", vx, vy, vn);
        ImPlot::PopStyleVar();
        ImPlot::PopStyleColor(2);
    }
    render_detection_overlay_(t0_session, /*is_velocity_axis=*/true);
    render_playhead_();

    if (ImPlot::IsPlotHovered()
        && ImGui::IsMouseClicked(ImGuiMouseButton_Left)
        && !ImGui::GetIO().KeyShift
        && !ImGui::IsAnyItemActive()
        && !ImGui::IsAnyItemHovered()) {
        ImPlotPoint mp = ImPlot::GetPlotMousePos();
        playhead_t_s_ = mp.x + t0_session;
    }

    sync_view_from_current_plot_(t0_session);
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
        ImVec4 c_top  = ImVec4(0.65f, 0.45f, 1.00f, sel ? 0.40f : 0.20f);
        ImVec4 c_ecc  = ImVec4(1.00f, 0.42f, 0.32f, sel ? 0.40f : 0.20f);
        ImVec4 c_bottom = ImVec4(0.35f, 0.75f, 0.90f, sel ? 0.34f : 0.16f);
        ImVec4 c_rest = ImVec4(0.55f, 0.55f, 0.55f, sel ? 0.25f : 0.12f);
        if (r.phase_order == "eccentric_first") {
            if (r.top_rest.t_end_s > r.top_rest.t_start_s + 1e-3)
                draw(r.top_rest.t_start_s, r.top_rest.t_end_s, c_top, "toprest");
            draw(r.eccentric.t_start_s,  r.eccentric.t_end_s,  c_ecc,  "ecc");
            if (r.bottom_rest.t_end_s > r.bottom_rest.t_start_s + 1e-3)
                draw(r.bottom_rest.t_start_s, r.bottom_rest.t_end_s, c_bottom, "bottomrest");
            draw(r.concentric.t_start_s, r.concentric.t_end_s, c_conc, "conc");
        } else {
            draw(r.concentric.t_start_s, r.concentric.t_end_s, c_conc, "conc");
            if (r.top_rest.t_end_s > r.top_rest.t_start_s + 1e-3)
                draw(r.top_rest.t_start_s, r.top_rest.t_end_s, c_top, "toprest");
            draw(r.eccentric.t_start_s,  r.eccentric.t_end_s,  c_ecc,  "ecc");
        }
        if (r.rest.t_end_s > r.rest.t_start_s + 1e-3)
            draw(r.rest.t_start_s, r.rest.t_end_s, c_rest, "rest");
        // Boundary outlines now drawn by render_drag_handles_ as visible
        // grip targets — no duplicate vlines here.

        // Rep number label over the whole rep envelope, not just the
        // concentric phase, so eccentric-first bench/squat reps read clearly.
        double rs = r.t_start_s > 0.0 ? r.t_start_s
                                      : std::min(r.concentric.t_start_s, r.eccentric.t_start_s);
        double re = r.t_end_s > 0.0 ? r.t_end_s : r.rest.t_end_s;
        if (re > rs) {
            ImPlotPoint pos((rs + re) * 0.5 - t0,
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

void TimelinePanel::render_playhead_() {
    if (!session_) return;
    double t0 = session_->t0_unified_s();
    double xph = playhead_t_s_ - t0;

    const ImPlotRect limits = ImPlot::GetPlotLimits(ImAxis_X1, ImAxis_Y1);
    if (!std::isfinite(xph) || xph < limits.X.Min || xph > limits.X.Max)
        return;

    const ImVec2 plot_pos = ImPlot::GetPlotPos();
    const ImVec2 plot_size = ImPlot::GetPlotSize();
    const ImVec2 plot_max(plot_pos.x + plot_size.x, plot_pos.y + plot_size.y);
    ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(xph, limits.Y.Max));
    const float x = std::round(px.x) + 0.5f;

    auto* dl = ImGui::GetForegroundDrawList();
    dl->PushClipRect(plot_pos, plot_max, true);
    // Shadow + bright core keeps the cursor visible over shaded rep bands,
    // dense velocity traces, and marker overlays.
    dl->AddLine(ImVec2(x, plot_pos.y), ImVec2(x, plot_max.y),
                IM_COL32(0, 0, 0, 180), 4.0f);
    dl->AddLine(ImVec2(x, plot_pos.y), ImVec2(x, plot_max.y),
                IM_COL32(255, 235, 80, 255), 2.5f);
    dl->AddTriangleFilled(
        ImVec2(x - 6, plot_pos.y), ImVec2(x + 6, plot_pos.y),
        ImVec2(x,     plot_pos.y + 8), IM_COL32(255, 235, 80, 255));
    dl->PopClipRect();
}

void TimelinePanel::sync_view_from_current_plot_(double t0_session) {
    if (!session_) return;
    const ImPlotRect limits = ImPlot::GetPlotLimits(ImAxis_X1, ImAxis_Y1);
    const double xmin = limits.X.Min + t0_session;
    const double xmax = limits.X.Max + t0_session;
    if (!std::isfinite(xmin) || !std::isfinite(xmax) || xmax <= xmin)
        return;
    view_t_min_ = xmin;
    view_t_max_ = xmax;
}

void TimelinePanel::hit_test_handle_pixels_(const ImVec2& mouse_px,
                                             double t0_session,
                                             int& out_rep,
                                             DragKind& out_kind) const {
    out_rep  = -1;
    out_kind = DragKind::None;
    if (!session_) return;
    const auto& reps = session_->reps();
    const float HIT_PX = 14.0f;
    float best = HIT_PX;
    auto consider = [&](int i, double t, DragKind k) {
        ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(t - t0_session, 0));
        // The handle is a vertical chevron column the full plot height,
        // so we test only the X-distance.
        float d = std::fabs(mouse_px.x - px.x);
        if (d < best) { best = d; out_rep = i; out_kind = k; }
    };
    for (int i = 0; i < (int)reps.size(); ++i) {
        const auto& r = reps[i];
        consider(i, r.concentric.t_start_s, DragKind::ConcentricStart);
        consider(i, r.concentric.t_end_s,   DragKind::ConcentricEnd);
        consider(i, r.eccentric.t_end_s,    DragKind::EccentricEnd);
        consider(i, r.rest.t_end_s,         DragKind::RestEnd);
    }
}

void TimelinePanel::render_drag_handles_(double t0_session) {
    if (!session_) return;
    const auto& reps = session_->reps();
    auto* dl = ImGui::GetWindowDrawList();
    auto pl_min = ImPlot::GetPlotPos();
    auto pl_size = ImPlot::GetPlotSize();
    float y_top = pl_min.y;
    float y_bot = pl_min.y + pl_size.y;

    ImVec2 mouse_px = ImGui::GetMousePos();
    auto draw_handle = [&](int rep_i, double t, ImU32 col, DragKind kind,
                            const char* tooltip) {
        ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(t - t0_session, 0));
        // Only draw if the line is inside the plot area.
        if (px.x < pl_min.x - 4 || px.x > pl_min.x + pl_size.x + 4) return;
        float dx = std::fabs(mouse_px.x - px.x);
        bool hovered = (dx < 14.0f) && mouse_px.y >= y_top && mouse_px.y <= y_bot;
        // Are we currently dragging this exact handle?
        bool active = (drag_rep_index_ == rep_i && drag_kind_ == kind);
        if (hovered) hover_handle_ = true;

        ImU32 line_col = active
            ? IM_COL32(255, 230, 90, 255)
            : (hovered ? IM_COL32(255, 255, 255, 230)
                       : col);
        float thick = (hovered || active) ? 2.5f : 1.6f;
        dl->AddLine(ImVec2(px.x, y_top), ImVec2(px.x, y_bot), line_col, thick);
        // Top and bottom chevron grips so the hit zone is unmistakable.
        const float r = 6.0f;
        ImU32 fill = (hovered || active)
            ? IM_COL32(255, 235, 100, 240) : col;
        ImU32 ring = IM_COL32(20, 20, 20, 220);
        dl->AddCircleFilled(ImVec2(px.x, y_top + r + 2), r, fill);
        dl->AddCircle      (ImVec2(px.x, y_top + r + 2), r, ring, 0, 1.5f);
        dl->AddCircleFilled(ImVec2(px.x, y_bot - r - 2), r, fill);
        dl->AddCircle      (ImVec2(px.x, y_bot - r - 2), r, ring, 0, 1.5f);
        if (hovered && tooltip) {
            ImGui::BeginTooltip();
            ImGui::Text("%s", tooltip);
            ImGui::TextDisabled("t = %.3f s%s", t - t0_session,
                snap_to_zerocross_ ? "  (Alt = snap to zero-crossing)" : "");
            ImGui::EndTooltip();
        }
    };
    for (int i = 0; i < (int)reps.size(); ++i) {
        const auto& r = reps[i];
        char b1[32], b2[32], b3[32], b4[32];
        std::snprintf(b1, sizeof(b1), "R%d  concentric START", r.rep_id);
        std::snprintf(b2, sizeof(b2), "R%d  concentric END",   r.rep_id);
        std::snprintf(b3, sizeof(b3), "R%d  eccentric END",    r.rep_id);
        std::snprintf(b4, sizeof(b4), "R%d  rest END",         r.rep_id);
        draw_handle(i, r.concentric.t_start_s,
                    IM_COL32( 80, 200, 255, 230), DragKind::ConcentricStart, b1);
        draw_handle(i, r.concentric.t_end_s,
                    IM_COL32(255, 170, 100, 230), DragKind::ConcentricEnd,   b2);
        draw_handle(i, r.eccentric.t_end_s,
                    IM_COL32(220, 220, 220, 230), DragKind::EccentricEnd,    b3);
        if (r.rest.t_end_s > r.rest.t_start_s)
            draw_handle(i, r.rest.t_end_s,
                        IM_COL32(150, 150, 150, 230), DragKind::RestEnd,     b4);
    }
}

void TimelinePanel::render_imp_drag_lines_(double t0_session) {
    if (!session_) return;
    auto& reps = session_->mutable_reps();
    drag_rep_index_ = -1;
    const ImVec2 plot_pos = ImPlot::GetPlotPos();
    const ImVec2 plot_size = ImPlot::GetPlotSize();
    const ImVec2 mouse = ImGui::GetMousePos();
    const bool mouse_y_in_plot =
        mouse.y >= plot_pos.y && mouse.y <= plot_pos.y + plot_size.y;
    bool boundary_wheel_handled = false;
    // 5 boundary lines per rep × up to 1024 reps fits in int id space.
    auto lineid = [](int rep_i, int kind_i) { return rep_i * 8 + kind_i + 1; };
    for (int i = 0; i < (int)reps.size(); ++i) {
        auto& r = reps[i];
        bool sel = (i == selected_rep_);
        struct B { double* val; ImVec4 col; const char* tag; double lo; double hi;
                   bool optional; };
        // Zero-width carry: when top_rest or rest are collapsed, the user
        // shouldn't have to drag three handles to move a boundary. We
        // widen the upper clamp so the drag is unblocked, and on commit
        // we carry the collapsed band with the moving boundary (see
        // below). Same idea for rest_end.
        const bool top_collapsed  =
            r.top_rest.t_end_s - r.top_rest.t_start_s < 0.005;
        const bool rest_collapsed =
            r.rest.t_end_s     - r.rest.t_start_s     < 0.005;
        const double conc_end_hi  = top_collapsed
            ? r.eccentric.t_end_s - 0.05
            : r.top_rest.t_end_s - 0.001;
        const double ecc_end_hi   = rest_collapsed
            ? 1e9
            : r.rest.t_end_s - 0.001;
        std::array<B, 5> bs = {{
            // 0: concentric START (left end of concentric)
            {&r.concentric.t_start_s, ImVec4(0.30f, 0.80f, 1.0f, 1.0f), "conc start",
                -1e9, r.concentric.t_end_s - 0.05, /*optional=*/false},
            // 1: concentric END = top_rest START (paired)
            {&r.concentric.t_end_s,   ImVec4(1.0f, 0.65f, 0.35f, 1.0f), "conc end",
                r.concentric.t_start_s + 0.05, conc_end_hi, false},
            // 2: top_rest END = eccentric START (paired). Hidden when zero-width.
            {&r.top_rest.t_end_s,     ImVec4(0.75f, 0.55f, 1.0f, 1.0f), "top-rest end",
                r.top_rest.t_start_s + 0.001, r.eccentric.t_end_s - 0.05, true},
            // 3: eccentric END = rest START (paired)
            {&r.eccentric.t_end_s,    ImVec4(0.85f, 0.85f, 0.85f, 1.0f), "ecc end",
                r.eccentric.t_start_s + 0.05, ecc_end_hi, false},
            // 4: rest END
            {&r.rest.t_end_s,         ImVec4(0.55f, 0.55f, 0.55f, 1.0f), "rest end",
                r.rest.t_start_s + 0.001, 1e9, true},
        }};
        for (int k = 0; k < 5; ++k) {
            // Optional handles: top_rest_end is hidden if top_rest has
            // ~zero duration AND none of its boundaries is the active
            // last_handle (so the user can still nudge it back open from
            // a zero-width state via the rep table editor). Same pattern
            // for rest_end on back-to-back reps.
            if (bs[k].optional) {
                bool zero_width =
                    (k == 2 && r.top_rest.t_end_s <= r.top_rest.t_start_s + 1e-3)
                 || (k == 4 && r.rest.t_end_s     <= r.rest.t_start_s     + 1e-3);
                bool is_active_handle =
                    (last_handle_rep_ == i && (DragKind)(k + 1) == last_handle_kind_);
                if (zero_width && !is_active_handle) continue;
            }
            double x_prev = *bs[k].val - t0_session;
            double x = x_prev;
            ImVec4 col = bs[k].col;
            if (sel) col.w = 1.0f; else col.w = 0.85f;
            float thickness = sel ? 3.0f : 2.0f;
            const float x_px =
                ImPlot::PlotToPixels(ImPlotPoint(x_prev, 0)).x;
            const float wheel_hit_px = std::max(10.0f, thickness * 4.0f);
            if (!boundary_wheel_handled
                && mouse_y_in_plot
                && std::fabs(mouse.x - x_px) <= wheel_hit_px) {
                boundary_wheel_handled =
                    apply_boundary_wheel_zoom_(t0_session, x_prev);
            }
            bool changed = ImPlot::DragLineX(lineid(i, k), &x, col, thickness,
                                              ImPlotDragToolFlags_None);
            if (changed) {
                // Coalesce frame-by-frame DragLineX updates into one undo
                // step: snapshot only on the first edit after the mouse
                // was last released. The studio resets pushed_this_drag_
                // to false on mouse-up.
                if (!pushed_this_drag_ && on_edit_begin_) {
                    on_edit_begin_();
                    pushed_this_drag_ = true;
                }
                double new_t = t0_session + x;
                if (snap_to_zerocross_ && ImGui::GetIO().KeyAlt)
                    new_t = maybe_snap_(t0_session + x);
                if (new_t < bs[k].lo) new_t = bs[k].lo;
                if (new_t > bs[k].hi) new_t = bs[k].hi;
                *bs[k].val = new_t;
                // Pair the lockstep boundaries — drag commits update both
                // endpoints of the paired phase boundary atomically. When
                // the adjacent rest band was zero-width, slide it with
                // the dragged boundary so the user can move concentric→
                // eccentric in one drag without first opening top_rest.
                if (k == 1) {
                    r.top_rest.t_start_s = *bs[k].val;
                    if (top_collapsed) {
                        r.top_rest.t_end_s    = *bs[k].val;
                        r.eccentric.t_start_s = *bs[k].val;
                    }
                }
                if (k == 2) r.eccentric.t_start_s = *bs[k].val;
                if (k == 3) {
                    r.rest.t_start_s = *bs[k].val;
                    if (rest_collapsed)
                        r.rest.t_end_s = *bs[k].val;
                }
                session_->recompute_rep_metrics(i);
                session_->mark_reps_dirty();
                made_edit_ = true;
                drag_rep_index_ = i;     // suppress click-to-seek
                // Remember which boundary the user last touched so the
                // arrow-key nudge in process_keyboard_nudge_() acts on it.
                last_handle_rep_  = i;
                last_handle_kind_ = (DragKind)(k + 1);  // 0=None offset
            }
        }
    }
    // When the mouse is released, the next drag is a new undo step.
    if (ImGui::IsMouseReleased(ImGuiMouseButton_Left))
        pushed_this_drag_ = false;
}

void TimelinePanel::render_detection_overlay_(double t0_session, bool is_velocity_axis) {
    if (!session_) return;
    const auto& m = session_->marker();
    if (m.size() == 0) return;
    if (is_velocity_axis) {
        if (m.vz_clean_mps.size() != m.size()) return;
    } else {
        if (m.pos_up_clean_m.size() != m.size()) return;
    }
    auto* dl = ImGui::GetWindowDrawList();
    if (show_zerocross_) {
        ImU32 col = IM_COL32(255, 235, 60, 230);
        for (int idx : m.zero_crossings) {
            if (idx < 0 || idx >= (int)m.size()) continue;
            float x = (float)(m.unified_t_s[idx] - t0_session);
            float y = is_velocity_axis ? 0.0f : m.pos_up_clean_m[idx];
            ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(x, y));
            dl->AddCircleFilled(px, 4.0f, col);
            dl->AddCircle      (px, 4.0f, IM_COL32(40, 40, 40, 240), 0, 1.0f);
        }
    }
    if (show_peaks_) {
        // Positive peaks (concentric peak velocity).
        ImU32 col_pos = IM_COL32(120, 255, 120, 240);
        for (int idx : m.peak_vel_pos_idx) {
            if (idx < 0 || idx >= (int)m.size()) continue;
            float x = (float)(m.unified_t_s[idx] - t0_session);
            float y = is_velocity_axis ? m.vz_clean_mps[idx] : m.pos_up_clean_m[idx];
            ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(x, y));
            dl->AddTriangleFilled(
                ImVec2(px.x - 6, px.y + 6),
                ImVec2(px.x + 6, px.y + 6),
                ImVec2(px.x,     px.y - 6), col_pos);
            dl->AddTriangle(
                ImVec2(px.x - 6, px.y + 6),
                ImVec2(px.x + 6, px.y + 6),
                ImVec2(px.x,     px.y - 6), IM_COL32(20, 60, 20, 240), 1.0f);
        }
        // Negative peaks (eccentric peak velocity, downward triangle).
        ImU32 col_neg = IM_COL32(255, 130, 130, 240);
        for (int idx : m.peak_vel_neg_idx) {
            if (idx < 0 || idx >= (int)m.size()) continue;
            float x = (float)(m.unified_t_s[idx] - t0_session);
            float y = is_velocity_axis ? m.vz_clean_mps[idx] : m.pos_up_clean_m[idx];
            ImVec2 px = ImPlot::PlotToPixels(ImPlotPoint(x, y));
            dl->AddTriangleFilled(
                ImVec2(px.x - 6, px.y - 6),
                ImVec2(px.x + 6, px.y - 6),
                ImVec2(px.x,     px.y + 6), col_neg);
            dl->AddTriangle(
                ImVec2(px.x - 6, px.y - 6),
                ImVec2(px.x + 6, px.y - 6),
                ImVec2(px.x,     px.y + 6), IM_COL32(60, 20, 20, 240), 1.0f);
        }
    }
    // FSYNC/IMU overlay removed — the Step-7 ground-truth labeling UI is
    // camera-only and never reads session_->imu().
}

void TimelinePanel::render_hover_tooltip_(double t0_session) {
    if (!session_) return;
    if (!ImPlot::IsPlotHovered()) return;
    if (drag_kind_ != DragKind::None) return;  // suppressed during drag
    const auto& m = session_->marker();
    if (m.size() == 0) return;
    if (m.pos_up_clean_m.size() != m.size() || m.vz_clean_mps.size() != m.size())
        return;
    ImPlotPoint mp = ImPlot::GetPlotMousePos();
    double t = mp.x + t0_session;
    auto it = std::lower_bound(m.unified_t_s.begin(), m.unified_t_s.end(), t);
    if (it == m.unified_t_s.end()) return;
    int idx = (int)(it - m.unified_t_s.begin());
    if (idx > 0 && std::fabs(m.unified_t_s[idx - 1] - t)
                   < std::fabs(m.unified_t_s[idx] - t)) idx--;
    if (idx < 0 || idx >= (int)m.size()) return;
    ImGui::BeginTooltip();
    ImGui::Text("t = %.3f s   (frame %d)",
                 m.unified_t_s[idx] - t0_session, idx);
    ImGui::Text("position : %.3f m", m.pos_up_clean_m[idx]);
    ImGui::Text("velocity : %.3f m/s", m.vz_clean_mps[idx]);
    ImGui::Separator();
    ImGui::TextDisabled("conf %.2f · snr %.1f · circ %.2f",
                         m.confidence[idx], m.snr[idx], m.circularity[idx]);
    ImGui::EndTooltip();
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
            // erase the matching GT attribute at the same index
            if (rep_index < (int)session_->gt_attrs().size())
                session_->mutable_gt_attrs().erase(
                    session_->mutable_gt_attrs().begin() + rep_index);
            session_->ensure_gt_attrs_aligned();
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

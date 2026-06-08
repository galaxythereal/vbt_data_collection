/**
 * @file AnnotationStudio.cpp
 */
#include "annotation/AnnotationStudio.h"
#include "annotation/Persistence.h"
#include "annotation/GroundTruthIO.h"
#include "app/Application.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <spdlog/spdlog.h>
#include <algorithm>
#include <cmath>
#include <filesystem>

namespace vbt {

namespace { namespace fs = std::filesystem; }

AnnotationStudio::AnnotationStudio(Application& app)
    : app_(app)
    , library_(app.config().dataset_root) {
    rep_table_.set_select_cb([this](int i){ select_rep_(i); });
    rep_table_.set_edit_begin_cb([this](){ push_undo_(); });
    quality_panel_.set_seek_cb([this](double t){ seek_(t); });
    timeline_.set_edit_begin_cb([this](){ push_undo_(); });
}

void AnnotationStudio::open() {
    is_open_ = true;
    library_.set_root(app_.config().dataset_root);
    library_.refresh();
}

void AnnotationStudio::render() {
    if (!is_open_) return;
    // Keep the GT attribute store sized to the rep list (cheap; guards every
    // panel that indexes gt_attrs() by rep after an insert/delete/undo).
    if (session_.is_loaded()) session_.ensure_gt_attrs_aligned();
    const ImGuiViewport* viewport = ImGui::GetMainViewport();
    const ImVec2 work_pos = viewport ? viewport->WorkPos : ImVec2(0, 0);
    const ImVec2 work_size = viewport ? viewport->WorkSize : ImGui::GetIO().DisplaySize;
    const float screen_margin = 4.0f;
    const ImVec2 max_window_size(
        std::max(1.0f, work_size.x - screen_margin * 2.0f),
        std::max(1.0f, work_size.y - screen_margin * 2.0f));

    ImGui::SetNextWindowPos(
        ImVec2(work_pos.x + screen_margin, work_pos.y + screen_margin),
        ImGuiCond_Always);
    ImGui::SetNextWindowSize(max_window_size, ImGuiCond_Always);
    if (!ImGui::Begin("Annotation Studio", &is_open_,
                      ImGuiWindowFlags_NoSavedSettings |
                      ImGuiWindowFlags_NoTitleBar |
                      ImGuiWindowFlags_NoMove |
                      ImGuiWindowFlags_NoResize |
                      ImGuiWindowFlags_HorizontalScrollbar)) {
        ImGui::End();
        return;
    }

    // Hotkeys (skip when typing in a text field).
    auto& io = ImGui::GetIO();
    if (!io.WantTextInput) {
        if (ImGui::IsKeyPressed(ImGuiKey_S, false) && io.KeyCtrl)
            show_save_dialog_ = session_.dirty();
        if (ImGui::IsKeyPressed(ImGuiKey_Z, false) && io.KeyCtrl) undo_();
        else if (ImGui::IsKeyPressed(ImGuiKey_Y, false) && io.KeyCtrl) redo_();
        else if (ImGui::IsKeyPressed(ImGuiKey_Z, false) && !session_.reps().empty()) {
            // 'Z' alone = previous rep (legacy; conflicts with Ctrl+Z but Ctrl
            // takes precedence above). Documented in the help tooltip.
            int s = rep_table_.selected_index();
            select_rep_(std::max(0, s - 1));
        }
        if (ImGui::IsKeyPressed(ImGuiKey_X, false) && !io.KeyCtrl
            && !session_.reps().empty()) {
            int s = rep_table_.selected_index();
            select_rep_(std::min((int)session_.reps().size() - 1, s + 1));
        }
        if (ImGui::IsKeyPressed(ImGuiKey_F, false) && io.KeyCtrl)
            focus_mode_ = !focus_mode_;
        if (ImGui::IsKeyPressed(ImGuiKey_L, false) && io.KeyCtrl)
            show_library_ = !show_library_;
        if (ImGui::IsKeyPressed(ImGuiKey_Insert, false))
            insert_rep_at_(playhead_t_s_);
        if (ImGui::IsKeyPressed(ImGuiKey_Delete, false)
            && rep_table_.selected_index() >= 0) {
            push_undo_();
            int s = rep_table_.selected_index();
            session_.mutable_reps().erase(session_.mutable_reps().begin() + s);
            if (s < (int)session_.gt_attrs().size())
                session_.mutable_gt_attrs().erase(session_.mutable_gt_attrs().begin() + s);
            session_.ensure_gt_attrs_aligned();
            session_.mark_reps_dirty();
        }
    }

    render_top_toolbar_();

    // 3-pane layout via child windows. The studio window itself is clamped
    // to the viewport; this body child owns overflow, so narrow displays get
    // scrollbars instead of a giant off-screen tool surface.
    ImGui::BeginChild("##studio_body", ImVec2(0, 0), false,
                      ImGuiWindowFlags_HorizontalScrollbar);
    const float total_w = std::max(1.0f, ImGui::GetContentRegionAvail().x);
    const float total_h = std::max(1.0f, ImGui::GetContentRegionAvail().y);
    const float spacing_x = ImGui::GetStyle().ItemSpacing.x;
    const bool show_library_restore = !show_library_;
    const int visible_panes = 1 + (show_library_ ? 1 : 0)
                                + (show_library_restore ? 1 : 0)
                                + 1;
    const float gaps = spacing_x * std::max(0, visible_panes - 1);
    float left_w  = show_library_  ? std::clamp(total_w * 0.22f, 180.0f, 320.0f) : 0.0f;
    float cards_w = std::clamp(total_w * 0.16f, 132.0f, 220.0f);
    const float library_restore_w = show_library_restore
                                  ? std::clamp(total_w * 0.06f, 68.0f, 92.0f)
                                  : 0.0f;
    const float min_center_w = std::min(360.0f, total_w);
    const float side_budget = std::max(0.0f,
        total_w - min_center_w - gaps - library_restore_w);
    const float side_sum = left_w + cards_w;
    if (side_sum > side_budget && side_sum > 0.0f) {
        const float scale = side_budget / side_sum;
        left_w *= scale;
        cards_w *= scale;
    }
    const bool draw_library = show_library_ && left_w >= 1.0f;
    const bool draw_library_restore = show_library_restore && library_restore_w >= 1.0f;
    const bool draw_cards = cards_w >= 1.0f;
    const float center_w = std::max(min_center_w,
        total_w - (draw_library ? left_w : 0.0f)
                - (draw_library_restore ? library_restore_w : 0.0f)
                - (draw_cards ? cards_w : 0.0f)
                - gaps);

    if (draw_library) {
        ImGui::BeginChild("##studio_left", ImVec2(left_w, total_h), true,
                          ImGuiWindowFlags_HorizontalScrollbar);
        render_session_browser_();
        ImGui::EndChild();
        ImGui::SameLine();
    }
    if (draw_library_restore) {
        ImGui::BeginChild("##studio_library_restore", ImVec2(library_restore_w, total_h), true,
                          ImGuiWindowFlags_NoScrollbar |
                          ImGuiWindowFlags_NoScrollWithMouse);
        ImGui::TextDisabled("Sessions");
        const float button_w = ImGui::GetContentRegionAvail().x;
        if (ImGui::Button("Show##library_restore", ImVec2(button_w, 0.0f))) {
            show_library_ = true;
        }
        if (ImGui::IsItemHovered()) {
            ImGui::BeginTooltip();
            ImGui::TextUnformatted("Show session browser (Ctrl+L)");
            ImGui::EndTooltip();
        }
        ImGui::EndChild();
        ImGui::SameLine();
    }
    ImGui::BeginChild("##studio_center", ImVec2(center_w, total_h), false,
                      ImGuiWindowFlags_NoScrollbar |
                      ImGuiWindowFlags_NoScrollWithMouse);
    render_workspace_();
    ImGui::EndChild();

    if (draw_cards) {
        ImGui::SameLine();
        ImGui::BeginChild("##studio_rep_cards", ImVec2(cards_w, total_h), true,
                          ImGuiWindowFlags_HorizontalScrollbar);
        render_summary_cards_();
        ImGui::EndChild();
    }
    ImGui::EndChild();

    render_save_dialog_();
    render_use_proposal_dialog_();
    ImGui::End();
}

void AnnotationStudio::render_session_browser_() {
    ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1), "Sessions");
    ImGui::SameLine();
    if (ImGui::SmallButton("Hide##library_panel")) {
        show_library_ = false;
    }
    if (ImGui::IsItemHovered()) {
        ImGui::BeginTooltip();
        ImGui::TextUnformatted("Hide the session browser and give the workspace more room.");
        ImGui::EndTooltip();
    }
    ImGui::Separator();
    char qbuf[256];
    std::snprintf(qbuf, sizeof(qbuf), "%s", filter_query_.c_str());
    if (ImGui::InputTextWithHint("##filter", "filter (subject, exercise…)",
                                  qbuf, sizeof(qbuf))) {
        filter_query_ = qbuf;
    }
    if (ImGui::Button("Refresh")) library_.refresh();
    ImGui::SameLine();
    ImGui::TextDisabled("%d sessions", (int)library_.sessions().size());

    auto idx = library_.filter(filter_query_);
    ImGui::BeginChild("##list", ImVec2(0, 0), true);
    for (int j : idx) {
        const auto& s = library_.sessions()[j];
        bool sel = (j == selected_session_idx_);
        ImGui::PushID(j);
        // Two-line entry: bolder title row, dim session details underneath,
        // both inside a single Selectable that spans the full width so the
        // browser column can't be horizontally pushed by long labels.
        char title[160];
        std::snprintf(title, sizeof(title), "%s%s",
                       s.label.c_str(), s.partial ? "  [PARTIAL]" : "");
        char sub[160];
        if (s.rep_count > 0) {
            std::snprintf(sub, sizeof(sub),
                           "  %s · %.0f kg · %d saved reps · RPE %d",
                           s.exercise.empty() ? "—" : s.exercise.c_str(),
                           s.total_weight_kg, s.rep_count, s.rpe);
        } else if (s.proposal_count > 0) {
            std::snprintf(sub, sizeof(sub),
                           "  %s · %.0f kg · %d default / %d post · RPE %d",
                           s.exercise.empty() ? "—" : s.exercise.c_str(),
                           s.total_weight_kg, s.proposal_count,
                           s.post_session_count, s.rpe);
        } else {
            std::snprintf(sub, sizeof(sub),
                           "  %s · %.0f kg · no reps · RPE %d",
                           s.exercise.empty() ? "—" : s.exercise.c_str(),
                           s.total_weight_kg, s.rpe);
        }
        const float row_h = ImGui::GetTextLineHeight() * 2.4f;
        if (ImGui::Selectable(("##sel" + std::to_string(j)).c_str(),
                              sel, 0, ImVec2(0, row_h))) {
            selected_session_idx_ = j;
            load_session_(s.dir);
        }
        // Draw label inside the selectable's rect — tightly clipped so it
        // never overflows the column.
        ImVec2 p = ImGui::GetItemRectMin();
        ImVec2 p_end = ImGui::GetItemRectMax();
        ImGui::PushClipRect(p, p_end, true);
        auto* dl = ImGui::GetWindowDrawList();
        dl->AddText(ImVec2(p.x + 4, p.y + 2),
                    IM_COL32(240, 240, 240, 230), title);
        dl->AddText(ImVec2(p.x + 4, p.y + 2 + ImGui::GetTextLineHeight()),
                    IM_COL32(170, 170, 170, 220), sub);
        ImGui::PopClipRect();
        ImGui::PopID();
    }
    ImGui::EndChild();
}

void AnnotationStudio::render_workspace_() {
    if (!session_.is_loaded()) {
        ImGui::TextDisabled("Pick a session from the left to load it.");
        for (auto& w : last_diag_.warnings) {
            ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f), "warn: %s", w.c_str());
        }
        for (auto& e : last_diag_.errors) {
            ImGui::TextColored(ImVec4(1.0f, 0.40f, 0.35f, 1.0f), "err : %s", e.c_str());
        }
        return;
    }

    // Workspace is two stacked work surfaces:
    //   row 1: large aspect-fit video
    //   row 2: the three timelines (rep phases, position, velocity)
    const float h = std::max(1.0f, ImGui::GetContentRegionAvail().y);
    const float spacing_y = ImGui::GetStyle().ItemSpacing.y;
    constexpr float kComfortVideoH = 360.0f;
    constexpr float kComfortTimelineH = 360.0f;
    const float row_gap_h = spacing_y;
    const float rows_h = std::max(0.0f, h - row_gap_h);
    float h_top = rows_h * 0.50f;
    float h_timeline = rows_h - h_top;
    if (rows_h >= kComfortVideoH + kComfortTimelineH) {
        h_top = std::max(kComfortVideoH, rows_h * 0.50f);
        h_timeline = rows_h - h_top;
        if (h_timeline < kComfortTimelineH) {
            const float deficit = kComfortTimelineH - h_timeline;
            const float shrink_top = std::min(deficit, h_top - kComfortVideoH);
            h_top -= shrink_top;
            h_timeline = rows_h - h_top;
        }
    } else if (rows_h > 1.0f) {
        h_top = std::max(180.0f, rows_h * 0.46f);
        h_timeline = std::max(180.0f, rows_h - h_top);
        const float used_h = h_top + h_timeline;
        if (used_h > rows_h) {
            const float scale = rows_h / used_h;
            h_top *= scale;
            h_timeline *= scale;
        }
    }

    // ── Row 1: video ──────────────────────────────────────────────
    ImGui::BeginChild("##toprow", ImVec2(0, h_top), false,
                      ImGuiWindowFlags_NoScrollbar |
                      ImGuiWindowFlags_NoScrollWithMouse);
    {
        ImGui::BeginChild("##videopane", ImVec2(0, 0), true,
                          ImGuiWindowFlags_NoScrollbar |
                          ImGuiWindowFlags_NoScrollWithMouse);
        playhead_t_s_ = video_panel_.render(playhead_t_s_, session_.t0_unified_s());
        ImGui::EndChild();
    }
    ImGui::EndChild();

    // ── Row 2: timeline ───────────────────────────────────────────
    // In focus mode the timeline is pinned to the selected rep — the
    // user gets a wide, zoomed view of just that rep so boundary edits
    // are easy. Updated every frame because the user may drag a
    // boundary that pushes the rep's range.
    if (focus_mode_) {
        int s = rep_table_.selected_index();
        const auto& reps = session_.reps();
        if (s >= 0 && s < (int)reps.size()) {
            const auto& r = reps[s];
            double rs = r.t_start_s > 0.0 ? r.t_start_s
                                          : std::min(r.concentric.t_start_s, r.eccentric.t_start_s);
            double re = r.t_end_s > 0.0 ? r.t_end_s : r.rest.t_end_s;
            double pad = std::max(0.3,
                0.20 * (re - rs));
            timeline_.set_forced_view(rs - pad, re + pad);
        }
    }
    ImGui::BeginChild("##timelinepane", ImVec2(0, h_timeline), true,
                      ImGuiWindowFlags_NoScrollbar |
                      ImGuiWindowFlags_NoScrollWithMouse);
    playhead_t_s_ = timeline_.render(playhead_t_s_);
    if (timeline_.consume_dirty_flag()) {
        // any timeline edit already marked the SessionData dirty.
    }
    ImGui::EndChild();
}

void AnnotationStudio::render_top_toolbar_() {
    const float toolbar_h = ImGui::GetFrameHeightWithSpacing() * 1.35f;
    ImGui::BeginChild("##studio_toolbar", ImVec2(0, toolbar_h), false,
                      ImGuiWindowFlags_HorizontalScrollbar);

    // Compact action row above the columns. Every button is one click —
    // power users press a hotkey, beginners click here. Tooltips spell
    // out the shortcut on hover so people learn them.
    auto tbtn = [](const char* lbl, const char* tip, bool active = false) {
        if (active) ImGui::PushStyleColor(ImGuiCol_Button,
                                            ImVec4(0.20f, 0.45f, 0.80f, 1.0f));
        bool clicked = ImGui::SmallButton(lbl);
        if (active) ImGui::PopStyleColor();
        if (ImGui::IsItemHovered()) {
            ImGui::BeginTooltip();
            ImGui::TextUnformatted(tip);
            ImGui::EndTooltip();
        }
        ImGui::SameLine();
        return clicked;
    };
    ImGui::TextColored(ImVec4(0.55f, 0.85f, 1.0f, 1.0f), "Annotation Studio");
    ImGui::SameLine();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn(show_library_ ? "Sessions" : "Sessions",
             "Ctrl+L — show / hide the session browser",
             show_library_)) show_library_ = !show_library_;
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("Focus",
             "Ctrl+F — collapse to the selected rep only (video + plot zoom)",
             focus_mode_)) focus_mode_ = !focus_mode_;
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("Undo",  "Ctrl+Z — revert the last rep edit"))    undo_();
    if (tbtn("Redo",  "Ctrl+Y — redo the last undone edit"))   redo_();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("+ Rep",
             "Insert (key) — add a new rep centred on the playhead"))
        insert_rep_at_(playhead_t_s_);
    bool can_delete = rep_table_.selected_index() >= 0;
    if (!can_delete) ImGui::BeginDisabled();
    if (tbtn("- Rep",
             "Delete (key) — remove the currently selected rep")
        && can_delete) {
        push_undo_();
        int s = rep_table_.selected_index();
        session_.mutable_reps().erase(session_.mutable_reps().begin() + s);
        if (s < (int)session_.gt_attrs().size())
            session_.mutable_gt_attrs().erase(session_.mutable_gt_attrs().begin() + s);
        session_.ensure_gt_attrs_aligned();
        session_.mark_reps_dirty();
    }
    if (!can_delete) ImGui::EndDisabled();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (session_.is_loaded()) {
        ImGui::TextDisabled("visible %d | default %d | post %d",
                            (int)session_.reps().size(),
                            (int)session_.candidate_reps().size(),
                            (int)session_.post_session_reps().size());
        ImGui::SameLine();
        ImGui::TextDisabled("|");
        ImGui::SameLine();
    }
    bool has_base = session_.is_loaded() && !session_.candidate_reps().empty();
    if (!has_base) ImGui::BeginDisabled();
    if (tbtn("Base",
             "Replace visible reps with the default/base annotations for review")
        && has_base) {
        use_base_proposal_();
    }
    if (!has_base) ImGui::EndDisabled();
    bool has_proposal = session_.is_loaded() && !session_.post_session_reps().empty();
    if (!has_proposal) ImGui::BeginDisabled();
    if (tbtn("Post",
             "Replace visible reps with the post-session annotations for review")
        && has_proposal) {
        show_use_proposal_dialog_ = true;
    }
    if (!has_proposal) ImGui::EndDisabled();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("Check", "Re-run the rep-segmentation sanity checks"))
        run_validation_();
    if (tbtn("Save", "Ctrl+S — commit edits to disk + update manifest",
             session_.dirty()) && session_.dirty())
        show_save_dialog_ = true;
    if (tbtn("Reload", "Ctrl+R — discard unsaved edits & reload"))
        reload_();
    ImGui::EndChild();
}

void AnnotationStudio::push_undo_() {
    if (!session_.is_loaded()) return;
    session_.ensure_gt_attrs_aligned();
    undo_stack_.push_back({session_.reps(), session_.gt_attrs()});
    if (undo_stack_.size() > 50) undo_stack_.erase(undo_stack_.begin());
    redo_stack_.clear();
}
void AnnotationStudio::undo_() {
    if (undo_stack_.empty() || !session_.is_loaded()) return;
    redo_stack_.push_back({session_.reps(), session_.gt_attrs()});
    session_.mutable_reps()     = std::move(undo_stack_.back().reps);
    session_.mutable_gt_attrs() = std::move(undo_stack_.back().attrs);
    undo_stack_.pop_back();
    session_.ensure_gt_attrs_aligned();
    session_.mark_reps_dirty();
}
void AnnotationStudio::redo_() {
    if (redo_stack_.empty() || !session_.is_loaded()) return;
    undo_stack_.push_back({session_.reps(), session_.gt_attrs()});
    session_.mutable_reps()     = std::move(redo_stack_.back().reps);
    session_.mutable_gt_attrs() = std::move(redo_stack_.back().attrs);
    redo_stack_.pop_back();
    session_.ensure_gt_attrs_aligned();
    session_.mark_reps_dirty();
}

void AnnotationStudio::insert_rep_at_(double seed_t) {
    if (!session_.is_loaded()) return;
    push_undo_();
    // Default phase split: ~0.4 s concentric, 0.6 s eccentric, 0.2 s rest,
    // centred on the playhead. The user immediately drags the boundaries
    // to fit; this just gets a band on the timeline so they can grab it.
    RepAnnotation r;
    r.rep_id = (int)session_.reps().size() + 1;
    double a = seed_t - 0.6, b = seed_t + 0.6;
    r.concentric.t_start_s = a;
    r.concentric.t_end_s   = a + 0.4;
    // Default top_rest is zero-width — most reps don't pause at the top.
    // The user drags the top-rest END handle wider when a real pause was
    // visible.
    r.top_rest.t_start_s   = r.concentric.t_end_s;
    r.top_rest.t_end_s     = r.concentric.t_end_s;
    r.eccentric.t_start_s  = r.top_rest.t_end_s;
    r.eccentric.t_end_s    = a + 1.0;
    r.rest.t_start_s       = r.eccentric.t_end_s;
    r.rest.t_end_s         = b;
    r.concentric.source = r.eccentric.source = r.rest.source = "manual";
    // Insert sorted by t_start so the rep IDs stay in chronological order
    // after a renumber.
    auto& reps = session_.mutable_reps();
    auto it = std::find_if(reps.begin(), reps.end(),
        [&](const RepAnnotation& x){ return x.concentric.t_start_s > a; });
    int new_idx = (int)(it - reps.begin());
    reps.insert(it, r);
    // Keep GT attributes index-aligned: insert a default at the SAME position
    // (attrs was aligned to the pre-insert reps, so it is one shorter now).
    auto& attrs = session_.mutable_gt_attrs();
    if (new_idx <= (int)attrs.size())
        attrs.insert(attrs.begin() + new_idx, GtAttr{});
    session_.ensure_gt_attrs_aligned();   // safety: reconcile any size drift
    int next = 1;
    for (auto& q : reps) q.rep_id = next++;
    session_.recompute_rep_metrics(new_idx);
    session_.mark_reps_dirty();
    select_rep_(new_idx);
}

void AnnotationStudio::run_validation_() {
    validation_issues_.clear();
    if (!session_.is_loaded()) return;
    const auto& reps = session_.reps();
    for (int i = 0; i < (int)reps.size(); ++i) {
        const auto& r = reps[i];
        auto add = [&](const char* sev, const char* code,
                        const std::string& msg, double t) {
            validation_issues_.push_back({r.rep_id, sev, code, msg, t});
        };
        if (r.concentric.t_end_s <= r.concentric.t_start_s)
            add("error", "concentric.zero_duration",
                "concentric phase has zero or negative duration",
                r.concentric.t_start_s);
        if (r.eccentric.t_end_s <= r.eccentric.t_start_s)
            add("error", "eccentric.zero_duration",
                "eccentric phase has zero or negative duration",
                r.eccentric.t_start_s);
        if (r.phase_order == "eccentric_first") {
            if (std::abs(r.top_rest.t_end_s - r.eccentric.t_start_s) > 1e-6
                || std::abs(r.eccentric.t_end_s - r.bottom_rest.t_start_s) > 1e-6
                || std::abs(r.bottom_rest.t_end_s - r.concentric.t_start_s) > 1e-6
                || std::abs(r.concentric.t_end_s - r.rest.t_start_s) > 1e-6) {
                add("warning", "phase.gap.ecc_first",
                    "eccentric-first phase boundaries don't align",
                    r.t_start_s > 0.0 ? r.t_start_s : r.eccentric.t_start_s);
            }
        } else {
            if (r.top_rest.t_end_s + 1e-3 < r.eccentric.t_start_s
                || r.top_rest.t_start_s != r.concentric.t_end_s)
                add("warning", "phase.gap.top",
                    "top-rest boundaries don't align with concentric/eccentric",
                    r.top_rest.t_start_s);
            if (r.eccentric.t_end_s != r.rest.t_start_s)
                add("warning", "phase.gap.bot",
                    "bottom-rest boundaries don't align with eccentric end",
                    r.eccentric.t_end_s);
        }
        if (i + 1 < (int)reps.size()
            && r.rest.t_end_s > (reps[i + 1].t_start_s > 0.0
                                 ? reps[i + 1].t_start_s
                                 : reps[i + 1].concentric.t_start_s) + 1e-3)
            add("error", "reps.overlap",
                "rest end overlaps next rep's concentric start",
                r.rest.t_end_s);
        if (r.peak_concentric_velocity > 4.5f)
            add("warning", "peak.outlier_high",
                "peak concentric velocity unusually high (>4.5 m/s)",
                r.concentric.t_start_s);
        if (r.peak_concentric_velocity > 0 && r.peak_concentric_velocity < 0.10f)
            add("warning", "peak.outlier_low",
                "peak concentric velocity very low (<0.1 m/s) — bad rep?",
                r.concentric.t_start_s);
        if (r.rom_m > 0 && r.rom_m < 0.05f)
            add("warning", "rom.too_small",
                "ROM <5 cm — likely false positive",
                r.concentric.t_start_s);
        if (r.rom_m > 1.5f)
            add("warning", "rom.too_large",
                "ROM >1.5 m — likely camera glitch",
                r.concentric.t_start_s);
    }
    spdlog::info("Validation: {} issues found across {} reps",
                  validation_issues_.size(), reps.size());
}

void AnnotationStudio::render_validation_tab_() {
    if (validation_issues_.empty() && session_.reps().empty()) {
        if (!session_.candidate_reps().empty() || !session_.post_session_reps().empty()) {
            ImGui::TextDisabled("%d default reps and %d post-session reps are available.",
                                (int)session_.candidate_reps().size(),
                                (int)session_.post_session_reps().size());
        } else {
            ImGui::TextDisabled("No reps to validate.");
        }
        return;
    }
    if (validation_issues_.empty()) {
        ImGui::TextColored(ImVec4(0.45f, 0.95f, 0.50f, 1),
                           "✓  No issues. %d reps clean.",
                           (int)session_.reps().size());
        ImGui::TextDisabled("Click 'Validate now' in the toolbar to recheck.");
        return;
    }
    int n_err = 0, n_warn = 0;
    for (const auto& v : validation_issues_) {
        if (v.severity == "error") ++n_err; else ++n_warn;
    }
    ImGui::Text("%d errors  ·  %d warnings  across %d reps",
                 n_err, n_warn, (int)session_.reps().size());
    ImGui::Separator();
    if (ImGui::BeginTable("##val", 5,
            ImGuiTableFlags_RowBg | ImGuiTableFlags_Borders
            | ImGuiTableFlags_Resizable | ImGuiTableFlags_ScrollY,
            ImVec2(0, 0))) {
        ImGui::TableSetupColumn("Rep", ImGuiTableColumnFlags_WidthFixed, 50);
        ImGui::TableSetupColumn("Severity", ImGuiTableColumnFlags_WidthFixed, 80);
        ImGui::TableSetupColumn("Code");
        ImGui::TableSetupColumn("Message");
        ImGui::TableSetupColumn("Jump", ImGuiTableColumnFlags_WidthFixed, 70);
        ImGui::TableHeadersRow();
        for (size_t i = 0; i < validation_issues_.size(); ++i) {
            const auto& v = validation_issues_[i];
            ImGui::TableNextRow();
            ImGui::TableNextColumn();
            ImGui::Text("R%d", v.rep_id);
            ImGui::TableNextColumn();
            ImVec4 col = (v.severity == "error")
                ? ImVec4(1.0f, 0.40f, 0.30f, 1.0f)
                : ImVec4(1.0f, 0.78f, 0.30f, 1.0f);
            ImGui::TextColored(col, "%s", v.severity.c_str());
            ImGui::TableNextColumn();
            ImGui::TextDisabled("%s", v.code.c_str());
            ImGui::TableNextColumn();
            ImGui::TextWrapped("%s", v.message.c_str());
            ImGui::TableNextColumn();
            ImGui::PushID((int)i);
            if (ImGui::SmallButton("seek")) seek_(v.t_unified_s);
            ImGui::PopID();
        }
        ImGui::EndTable();
    }
}

void AnnotationStudio::render_summary_cards_() {
    if (!session_.is_loaded()) {
        ImGui::TextDisabled("Per-rep summary will appear here once loaded.");
        return;
    }
    const auto& reps = session_.reps();
    ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1),
                        "Reps  ·  %d total", (int)reps.size());
    ImGui::Separator();
    if (reps.empty()) {
        ImGui::TextDisabled("No reps yet. Shift+drag the timeline to create.");
        return;
    }
    // Set-level KPIs.
    double sum_pv = 0, sum_rom = 0, max_pv = -1e9;
    for (const auto& r : reps) {
        sum_pv  += r.peak_concentric_velocity;
        sum_rom += r.rom_m;
        max_pv  = std::max<double>(max_pv, r.peak_concentric_velocity);
    }
    int n = (int)reps.size();
    double mean_pv = sum_pv / n, mean_rom = sum_rom / n;
    double vloss = 100.0 * (reps.front().peak_concentric_velocity
                             - reps.back().peak_concentric_velocity)
                          / std::max(0.05f, reps.front().peak_concentric_velocity);
    ImGui::Text("avg %.2f", mean_pv);
    ImGui::SameLine();
    ImGui::TextDisabled("max %.2f", max_pv);
    ImGui::Text("ROM %.0f mm", mean_rom * 1000);
    ImGui::SameLine();
    ImGui::TextDisabled("loss %+.1f%%", vloss);
    ImGui::Separator();
    // Compact rep-jump buttons.
    const float card_w = 48.0f, card_h = 30.0f, gap = 4.0f;
    float avail_w = ImGui::GetContentRegionAvail().x;
    int per_row = std::max(1, (int)((avail_w + gap) / (card_w + gap)));
    ImGui::BeginChild("##cards", ImVec2(0, 0), false);
    for (int i = 0; i < n; ++i) {
        if (i > 0 && (i % per_row) != 0) ImGui::SameLine(0, gap);
        const auto& r = reps[i];
        bool sel = (i == rep_table_.selected_index());
        ImGui::PushID(i);
        ImVec2 p = ImGui::GetCursorScreenPos();
        ImGui::InvisibleButton("##c", ImVec2(card_w, card_h));
        bool hover = ImGui::IsItemHovered();
        if (ImGui::IsItemClicked()) select_rep_(i);
        ImU32 bg = sel ? IM_COL32(60, 110, 200, 230)
                        : hover ? IM_COL32(60, 75, 95, 230)
                                 : IM_COL32(40, 50, 65, 230);
        ImU32 border = sel ? IM_COL32(120, 200, 255, 255)
                           : IM_COL32(80, 100, 130, 200);
        auto* dl = ImGui::GetWindowDrawList();
        dl->AddRectFilled(p, ImVec2(p.x + card_w, p.y + card_h), bg, 4.0f);
        dl->AddRect      (p, ImVec2(p.x + card_w, p.y + card_h), border, 4.0f, 0, 1.5f);
        char id[8]; std::snprintf(id, sizeof(id), "R%d", r.rep_id);
        const ImVec2 text_sz = ImGui::CalcTextSize(id);
        dl->AddText(ImVec2(p.x + (card_w - text_sz.x) * 0.5f,
                           p.y + (card_h - text_sz.y) * 0.5f),
                    IM_COL32(255,255,255,240), id);
        if (hover) {
            ImGui::BeginTooltip();
            ImGui::Text("Rep %d", r.rep_id);
            ImGui::Text("Peak velocity: %.3f m/s", r.peak_concentric_velocity);
            ImGui::Text("ROM: %.0f mm", r.rom_m * 1000);
            ImGui::EndTooltip();
        }
        ImGui::PopID();
    }
    ImGui::EndChild();
}

void AnnotationStudio::render_save_dialog_() {
    if (!show_save_dialog_) return;
    ImGui::OpenPopup("Save annotations?");
    if (ImGui::BeginPopupModal("Save annotations?", &show_save_dialog_,
                                ImGuiWindowFlags_AlwaysAutoResize)) {
        ImGui::Text("Session: %s", session_.path().filename().string().c_str());
        ImGui::Text("Reps dirty:     %s", session_.reps_dirty() ? "yes" : "no");
        ImGui::Separator();
        ImGui::Text("Audit note (appended to events.jsonl):");
        char buf[512];
        std::snprintf(buf, sizeof(buf), "%s", save_note_.c_str());
        if (ImGui::InputText("##note", buf, sizeof(buf))) save_note_ = buf;
        if (ImGui::Button("Save")) {
            save_();
            show_save_dialog_ = false;
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) show_save_dialog_ = false;
        ImGui::EndPopup();
    }
}

void AnnotationStudio::render_use_proposal_dialog_() {
    if (!show_use_proposal_dialog_) return;
    ImGui::OpenPopup("Use post-session annotations?");
    if (ImGui::BeginPopupModal("Use post-session annotations?", &show_use_proposal_dialog_,
        ImGuiWindowFlags_AlwaysAutoResize)) {
        const int current_n = (int)session_.reps().size();
        const int base_n = (int)session_.candidate_reps().size();
        const int proposal_n = (int)session_.post_session_reps().size();
        ImGui::Text("Session: %s", session_.path().filename().string().c_str());
        ImGui::Text("Current visible reps: %d", current_n);
        ImGui::Text("Default/base reps: %d", base_n);
        ImGui::Text("Post-session annotation reps: %d", proposal_n);
        ImGui::Separator();
        ImGui::TextWrapped("This replaces the visible reps in memory only. "
                           "Review and save when the boundaries look correct.");
        if (ImGui::Button("Use post-session annotations")) {
            use_post_session_proposal_();
            show_use_proposal_dialog_ = false;
        }
        ImGui::SameLine();
        if (ImGui::Button("Cancel")) show_use_proposal_dialog_ = false;
        ImGui::EndPopup();
    }
}

void AnnotationStudio::load_session_(const std::filesystem::path& dir) {
    if (session_.dirty()) {
        Notifications::get().warn("Discarding unsaved annotation changes — "
                                   "save (Ctrl+S) before switching sessions next time.");
    }
    last_diag_ = {};
    if (!session_.load(dir, last_diag_)) {
        Notifications::get().error("Failed to load session: " + dir.string());
        return;
    }
    undo_stack_.clear();
    redo_stack_.clear();
    validation_issues_.clear();
    // Apply default cleaning so the timeline has something to plot.
    auto cfg = session_.clean_config();
    if (cfg.v_max_mps == 0) cfg.v_max_mps = 4.0f;  // safety default; per-exercise tightening optional
    session_.set_clean_config(cfg);
    session_.recompute_clean_signal(cfg);

    video_.open(dir, session_.video_index());
    timeline_.set_session(&session_);
    video_panel_.set_session(&session_, &video_);
    rep_table_.set_session(&session_);
    quality_panel_.set_session(&session_);
    playhead_t_s_ = session_.t0_unified_s();

    // ── Step 7: camera-only ground-truth working set ───────────────────
    // Prefer a previously-saved ground_truth.json (resume), else the pipeline
    // prefill candidate, else an empty list (label from scratch). The pipeline
    // reference trace (s/v) is loaded for plotting. Nothing is read from / written
    // to the dataset dir — labels live under the labels root.
    namespace gio = ground_truth_io;
    const std::string sid = dir.filename().string();
    const AppConfig& cfg2 = app_.config();
    session_.load_trace(fs::path(cfg2.gt_prefill_root) / sid / "trace.csv");

    auto f2t = [this](int f){ return session_.time_for_frame(f); };
    std::vector<GroundTruthLabel> labels;
    std::string err;
    const fs::path gt_file   = fs::path(cfg2.gt_labels_root)  / sid / "ground_truth.json";
    const fs::path cand_file = fs::path(cfg2.gt_prefill_root) / sid / "ground_truth.candidate.json";
    if (gio::load(gt_file, labels, err)) {
        gio::labels_to_reps(labels, f2t, session_.mutable_reps(), session_.mutable_gt_attrs());
        Notifications::get().info(std::to_string(labels.size()) +
                                  " saved ground-truth labels loaded for review.");
    } else if (gio::load(cand_file, labels, err)) {
        gio::labels_to_reps(labels, f2t, session_.mutable_reps(), session_.mutable_gt_attrs());
        Notifications::get().info(std::to_string(labels.size()) +
                                  " pipeline prefill labels loaded — review and save.");
    } else {
        session_.mutable_reps().clear();
        session_.mutable_gt_attrs().clear();
        Notifications::get().info("No prefill found for this session — label from scratch.");
    }
    session_.ensure_gt_attrs_aligned();
    session_.clear_dirty();
    run_validation_();
    if (!session_.reps().empty()) select_rep_(0);
}

void AnnotationStudio::save_() {
    if (!session_.is_loaded()) return;
    // Step 7: write frame-indexed ground_truth.json to the labels root (NEVER
    // into the read-only dataset; legacy Persistence/rep_segments.json is not used).
    namespace gio = ground_truth_io;
    session_.ensure_gt_attrs_aligned();
    auto t2f = [this](double t){ return session_.frame_for_time(t); };
    auto labels = gio::reps_to_labels(session_.reps(), session_.gt_attrs(), t2f);
    const std::string sid = session_.path().filename().string();
    std::string err;
    if (gio::save(app_.config().gt_labels_root, sid, labels, err)) {
        session_.clear_dirty();
        save_note_.clear();
        Notifications::get().success("Ground truth saved (" +
                                     std::to_string(labels.size()) + " labels).");
    } else {
        Notifications::get().error("Ground-truth save failed: " + err);
    }
}

void AnnotationStudio::reload_() {
    if (!session_.is_loaded()) return;
    auto dir = session_.path();
    load_session_(dir);
    Notifications::get().info("Reloaded from disk.");
}

void AnnotationStudio::use_base_proposal_() {
    if (!session_.is_loaded() || session_.candidate_reps().empty()) return;
    push_undo_();

    auto proposal = session_.candidate_reps();
    const int default_set_id = session_.info().sets.empty()
        ? 1
        : std::max(1, session_.info().sets.front().set_id);
    int next_id = 1;
    for (auto& r : proposal) {
        r.rep_id = next_id++;
        if (r.set_id <= 0) r.set_id = default_set_id;
        if (r.concentric.source.empty()) r.concentric.source = "camera_gt_v1_base";
        if (r.eccentric.source.empty()) r.eccentric.source = "camera_gt_v1_base";
    }

    session_.mutable_reps() = std::move(proposal);
    session_.mutable_gt_attrs().assign(session_.reps().size(), GtAttr{});  // fresh GT attrs
    session_.mark_reps_dirty();
    run_validation_();
    if (!session_.reps().empty()) select_rep_(0);
    Notifications::get().info("Loaded default/base annotation proposal for review.");
}

void AnnotationStudio::use_post_session_proposal_() {
    if (!session_.is_loaded() || session_.post_session_reps().empty()) return;
    push_undo_();

    auto proposal = session_.post_session_reps();
    const int default_set_id = session_.info().sets.empty()
        ? 1
        : std::max(1, session_.info().sets.front().set_id);
    int next_id = 1;
    for (auto& r : proposal) {
        r.rep_id = next_id++;
        if (r.set_id <= 0) r.set_id = default_set_id;
        if (r.concentric.source.empty()) r.concentric.source = "post_session_camera_gt";
        if (r.eccentric.source.empty()) r.eccentric.source = "post_session_camera_gt";
    }

    session_.mutable_reps() = std::move(proposal);
    session_.mutable_gt_attrs().assign(session_.reps().size(), GtAttr{});  // fresh GT attrs
    session_.mark_reps_dirty();
    run_validation_();
    if (!session_.reps().empty()) select_rep_(0);
    Notifications::get().info("Loaded post-session annotation proposal for review.");
}

void AnnotationStudio::seek_(double t_unified_s) {
    playhead_t_s_ = t_unified_s;
    timeline_.set_playhead(t_unified_s);
}

void AnnotationStudio::select_rep_(int rep_index) {
    if (rep_index < 0 || rep_index >= (int)session_.reps().size()) return;
    timeline_.set_selected_rep(rep_index);
    timeline_.center_on_rep(rep_index);
    const auto& r = session_.reps()[rep_index];
    seek_(r.t_start_s > 0.0 ? r.t_start_s
                            : std::min(r.concentric.t_start_s, r.eccentric.t_start_s));
}

} // namespace vbt

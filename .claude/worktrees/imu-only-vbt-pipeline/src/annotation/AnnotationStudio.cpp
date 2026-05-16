/**
 * @file AnnotationStudio.cpp
 */
#include "annotation/AnnotationStudio.h"
#include "annotation/Persistence.h"
#include "app/Application.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <spdlog/spdlog.h>

namespace vbt {

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
    ImGui::SetNextWindowSize(ImVec2(1500, 920), ImGuiCond_FirstUseEver);
    if (!ImGui::Begin("Annotation Studio", &is_open_,
                      ImGuiWindowFlags_MenuBar)) {
        ImGui::End();
        return;
    }
    if (ImGui::BeginMenuBar()) {
        if (ImGui::BeginMenu("File")) {
            if (ImGui::MenuItem("Refresh library")) library_.refresh();
            if (ImGui::MenuItem("Save", "Ctrl+S",
                                false, session_.dirty()))
                show_save_dialog_ = true;
            if (ImGui::MenuItem("Reload from disk", "Ctrl+R",
                                false, session_.is_loaded()))
                reload_();
            ImGui::Separator();
            if (ImGui::MenuItem("Close studio")) is_open_ = false;
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Navigate")) {
            if (ImGui::MenuItem("Previous rep", "Z",
                                false, !session_.reps().empty())) {
                int s = rep_table_.selected_index();
                select_rep_(std::max(0, s - 1));
            }
            if (ImGui::MenuItem("Next rep", "X",
                                false, !session_.reps().empty())) {
                int s = rep_table_.selected_index();
                select_rep_(std::min((int)session_.reps().size() - 1, s + 1));
            }
            ImGui::EndMenu();
        }
        // Status indicator.
        if (session_.is_loaded()) {
            ImGui::SameLine(ImGui::GetWindowWidth() - 380);
            ImGui::TextDisabled("%s", session_.path().filename().string().c_str());
            if (session_.dirty()) {
                ImGui::SameLine();
                ImGui::TextColored(ImVec4(1.0f, 0.85f, 0.30f, 1.0f),
                                   "● unsaved");
            }
        }
        ImGui::EndMenuBar();
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
        if (ImGui::IsKeyPressed(ImGuiKey_Insert, false))
            insert_rep_at_(playhead_t_s_);
        if (ImGui::IsKeyPressed(ImGuiKey_Delete, false)
            && rep_table_.selected_index() >= 0) {
            push_undo_();
            int s = rep_table_.selected_index();
            session_.mutable_reps().erase(session_.mutable_reps().begin() + s);
            session_.mark_reps_dirty();
        }
    }

    render_top_toolbar_();

    // 3-pane layout via stacked child windows (Columns API leaks into
    // siblings whenever a child Selectable + SameLine combo overflows
    // horizontally, which is why the previous attempt left the right
    // column empty). Computing widths from the available content region
    // keeps every pane self-contained and lets each scroll independently.
    const float total_w = ImGui::GetContentRegionAvail().x;
    const float total_h = ImGui::GetContentRegionAvail().y;
    const float left_w  = show_library_  ? std::min(320.0f, total_w * 0.22f) : 0.0f;
    const float right_w = show_metadata_ ? std::min(420.0f, total_w * 0.28f) : 0.0f;
    const float gap = (left_w > 0 ? 8.0f : 0.0f) + (right_w > 0 ? 8.0f : 0.0f);
    const float center_w = std::max(400.0f, total_w - left_w - right_w - gap);

    if (show_library_) {
        ImGui::BeginChild("##studio_left", ImVec2(left_w, total_h), true);
        render_session_browser_();
        ImGui::EndChild();
        ImGui::SameLine();
    }
    ImGui::BeginChild("##studio_center", ImVec2(center_w, total_h), false);
    render_workspace_();
    ImGui::EndChild();

    if (show_metadata_) {
        ImGui::SameLine();
        ImGui::BeginChild("##studio_right", ImVec2(right_w, total_h), true);
        meta_panel_.render();
        ImGui::EndChild();
    }

    render_save_dialog_();
    ImGui::End();
}

void AnnotationStudio::render_session_browser_() {
    ImGui::TextColored(ImVec4(0.55f, 0.78f, 1, 1), "Sessions");
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
        // Two-line entry: bolder title row, dim metadata row underneath,
        // both inside a single Selectable that spans the full width so the
        // browser column can't be horizontally pushed by long labels.
        char title[160];
        std::snprintf(title, sizeof(title), "%s%s",
                       s.label.c_str(), s.partial ? "  [PARTIAL]" : "");
        char sub[160];
        std::snprintf(sub, sizeof(sub),
                       "  %s · %.0f kg · %d reps · RPE %d",
                       s.exercise.empty() ? "—" : s.exercise.c_str(),
                       s.total_weight_kg, s.rep_count, s.rpe);
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

    // Workspace is three stacked rows:
    //   row 1 (top):  video left, summary cards right (uses the gap that
    //                 was empty when the video aspect-fit didn't fill the
    //                 column)
    //   row 2 (mid):  full-width timeline (the editing surface)
    //   row 3 (bot):  tabs (rep table, marker quality, events) free-scroll
    const float h = ImGui::GetContentRegionAvail().y;
    const float h_top      = std::max(260.0f, h * 0.34f);
    const float h_timeline = std::max(280.0f, h * 0.38f);
    const float h_bottom   = std::max(220.0f, h - h_top - h_timeline - 12.0f);

    // ── Row 1: video + summary cards ──────────────────────────────
    ImGui::BeginChild("##toprow", ImVec2(0, h_top), false);
    {
        const float w = ImGui::GetContentRegionAvail().x;
        // Video gets enough width to comfortably hit its 1.77:1 aspect at
        // the row height; summary cards get the leftover space (which the
        // user noticed was previously empty).
        const float video_w = std::min(w * 0.62f, h_top * 1.85f);
        const float side_w  = std::max(180.0f, w - video_w - 8.0f);
        ImGui::BeginChild("##videopane", ImVec2(video_w, 0), true);
        playhead_t_s_ = video_panel_.render(playhead_t_s_, session_.t0_unified_s());
        ImGui::EndChild();
        ImGui::SameLine();
        ImGui::BeginChild("##summarypane", ImVec2(side_w, 0), true);
        render_summary_cards_();
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
            double pad = std::max(0.3,
                0.20 * (r.rest.t_end_s - r.concentric.t_start_s));
            timeline_.set_forced_view(r.concentric.t_start_s - pad,
                                       r.rest.t_end_s + pad);
        }
    }
    ImGui::BeginChild("##timelinepane", ImVec2(0, h_timeline), true);
    playhead_t_s_ = timeline_.render(playhead_t_s_);
    if (timeline_.consume_dirty_flag()) {
        // any timeline edit already marked the SessionData dirty.
    }
    ImGui::EndChild();

    // ── Row 3: tab strip with free-scrolling content ─────────────
    rep_table_.set_playhead(playhead_t_s_);
    ImGui::BeginChild("##bottompane", ImVec2(0, h_bottom), true);
    if (ImGui::BeginTabBar("##bottomtabs")) {
        if (ImGui::BeginTabItem("Reps")) {
            rep_table_.render();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Validate")) {
            render_validation_tab_();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Marker quality")) {
            quality_panel_.render();
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Events")) {
            if (session_.events().empty())
                ImGui::TextDisabled("No events.");
            else {
                ImGui::BeginChild("##evt", ImVec2(0, 0), false);
                for (auto& ev : session_.events()) {
                    std::string lvl = ev.value("level", "info");
                    ImVec4 col = ImVec4(0.85f, 0.85f, 0.85f, 1);
                    if (lvl == "warning") col = ImVec4(1, 0.78f, 0.25f, 1);
                    else if (lvl == "error") col = ImVec4(1, 0.30f, 0.25f, 1);
                    ImGui::PushStyleColor(ImGuiCol_Text, col);
                    ImGui::TextWrapped("[%s] [%s/%s] %s",
                        ev.value("wallclock", "?").c_str(),
                        ev.value("source", "?").c_str(),
                        ev.value("code", "?").c_str(),
                        ev.value("msg", "").c_str());
                    ImGui::PopStyleColor();
                }
                ImGui::EndChild();
            }
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    ImGui::EndChild();
}

void AnnotationStudio::render_top_toolbar_() {
    // Compact action row above the columns. Every button is one click —
    // power users press a hotkey, beginners click here. Tooltips spell
    // out the shortcut on hover so people learn them.
    auto tbtn = [](const char* lbl, const char* tip, bool active = false) {
        if (active) ImGui::PushStyleColor(ImGuiCol_Button,
                                            ImVec4(0.20f, 0.45f, 0.80f, 1.0f));
        bool clicked = ImGui::Button(lbl);
        if (active) ImGui::PopStyleColor();
        if (ImGui::IsItemHovered()) {
            ImGui::BeginTooltip();
            ImGui::TextUnformatted(tip);
            ImGui::EndTooltip();
        }
        ImGui::SameLine();
        return clicked;
    };
    if (tbtn(show_library_ ? "<< Library" : ">> Library",
             "Show / hide the session list (full-width plot when hidden)",
             show_library_)) show_library_ = !show_library_;
    if (tbtn(show_metadata_ ? "Metadata >>" : "<< Metadata",
             "Show / hide the metadata editor (full-width plot when hidden)",
             show_metadata_)) show_metadata_ = !show_metadata_;
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn(focus_mode_ ? "Focus: ON" : "Focus: OFF",
             "Ctrl+F — collapse to the selected rep only (video + plot zoom)",
             focus_mode_)) focus_mode_ = !focus_mode_;
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("Undo",  "Ctrl+Z — revert the last rep edit"))    undo_();
    if (tbtn("Redo",  "Ctrl+Y — redo the last undone edit"))   redo_();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("+ Insert rep here",
             "Insert (key) — add a new rep centred on the playhead"))
        insert_rep_at_(playhead_t_s_);
    bool can_delete = rep_table_.selected_index() >= 0;
    if (!can_delete) ImGui::BeginDisabled();
    if (tbtn("− Delete selected",
             "Delete (key) — remove the currently selected rep")
        && can_delete) {
        push_undo_();
        int s = rep_table_.selected_index();
        session_.mutable_reps().erase(session_.mutable_reps().begin() + s);
        session_.mark_reps_dirty();
    }
    if (!can_delete) ImGui::EndDisabled();
    ImGui::TextDisabled("|"); ImGui::SameLine();
    if (tbtn("Validate now", "Re-run the rep-segmentation sanity checks"))
        run_validation_();
    if (tbtn("Save", "Ctrl+S — commit edits to disk + update manifest",
             session_.dirty()) && session_.dirty())
        show_save_dialog_ = true;
    if (tbtn("Reset (reload)", "Ctrl+R — discard unsaved edits & reload"))
        reload_();
    ImGui::NewLine();   // close the SameLine chain
    ImGui::Separator();
}

void AnnotationStudio::push_undo_() {
    if (!session_.is_loaded()) return;
    undo_stack_.push_back(session_.reps());
    if (undo_stack_.size() > 50) undo_stack_.erase(undo_stack_.begin());
    redo_stack_.clear();
}
void AnnotationStudio::undo_() {
    if (undo_stack_.empty() || !session_.is_loaded()) return;
    redo_stack_.push_back(session_.reps());
    session_.mutable_reps() = std::move(undo_stack_.back());
    undo_stack_.pop_back();
    session_.mark_reps_dirty();
}
void AnnotationStudio::redo_() {
    if (redo_stack_.empty() || !session_.is_loaded()) return;
    undo_stack_.push_back(session_.reps());
    session_.mutable_reps() = std::move(redo_stack_.back());
    redo_stack_.pop_back();
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
        if (r.top_rest.t_end_s + 1e-3 < r.eccentric.t_start_s
            || r.top_rest.t_start_s != r.concentric.t_end_s)
            add("warning", "phase.gap.top",
                "top-rest boundaries don't align with concentric/eccentric",
                r.top_rest.t_start_s);
        if (r.eccentric.t_end_s != r.rest.t_start_s)
            add("warning", "phase.gap.bot",
                "bottom-rest boundaries don't align with eccentric end",
                r.eccentric.t_end_s);
        if (i + 1 < (int)reps.size()
            && r.rest.t_end_s > reps[i + 1].concentric.t_start_s + 1e-3)
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
        ImGui::TextDisabled("No reps to validate.");
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
    ImGui::Text("avg pv  : %.3f m/s", mean_pv);
    ImGui::Text("max pv  : %.3f m/s", max_pv);
    ImGui::Text("avg ROM : %.0f mm",  mean_rom * 1000);
    ImGui::Text("v-loss  : %+.1f %%", vloss);
    ImGui::Separator();
    // Tile grid.
    const float card_w = 96.0f, card_h = 60.0f, gap = 6.0f;
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
        dl->AddRectFilled(p, ImVec2(p.x + card_w, p.y + card_h), bg, 6.0f);
        dl->AddRect      (p, ImVec2(p.x + card_w, p.y + card_h), border, 6.0f, 0, 1.5f);
        char id[8]; std::snprintf(id, sizeof(id), "R%d", r.rep_id);
        char pv[24]; std::snprintf(pv, sizeof(pv), "pv %.2f", r.peak_concentric_velocity);
        char rom[24]; std::snprintf(rom, sizeof(rom), "ROM %.0f", r.rom_m * 1000);
        dl->AddText(ImVec2(p.x + 8, p.y + 4),  IM_COL32(255,255,255,240), id);
        dl->AddText(ImVec2(p.x + 8, p.y + 22), IM_COL32(220,255,220,230), pv);
        dl->AddText(ImVec2(p.x + 8, p.y + 40), IM_COL32(220,220,255,220), rom);
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
        ImGui::Text("Metadata dirty: %s", session_.meta_dirty() ? "yes" : "no");
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
    // Apply default cleaning so the timeline has something to plot.
    auto cfg = session_.clean_config();
    if (cfg.v_max_mps == 0) cfg.v_max_mps = 4.0f;  // safety default; per-exercise tightening optional
    session_.set_clean_config(cfg);
    session_.recompute_clean_signal(cfg);

    video_.open(dir, session_.video_index());
    timeline_.set_session(&session_);
    video_panel_.set_session(&session_, &video_);
    rep_table_.set_session(&session_);
    meta_panel_.set_session(&session_);
    quality_panel_.set_session(&session_);
    playhead_t_s_ = session_.t0_unified_s();
}

void AnnotationStudio::save_() {
    SaveOptions opt;
    opt.note = save_note_;
    opt.recompute_metrics = true;
    opt.append_audit = true;
    if (Persistence::save_all(session_, opt)) {
        Notifications::get().success("Annotations saved.");
        save_note_.clear();
    } else {
        Notifications::get().error("Save failed: " + Persistence::last_error());
    }
}

void AnnotationStudio::reload_() {
    if (!session_.is_loaded()) return;
    auto dir = session_.path();
    load_session_(dir);
    Notifications::get().info("Reloaded from disk.");
}

void AnnotationStudio::seek_(double t_unified_s) {
    playhead_t_s_ = t_unified_s;
    timeline_.set_playhead(t_unified_s);
}

void AnnotationStudio::select_rep_(int rep_index) {
    if (rep_index < 0 || rep_index >= (int)session_.reps().size()) return;
    timeline_.set_selected_rep(rep_index);
    timeline_.center_on_rep(rep_index);
    seek_(session_.reps()[rep_index].concentric.t_start_s);
}

} // namespace vbt

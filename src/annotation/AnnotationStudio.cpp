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
    quality_panel_.set_seek_cb([this](double t){ seek_(t); });
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

    // Hotkeys.
    if (ImGui::IsKeyPressed(ImGuiKey_S, false) && ImGui::GetIO().KeyCtrl)
        show_save_dialog_ = session_.dirty();
    if (ImGui::IsKeyPressed(ImGuiKey_Z, false) && !session_.reps().empty()) {
        int s = rep_table_.selected_index();
        select_rep_(std::max(0, s - 1));
    }
    if (ImGui::IsKeyPressed(ImGuiKey_X, false) && !session_.reps().empty()) {
        int s = rep_table_.selected_index();
        select_rep_(std::min((int)session_.reps().size() - 1, s + 1));
    }

    // 3-pane layout via stacked child windows (Columns API leaks into
    // siblings whenever a child Selectable + SameLine combo overflows
    // horizontally, which is why the previous attempt left the right
    // column empty). Computing widths from the available content region
    // keeps every pane self-contained and lets each scroll independently.
    const float total_w = ImGui::GetContentRegionAvail().x;
    const float total_h = ImGui::GetContentRegionAvail().y;
    const float left_w  = std::min(320.0f, total_w * 0.22f);
    const float right_w = std::min(420.0f, total_w * 0.28f);
    const float center_w = std::max(400.0f, total_w - left_w - right_w - 16.0f);

    ImGui::BeginChild("##studio_left", ImVec2(left_w, total_h), true);
    render_session_browser_();
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##studio_center", ImVec2(center_w, total_h), false);
    render_workspace_();
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##studio_right", ImVec2(right_w, total_h), true);
    meta_panel_.render();
    ImGui::EndChild();

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

    // Lay out the three workspace stacks proportional to the column's
    // current height so nothing clips when the user resizes the window.
    const float h = ImGui::GetContentRegionAvail().y;
    const float h_video    = std::max(220.0f, h * 0.36f);
    const float h_timeline = std::max(260.0f, h * 0.42f);
    // The bottom tab block fills whatever remains; ImGui::BeginTabBar
    // figures the size from its parent child, so leave a sensible floor.
    const float h_tabs     = std::max(180.0f, h - h_video - h_timeline - 16.0f);

    ImGui::BeginChild("##videopane", ImVec2(0, h_video), true);
    playhead_t_s_ = video_panel_.render(playhead_t_s_, session_.t0_unified_s());
    ImGui::EndChild();

    ImGui::BeginChild("##timelinepane", ImVec2(0, h_timeline), true);
    playhead_t_s_ = timeline_.render(playhead_t_s_);
    if (timeline_.consume_dirty_flag()) {
        // any timeline edit already marked the SessionData dirty.
    }
    ImGui::EndChild();

    ImGui::BeginChild("##bottompane", ImVec2(0, h_tabs), true);
    if (ImGui::BeginTabBar("##bottomtabs")) {
        if (ImGui::BeginTabItem("Reps")) {
            rep_table_.render();
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

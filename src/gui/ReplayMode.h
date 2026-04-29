#pragma once

/**
 * @file ReplayMode.h
 * @brief Load a saved session and inspect its rep annotations + metadata.
 *
 * Reads:
 *   <session_dir>/metadata.json       — SessionInfo (schema-versioned)
 *   <session_dir>/annotations/rep_segments.json
 *   <session_dir>/manifest.json        — checksums (verified)
 *   <session_dir>/events.jsonl         — operator/system event log
 *
 * Inspection-only (does not replay live sensor playback yet, since that
 * needs a streaming binary reader; the rep table + metadata + event log
 * cover most diagnostic needs).
 */

#include "app/Application.h"
#include "processing/RepSegmenter.h"
#include "utils/Notifications.h"
#include <imgui.h>
#include <string>
#include <fstream>
#include <vector>
#include <filesystem>
#include <nlohmann/json.hpp>

namespace fs = std::filesystem;

namespace vbt {

class ReplayMode {
public:
    explicit ReplayMode(Application& app) : app_(app) {}

    void open() { is_open_ = true; refresh_session_list(); }
    bool is_open() const { return is_open_; }

    void render() {
        if (!is_open_) return;
        ImGui::SetNextWindowSize(ImVec2(900, 600), ImGuiCond_FirstUseEver);
        if (!ImGui::Begin("Session Replay / Inspection", &is_open_)) {
            ImGui::End();
            return;
        }

        if (ImGui::Button("Refresh")) refresh_session_list();
        ImGui::SameLine();
        ImGui::TextDisabled("Browsing %s", app_.config().dataset_root.c_str());

        ImGui::Columns(2, nullptr, true);
        ImGui::SetColumnWidth(0, 280);

        // Left: session list
        ImGui::TextColored(ImVec4(0.55f,0.78f,1,1), "Sessions");
        ImGui::Separator();
        ImGui::BeginChild("##slist", ImVec2(0,0), true);
        for (size_t i = 0; i < sessions_.size(); ++i) {
            bool sel = (selected_idx_ == (int)i);
            if (ImGui::Selectable(sessions_[i].label.c_str(), sel)) {
                selected_idx_ = (int)i;
                load_session(sessions_[i].path);
            }
        }
        ImGui::EndChild();

        ImGui::NextColumn();

        // Right: details
        if (selected_idx_ < 0) {
            ImGui::TextDisabled("Select a session to inspect.");
        } else {
            render_details();
        }
        ImGui::Columns(1);
        ImGui::End();
    }

private:
    struct Entry { std::string path; std::string label; };

    void refresh_session_list() {
        sessions_.clear();
        const auto& root = app_.config().dataset_root;
        if (!fs::exists(root)) return;
        std::error_code ec;
        for (auto& p : fs::recursive_directory_iterator(root, ec)) {
            if (ec) break;
            if (p.is_regular_file() && p.path().filename() == "metadata.json") {
                Entry e;
                e.path  = p.path().parent_path().string();
                e.label = fs::relative(p.path().parent_path(), root).generic_string();
                if (e.path.find(".partial") != std::string::npos) e.label += " [PARTIAL]";
                sessions_.push_back(e);
            }
        }
        std::sort(sessions_.begin(), sessions_.end(),
                  [](const Entry& a, const Entry& b){ return a.label < b.label; });
    }

    void load_session(const std::string& path) {
        loaded_path_ = path;
        meta_.clear();
        reps_.clear();
        events_.clear();
        manifest_.clear();
        try {
            std::ifstream m(path + "/metadata.json");
            if (m) m >> meta_;
            std::ifstream r(path + "/annotations/rep_segments.json");
            if (r) {
                nlohmann::json rj; r >> rj;
                if (rj.contains("reps") && rj["reps"].is_array()) {
                    for (auto& el : rj["reps"]) reps_.push_back(RepAnnotation::from_json(el));
                } else if (rj.is_array()) {
                    for (auto& el : rj) reps_.push_back(RepAnnotation::from_json(el));
                }
            }
            std::ifstream mf(path + "/manifest.json");
            if (mf) mf >> manifest_;
            std::ifstream e(path + "/events.jsonl");
            std::string line;
            while (std::getline(e, line)) {
                if (line.empty()) continue;
                try { events_.push_back(nlohmann::json::parse(line)); }
                catch (...) { /* ignore malformed lines */ }
            }
        } catch (const std::exception& ex) {
            Notifications::get().error(std::string("Failed to load session: ") + ex.what());
        }
    }

    void render_details() {
        ImGui::TextColored(ImVec4(0.55f,0.78f,1,1), "%s", loaded_path_.c_str());
        ImGui::Separator();

        if (ImGui::CollapsingHeader("Metadata", ImGuiTreeNodeFlags_DefaultOpen)) {
            std::string dump = meta_.dump(2);
            ImGui::InputTextMultiline("##meta", &dump[0], dump.size(),
                ImVec2(-1, 220), ImGuiInputTextFlags_ReadOnly);
        }
        if (ImGui::CollapsingHeader("Reps")) {
            if (ImGui::BeginTable("##rrep", 5,
                ImGuiTableFlags_Borders | ImGuiTableFlags_RowBg | ImGuiTableFlags_ScrollY,
                ImVec2(0, 200))) {
                ImGui::TableSetupColumn("ID", ImGuiTableColumnFlags_WidthFixed, 40);
                ImGui::TableSetupColumn("MV"); ImGui::TableSetupColumn("PV");
                ImGui::TableSetupColumn("ROM");
                ImGui::TableSetupColumn("Source");
                ImGui::TableHeadersRow();
                for (const auto& r : reps_) {
                    ImGui::TableNextRow(); ImGui::TableNextColumn();
                    ImGui::Text("%d", r.rep_id); ImGui::TableNextColumn();
                    ImGui::Text("%.3f", r.mean_concentric_velocity); ImGui::TableNextColumn();
                    ImGui::Text("%.3f", r.peak_concentric_velocity); ImGui::TableNextColumn();
                    ImGui::Text("%.0f cm", r.rom_m * 100); ImGui::TableNextColumn();
                    ImGui::Text("%s", r.concentric.source.c_str());
                }
                ImGui::EndTable();
            }
        }
        if (ImGui::CollapsingHeader("Manifest")) {
            std::string dump = manifest_.dump(2);
            ImGui::InputTextMultiline("##manifest", &dump[0], dump.size(),
                ImVec2(-1, 160), ImGuiInputTextFlags_ReadOnly);
        }
        if (ImGui::CollapsingHeader("Events")) {
            if (events_.empty()) ImGui::TextDisabled("No events.");
            else {
                ImGui::BeginChild("##evt", ImVec2(0, 200), true);
                for (auto& ev : events_) {
                    std::string lvl = ev.value("level", "info");
                    ImVec4 col = ImVec4(0.85f, 0.85f, 0.85f, 1);
                    if (lvl == "warning") col = ImVec4(1, 0.78f, 0.25f, 1);
                    else if (lvl == "error") col = ImVec4(1, 0.30f, 0.25f, 1);
                    else if (lvl == "info" && ev.value("source","") == "session") col = ImVec4(0.55f, 0.78f, 1, 1);
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
        }
    }

    Application&         app_;
    bool                 is_open_ = false;
    std::vector<Entry>   sessions_;
    int                  selected_idx_ = -1;
    std::string          loaded_path_;
    nlohmann::json       meta_, manifest_;
    std::vector<RepAnnotation> reps_;
    std::vector<nlohmann::json> events_;
};

} // namespace vbt

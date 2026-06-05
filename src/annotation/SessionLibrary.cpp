/**
 * @file SessionLibrary.cpp
 */
#include "annotation/SessionLibrary.h"
#include <nlohmann/json.hpp>
#include <fstream>
#include <algorithm>
#include <cctype>

namespace fs = std::filesystem;

namespace vbt {

namespace {
std::string lower(std::string s) {
    for (auto& c : s) c = (char)std::tolower((unsigned char)c);
    return s;
}
} // namespace

void SessionLibrary::refresh() {
    sessions_.clear();
    if (root_.empty() || !fs::exists(root_)) return;
    std::error_code ec;
    for (auto& p : fs::recursive_directory_iterator(root_, ec)) {
        if (ec) break;
        if (!p.is_regular_file()) continue;
        if (p.path().filename() != "metadata.json") continue;
        SessionSummary s;
        s.dir = p.path().parent_path();
        s.label = fs::relative(s.dir, root_).generic_string();
        s.partial = s.dir.extension() == ".partial"
                    || s.dir.string().find(".partial") != std::string::npos;
        load_summary_(p.path(), s);
        // Probe key files for the "has_video"/"has_imu" badges.
        s.has_imu   = fs::exists(s.dir / "imu" / "raw_imu.csv");
        s.has_video = fs::exists(s.dir / "camera" / "ir_video.mp4");
        sessions_.push_back(std::move(s));
    }
    std::sort(sessions_.begin(), sessions_.end(),
              [](const SessionSummary& a, const SessionSummary& b) {
                  if (a.date != b.date) return a.date > b.date;  // newest first
                  return a.label < b.label;
              });
}

void SessionLibrary::load_summary_(const fs::path& meta_path, SessionSummary& s) const {
    std::ifstream f(meta_path);
    if (!f.is_open()) return;
    nlohmann::json j;
    try { f >> j; } catch (...) { return; }
    s.subject_id   = j.value("subject_id", std::string{});
    s.exercise     = j.value("exercise", std::string{"unknown"});
    s.variant      = j.value("exercise_variant", std::string{});
    s.date         = j.value("date", std::string{});
    s.total_weight_kg = j.value("total_weight_kg", 0.0f);
    s.set_number   = j.value("set_number", 0);
    s.target_reps  = j.value("target_reps", 0);
    s.rpe          = j.value("rpe", 0);

    auto count_reps = [](const fs::path& p) -> int {
        std::ifstream r(p);
        if (!r.is_open()) return 0;
        try {
            nlohmann::json rj; r >> rj;
            const auto& arr = rj.is_array() ? rj : rj.contains("reps") ? rj["reps"] : rj;
            if (arr.is_array()) return (int)arr.size();
        } catch (...) {}
        return 0;
    };

    // Final annotations and post-session proposals are separate. Show both
    // counts in the browser so a session with only candidate annotations does
    // not look like it has no detected reps.
    fs::path ann_dir = meta_path.parent_path() / "annotations";
    s.rep_count = count_reps(ann_dir / "rep_segments.json");
    s.proposal_count = count_reps(ann_dir / "rep_segments.candidate.json");
    s.post_session_count = count_reps(ann_dir / "rep_segments.post_session.json");
}

std::vector<int> SessionLibrary::filter(const std::string& query) const {
    std::vector<int> out;
    out.reserve(sessions_.size());
    if (query.empty()) {
        for (int i = 0; i < (int)sessions_.size(); ++i) out.push_back(i);
        return out;
    }
    std::string q = lower(query);
    for (int i = 0; i < (int)sessions_.size(); ++i) {
        const auto& s = sessions_[i];
        if (lower(s.label).find(q) != std::string::npos
            || lower(s.subject_id).find(q) != std::string::npos
            || lower(s.exercise).find(q) != std::string::npos
            || lower(s.variant).find(q) != std::string::npos) {
            out.push_back(i);
        }
    }
    return out;
}

std::string SessionLibrary::format_label(const SessionSummary& s, int max_chars) {
    std::string out = s.label;
    if (s.partial) out += " [PARTIAL]";
    if ((int)out.size() > max_chars) {
        out = out.substr(0, std::max(0, max_chars - 1)) + "…";
    }
    return out;
}

} // namespace vbt

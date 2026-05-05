#include "utils/SubjectRegistry.h"
#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>
#include <fstream>
#include <regex>
#include <unordered_map>

namespace fs = std::filesystem;

namespace vbt {

namespace {

// Lightweight read of just the fields we care about. We intentionally
// avoid full SessionInfo deserialization to keep the scan fast and
// resilient to schema drift.
struct LiteRecord {
    std::string uuid;
    std::string id;
    std::string name;
    fs::file_time_type mtime;
    fs::path session_dir;
};

bool read_lite(const fs::path& meta_path, LiteRecord& out) {
    std::ifstream f(meta_path);
    if (!f.is_open()) return false;
    nlohmann::json j;
    try { f >> j; } catch (...) { return false; }
    out.uuid = j.value("subject_uuid", "");
    out.id   = j.value("subject_id",   "");
    out.name = j.value("subject_name", "");
    std::error_code ec;
    out.mtime = fs::last_write_time(meta_path, ec);
    out.session_dir = meta_path.parent_path();
    return true;
}

void scan_dir_for_metadata(const fs::path& dir, std::vector<LiteRecord>& out) {
    std::error_code ec;
    if (!fs::exists(dir, ec) || !fs::is_directory(dir, ec)) return;
    for (auto& entry : fs::directory_iterator(dir, fs::directory_options::skip_permission_denied, ec)) {
        if (!entry.is_directory()) continue;
        fs::path m = entry.path() / "metadata.json";
        if (fs::exists(m, ec)) {
            LiteRecord lr;
            if (read_lite(m, lr)) out.push_back(std::move(lr));
        }
    }
}

} // namespace

std::vector<SubjectRecord> scan_subject_registry(const fs::path& dataset_root) {
    std::vector<LiteRecord> raw;
    scan_dir_for_metadata(dataset_root / "sessions", raw);
    // BIDS layout: sub-XXX/ses-YYYY-MM-DD/metadata.json — walk one extra level.
    std::error_code ec;
    if (fs::exists(dataset_root, ec)) {
        for (auto& entry : fs::directory_iterator(dataset_root, ec)) {
            if (!entry.is_directory()) continue;
            const std::string n = entry.path().filename().string();
            if (n.rfind("sub-", 0) == 0) scan_dir_for_metadata(entry.path(), raw);
        }
    }

    // Group by subject_uuid (skip rows with no UUID — pre-v4 schema).
    std::unordered_map<std::string, SubjectRecord> by_uuid;
    for (auto& lr : raw) {
        if (lr.uuid.empty()) continue;
        auto it = by_uuid.find(lr.uuid);
        if (it == by_uuid.end()) {
            SubjectRecord r;
            r.subject_uuid   = lr.uuid;
            r.subject_id     = lr.id;
            r.subject_name   = lr.name;
            r.latest_session = lr.session_dir;
            by_uuid[lr.uuid] = std::move(r);
        } else {
            // Keep the most recent session_dir for this subject.
            std::error_code ec2;
            auto cur_mtime = fs::last_write_time(it->second.latest_session, ec2);
            if (lr.mtime > cur_mtime) {
                it->second.latest_session = lr.session_dir;
                if (!lr.id.empty())   it->second.subject_id   = lr.id;
                if (!lr.name.empty()) it->second.subject_name = lr.name;
            }
        }
    }
    std::vector<SubjectRecord> out;
    out.reserve(by_uuid.size());
    for (auto& [_, v] : by_uuid) out.push_back(std::move(v));
    return out;
}

std::optional<SubjectRecord> lookup_subject_by_uuid(const fs::path& dataset_root,
                                                     const std::string& uuid) {
    if (uuid.empty()) return std::nullopt;
    for (auto& r : scan_subject_registry(dataset_root)) {
        if (r.subject_uuid == uuid) return r;
    }
    return std::nullopt;
}

std::string next_subject_id(const fs::path& dataset_root) {
    static const std::regex pat(R"(^S(\d+)$)");
    int max_n = 0;
    auto records = scan_subject_registry(dataset_root);
    for (auto& r : records) {
        std::smatch m;
        if (std::regex_match(r.subject_id, m, pat)) {
            try { max_n = std::max(max_n, std::stoi(m[1].str())); }
            catch (...) {}
        }
    }
    char buf[8];
    std::snprintf(buf, sizeof(buf), "S%02d", max_n + 1);
    return std::string(buf);
}

} // namespace vbt

#include "annotation/GroundTruthIO.h"

#include <algorithm>
#include <cmath>
#include <fstream>
#include <sstream>

#include <nlohmann/json.hpp>
#include <spdlog/spdlog.h>

namespace fs = std::filesystem;

namespace vbt::ground_truth_io {

std::vector<GroundTruthLabel> reps_to_labels(
    const std::vector<RepAnnotation>& reps,
    const std::vector<GtAttr>& attrs,
    const TimeToFrame& t2f) {
    std::vector<GroundTruthLabel> out;
    out.reserve(reps.size());
    for (size_t i = 0; i < reps.size(); ++i) {
        const RepAnnotation& r = reps[i];
        GroundTruthLabel g;
        g.set_id = r.set_id > 0 ? r.set_id : 1;
        g.concentric_start_frame = t2f(r.concentric.t_start_s);
        g.concentric_end_frame   = t2f(r.concentric.t_end_s);
        // eccentric present only if it has a non-degenerate span
        if (r.eccentric.t_end_s > r.eccentric.t_start_s + 1e-6) {
            g.eccentric_start_frame = t2f(r.eccentric.t_start_s);
            g.eccentric_end_frame   = t2f(r.eccentric.t_end_s);
        }
        g.rom = r.rom_m;
        if (i < attrs.size()) {
            const GtAttr& a = attrs[i];
            g.status           = a.status;
            g.rom_completeness = a.rom_completeness;
            g.has_pause        = a.has_pause;
            g.pause_kind       = a.pause_kind;
            g.source           = a.source;
        }
        out.push_back(std::move(g));
    }
    return out;
}

void labels_to_reps(
    const std::vector<GroundTruthLabel>& labels, const FrameToTime& f2t,
    std::vector<RepAnnotation>& reps_out, std::vector<GtAttr>& attrs_out) {
    reps_out.clear();
    attrs_out.clear();
    reps_out.reserve(labels.size());
    attrs_out.reserve(labels.size());
    int rep_id = 1;
    for (const GroundTruthLabel& g : labels) {
        RepAnnotation r;
        r.rep_id = rep_id++;
        r.set_id = g.set_id;
        r.concentric.phase     = RepPhase::CONCENTRIC;
        r.concentric.t_start_s = f2t(g.concentric_start_frame);
        r.concentric.t_end_s   = f2t(g.concentric_end_frame);
        r.concentric.source    = g.source;
        r.t_start_s = r.concentric.t_start_s;
        // a top pause sits between the concentric end and the eccentric start
        r.top_rest.phase = RepPhase::REST;
        r.top_rest.t_start_s = r.concentric.t_end_s;
        r.top_rest.t_end_s   = r.concentric.t_end_s;
        if (g.eccentric_start_frame >= 0 && g.eccentric_end_frame >= 0) {
            r.eccentric.phase     = RepPhase::ECCENTRIC;
            r.eccentric.t_start_s = f2t(g.eccentric_start_frame);
            r.eccentric.t_end_s   = f2t(g.eccentric_end_frame);
            r.eccentric.source    = g.source;
            r.top_rest.t_end_s    = r.eccentric.t_start_s;   // pause span (may be 0)
            r.t_end_s = r.eccentric.t_end_s;
        } else {
            r.eccentric.t_start_s = r.concentric.t_end_s;
            r.eccentric.t_end_s   = r.concentric.t_end_s;
            r.t_end_s = r.concentric.t_end_s;
        }
        r.rom_m = static_cast<float>(g.rom);
        reps_out.push_back(std::move(r));

        GtAttr a;
        a.status           = g.status;
        a.has_pause        = g.has_pause;
        a.pause_kind       = g.pause_kind;
        a.rom_completeness = static_cast<float>(g.rom_completeness);
        a.source           = g.source;
        attrs_out.push_back(std::move(a));
    }
}

fs::path label_dir(const fs::path& labels_root, const std::string& session_id) {
    return labels_root / session_id;
}

bool save(const fs::path& labels_root, const std::string& session_id,
          const std::vector<GroundTruthLabel>& labels, std::string& err) {
    try {
        fs::path dir = label_dir(labels_root, session_id);
        fs::create_directories(dir);
        nlohmann::json arr = nlohmann::json::array();
        for (const auto& g : labels) arr.push_back(g.to_json());

        fs::path final_path = dir / "ground_truth.json";
        fs::path tmp = dir / "ground_truth.json.tmp";
        {
            std::ofstream f(tmp, std::ios::trunc);
            if (!f) { err = "cannot open " + tmp.string(); return false; }
            f << arr.dump(2);
            f.flush();
            if (!f) { err = "write failed: " + tmp.string(); return false; }
        }
        fs::rename(tmp, final_path);   // atomic within the same dir
        spdlog::info("GroundTruthIO: saved {} labels -> {}", labels.size(),
                     final_path.string());
        return true;
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
}

bool load(const fs::path& file, std::vector<GroundTruthLabel>& out, std::string& err) {
    out.clear();
    std::ifstream f(file);
    if (!f) { err = "cannot open " + file.string(); return false; }
    try {
        nlohmann::json j;
        f >> j;
        if (!j.is_array()) { err = "ground_truth file is not a JSON array"; return false; }
        for (const auto& e : j) out.push_back(GroundTruthLabel::from_json(e));
        return true;
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
}

bool load_trace(const fs::path& csv, std::vector<TracePoint>& out, std::string& err) {
    out.clear();
    std::ifstream f(csv);
    if (!f) { err = "cannot open " + csv.string(); return false; }
    std::string line;
    if (!std::getline(f, line)) { err = "empty trace.csv"; return false; }  // header
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        std::stringstream ss(line);
        std::string cell;
        TracePoint p{};
        if (!std::getline(ss, cell, ',')) continue; p.frame_idx = std::stoi(cell);
        if (!std::getline(ss, cell, ',')) continue; p.t_s = std::stod(cell);
        if (!std::getline(ss, cell, ',')) continue; p.s = std::stod(cell);
        if (!std::getline(ss, cell, ',')) continue; p.v = std::stod(cell);
        out.push_back(p);
    }
    return true;
}

} // namespace vbt::ground_truth_io

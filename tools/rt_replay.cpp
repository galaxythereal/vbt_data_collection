/**
 * @file rt_replay.cpp
 * @brief Replay a recorded session through the REAL-TIME annotator, frame by frame.
 *
 * This is the validation harness for the online algorithm. It streams
 * camera/marker_positions.csv in order, one sample at a time, into exactly the same
 * RtAnnotator the live acquisition loop uses — no lookahead, no whole-session
 * statistics, no second pass. Whatever it prints here is what the annotator would have
 * produced live, so the 84-session table it generates is the honest answer to
 * "what labelled the data during collection?".
 *
 * Usage:
 *   rt_replay <session_dir> [more_session_dirs...]     # per-rep detail + summary
 *   rt_replay --summary <session_dir>...               # one line per session
 *   rt_replay --csv out.csv <session_dir>...           # machine-readable summary
 *
 * Reads only; writes nothing into the dataset.
 */

#include "rt_annotator/RtAnnotator.h"
#include "rt_annotator/RtAnnotationIO.h"

#include <algorithm>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using namespace vbt::rt;

namespace {

std::vector<std::string> split(const std::string& line, char sep) {
    std::vector<std::string> out;
    std::string cell;
    std::stringstream ss(line);
    while (std::getline(ss, cell, sep)) out.push_back(cell);
    return out;
}

/// Minimal scan for "exercise": "..." — avoids pulling a JSON dependency into the tool.
std::string read_exercise(const fs::path& metadata) {
    std::ifstream f(metadata);
    if (!f) return {};
    std::string all((std::istreambuf_iterator<char>(f)), std::istreambuf_iterator<char>());
    const std::string key = "\"exercise\"";
    auto k = all.find(key);
    if (k == std::string::npos) return {};
    auto c = all.find(':', k + key.size());
    if (c == std::string::npos) return {};
    auto q1 = all.find('"', c);
    if (q1 == std::string::npos) return {};
    auto q2 = all.find('"', q1 + 1);
    if (q2 == std::string::npos) return {};
    return all.substr(q1 + 1, q2 - q1 - 1);
}

bool is_down_first(const std::string& exercise) {
    // The cycle opens with the eccentric for these two; the others start from the bottom.
    return exercise == "bench_press" || exercise == "back_squat";
}

/// Nominal ROM per lift (metres) — physiological priors, identical to the values in
/// vbt_gt/config.py EXERCISE_CONFIG. Used only to establish that a movement is on the
/// scale of a human repetition; NOT fitted to any session.
double rom_prior_for(const std::string& exercise) {
    if (exercise == "deadlift")    return 0.60;
    if (exercise == "back_squat")  return 0.55;
    if (exercise == "bench_press") return 0.45;
    return 0.50;   // biceps_curl, barbell_row
}

struct Result {
    std::string session, exercise;
    int  frames = 0, provisional = 0, confirmed = 0, turnarounds = 0;
    int  dropped_ecc = 0, with_gap = 0;
    double median_latency_frames = 0.0;
    bool ok = false;
    std::string error;
    std::vector<RtRep> reps;
};

Result replay(const fs::path& dir, bool verbose) {
    Result R;
    R.session = dir.filename().string();
    R.exercise = read_exercise(dir / "metadata.json");

    std::ifstream f(dir / "camera" / "marker_positions.csv");
    if (!f) { R.error = "cannot open marker_positions.csv"; return R; }

    std::string header;
    if (!std::getline(f, header)) { R.error = "empty csv"; return R; }
    auto cols = split(header, ',');
    std::map<std::string, int> ix;
    for (size_t i = 0; i < cols.size(); ++i) ix[cols[i]] = static_cast<int>(i);
    for (const char* need : {"y_m", "detected", "confidence"})
        if (!ix.count(need)) { R.error = std::string("missing column ") + need; return R; }

    RtAnnotator::Config cfg;
    cfg.down_first  = is_down_first(R.exercise);
    cfg.rom_prior_m = rom_prior_for(R.exercise);
    if (const char* mf = std::getenv("RT_MIN_REP_FRAC")) cfg.min_rep_frac = std::atof(mf);
    if (const char* rf = std::getenv("RT_RETURN_FRAC"))  cfg.return_frac  = std::atof(rf);
    RtAnnotator ann(cfg);

    // The camera-only time base: t = frame_idx / 90, exactly as the pipeline defines it.
    constexpr double kFps = 90.0;
    std::string line;
    int64_t frame = 0;
    std::vector<double> latency;   // frames between lockout and confirmation
    size_t confirmed_seen = 0;

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split(line, ',');
        if (v.size() < cols.size()) continue;
        RtSample s;
        s.frame_idx  = frame;
        s.t_s        = static_cast<double>(frame) / kFps;
        s.y_m        = std::atof(v[ix["y_m"]].c_str());
        s.detected   = std::atoi(v[ix["detected"]].c_str()) != 0;
        s.confidence = std::atof(v[ix["confidence"]].c_str());
        ann.push(s);

        // measure how long after lockout a rep becomes confirmed
        const auto& reps = ann.reps();
        while (confirmed_seen < reps.size()) {
            const auto& r = reps[confirmed_seen];
            if (!r.confirmed) break;
            latency.push_back(static_cast<double>(frame - r.concentric_end_frame));
            ++confirmed_seen;
        }
        ++frame;
    }

    R.frames      = static_cast<int>(frame);
    R.provisional = ann.provisional_count();
    R.confirmed   = ann.confirmed_count();
    R.turnarounds = static_cast<int>(ann.turnarounds().size());
    R.reps        = ann.reps();
    for (const auto& r : R.reps) {
        if (r.dropped_eccentric) ++R.dropped_ecc;
        if (r.tracking_gap)      ++R.with_gap;
    }
    if (!latency.empty()) {
        std::sort(latency.begin(), latency.end());
        R.median_latency_frames = latency[latency.size() / 2];
    }
    R.ok = true;

    if (verbose) {
        std::printf("\n=== %s  [%s]  %d frames ===\n", R.session.c_str(),
                    R.exercise.c_str(), R.frames);
        std::printf("%4s %10s %10s %10s %10s %10s %10s %10s %10s %8s %8s %6s %s\n",
                    "rep", "con_start", "con_end", "ecc_start", "ecc_end",
                    "trest_s", "trest_e", "brest_s", "brest_e",
                    "rom_m", "peak_v", "conf", "flags");
        for (const auto& r : R.reps) {
            std::printf("%4d %10lld %10lld %10lld %10lld %10lld %10lld %10lld %10lld"
                        " %8.3f %8.3f %6s %s%s\n",
                        r.rep_id,
                        (long long)r.concentric_start_frame,  (long long)r.concentric_end_frame,
                        (long long)r.eccentric_start_frame,   (long long)r.eccentric_end_frame,
                        (long long)r.top_rest_start_frame,    (long long)r.top_rest_end_frame,
                        (long long)r.bottom_rest_start_frame, (long long)r.bottom_rest_end_frame,
                        r.rom_m, r.peak_velocity,
                        r.confirmed ? "yes" : "NO",
                        r.dropped_eccentric ? "dropped_ecc " : "",
                        r.tracking_gap ? "tracking_gap" : "");
        }
    }
    return R;
}

} // namespace

int main(int argc, char** argv) {
    std::vector<fs::path> dirs;
    bool summary_only = false;
    bool write_sessions = false;   // write camera/rt_annotation.csv INTO each session
    std::string csv_out;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--summary") summary_only = true;
        else if (a == "--csv" && i + 1 < argc) csv_out = argv[++i];
        else if (a == "--write") write_sessions = true;
        else dirs.emplace_back(a);
    }
    if (dirs.empty()) {
        std::fprintf(stderr,
            "usage: rt_replay [--summary] [--csv out.csv] <session_dir>...\n");
        return 2;
    }

    std::vector<Result> results;
    for (const auto& d : dirs) {
        results.push_back(replay(d, !summary_only && csv_out.empty()));
        // Backfill: produce the same camera/rt_annotation.csv a live recording would
        // have written, so already-captured sessions open in the studio with the
        // real-time annotation as their default prefill.
        if (write_sessions && results.back().ok) {
            std::string werr;
            if (!vbt::rt::rt_write_csv(d.string(), results.back().exercise,
                                       results.back().reps, werr))
                std::fprintf(stderr, "write failed for %s: %s\n",
                             d.string().c_str(), werr.c_str());
        }
    }

    std::printf("\n%-28s %-13s %7s %7s %7s %7s %9s\n",
                "session", "exercise", "frames", "prov", "CONF", "turns", "lat_frm");
    for (const auto& r : results) {
        if (!r.ok) { std::printf("%-28s  SKIP: %s\n", r.session.c_str(), r.error.c_str()); continue; }
        std::printf("%-28s %-13s %7d %7d %7d %7d %9.0f\n",
                    r.session.c_str(), r.exercise.c_str(), r.frames,
                    r.provisional, r.confirmed, r.turnarounds, r.median_latency_frames);
    }

    if (!csv_out.empty()) {
        std::ofstream o(csv_out);
        o << "session_id,exercise,frames,provisional,confirmed,turnarounds,"
             "dropped_ecc,with_gap,median_latency_frames\n";
        for (const auto& r : results) {
            if (!r.ok) continue;
            o << r.session << ',' << r.exercise << ',' << r.frames << ','
              << r.provisional << ',' << r.confirmed << ',' << r.turnarounds << ','
              << r.dropped_ecc << ',' << r.with_gap << ','
              << r.median_latency_frames << '\n';
        }
        std::printf("\nwrote %s\n", csv_out.c_str());
    }
    return 0;
}

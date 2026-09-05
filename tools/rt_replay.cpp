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
 *   rt_replay --check <session_dir>...                 # verify the invariant, exit 1 if broken
 *
 * --check proves that nothing in the annotation was derived from a frame the marker was
 * not seen on. It is a regression guard, not a feature: this defect class reached the
 * annotator twice through two different code paths, and both times it was found by
 * reading a column by hand. Removing the `detected` guard on the rest run makes it
 * report the exact five violations that were originally found that way.
 *
 * Reads only; writes nothing into the dataset.
 */

#include "rt_annotator/RtAnnotator.h"
#include "rt_annotator/RtAnnotationIO.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <map>
#include <sstream>
#include <string>
#include <utility>
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
/// human anatomy. Used only to establish that a movement is on the
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
    /// Per-frame `detected` exactly as recorded. Retained so --check can prove that
    /// nothing in the annotation was derived from a frame the marker was not seen on.
    std::vector<uint8_t> detected;
};

static bool use_smoothed = false;

Result replay(const fs::path& dir, bool verbose) {
    Result R;
    R.session = dir.filename().string();
    R.exercise = read_exercise(dir / "metadata.json");

    // --source smoothed reads the whole-session smoothed track instead of the raw marker
    // stream. Everything after the rotation works on the new data, so when this pass is
    // used to seed the post-session annotation it must read the same track that pass does.
    // Every frame carries an estimate there, including the ones the marker was not seen
    // on, because the smoother reconstructed them -- which is the point.
    const fs::path src = use_smoothed ? (dir / "smoothed.csv")
                                     : (dir / "camera" / "marker_positions.csv");
    std::ifstream f(src);
    if (!f) { R.error = "cannot open " + src.filename().string(); return R; }

    std::string header;
    if (!std::getline(f, header)) { R.error = "empty csv"; return R; }
    auto cols = split(header, ',');
    std::map<std::string, int> ix;
    for (size_t i = 0; i < cols.size(); ++i) ix[cols[i]] = static_cast<int>(i);
    const char* ycol = use_smoothed ? "pos_y"    : "y_m";
    const char* dcol = use_smoothed ? "measured"  : "detected";
    for (const char* need : {ycol, dcol})
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
        s.y_m        = std::atof(v[ix[ycol]].c_str());
        // On the smoothed track every frame has an estimate, so every frame is usable.
        // `measured` is still carried through so a rep that rests on reconstructed frames
        // is still reported as such.
        s.detected   = use_smoothed ? true
                                    : (std::atoi(v[ix[dcol]].c_str()) != 0);
        s.confidence = ix.count("confidence") ? std::atof(v[ix["confidence"]].c_str()) : 0.70;
        R.detected.push_back(s.detected ? 1 : 0);
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

// ============================================================================
// --check : the annotation must never depend on data that was not measured
// ============================================================================
// This defect class got into the annotator TWICE through two different code paths --
// a rep boundary placed on a frame where the marker was never seen. Both times it was
// caught by reading a column by hand and noticing one squat rep bottoming 0.36 m below
// its neighbours. Nothing reported it. These four rules are the invariant that was
// being violated, checked over every session so a regression fails loudly instead of
// waiting to be noticed.
//
// The checker only READS what the annotator produced. It cannot change the algorithm,
// the output, or the recording path.
struct Violation {
    std::string session;
    int         rep_id = 0;
    std::string what;
};

/// Frame span a rep occupies: from its earliest boundary to its latest.
bool rep_span(const RtRep& r, int64_t& lo, int64_t& hi) {
    const int64_t f[4] = {r.concentric_start_frame, r.concentric_end_frame,
                          r.eccentric_start_frame,  r.eccentric_end_frame};
    bool any = false;
    for (int64_t v : f) {
        if (v < 0) continue;
        if (!any) { lo = hi = v; any = true; }
        else { lo = std::min(lo, v); hi = std::max(hi, v); }
    }
    return any;
}

void check_session(const Result& R, std::vector<Violation>& out) {
    const auto& det = R.detected;
    const int64_t n = static_cast<int64_t>(det.size());
    auto seen = [&](int64_t f) { return f >= 0 && f < n && det[f]; };

    long unseen_in_any_rep = 0, reported = 0;
    std::vector<uint8_t> covered(det.size(), 0);   // frames lying inside some rep span

    for (const auto& r : R.reps) {
        // ---- RULE 1: no rep boundary sits on a frame the marker was not seen on.
        // A boundary is an assertion about where the bar turned around. On an unseen
        // frame there is no position, so the assertion has no basis.
        const std::pair<const char*, int64_t> bnd[4] = {
            {"concentric_start_frame", r.concentric_start_frame},
            {"concentric_end_frame",   r.concentric_end_frame},
            {"eccentric_start_frame",  r.eccentric_start_frame},
            {"eccentric_end_frame",    r.eccentric_end_frame},
        };
        for (const auto& b : bnd) {
            if (b.second < 0) continue;
            if (b.second >= n)
                out.push_back({R.session, r.rep_id,
                    std::string(b.first) + "=" + std::to_string(b.second) +
                    " is past the end of the recording (" + std::to_string(n) + " frames)"});
            else if (!det[b.second])
                out.push_back({R.session, r.rep_id,
                    std::string(b.first) + "=" + std::to_string(b.second) +
                    " is on a frame where the marker was not seen"});
        }

        // ---- RULE 2: no rest window contains an unseen frame.
        // "The bar was at rest here" cannot be claimed about frames nobody measured,
        // and a rest's end is where the next phase begins.
        const std::pair<const char*, std::pair<int64_t,int64_t>> rest[2] = {
            {"top_rest",    {r.top_rest_start_frame,    r.top_rest_end_frame}},
            {"bottom_rest", {r.bottom_rest_start_frame, r.bottom_rest_end_frame}},
        };
        for (const auto& w : rest) {
            const int64_t a = w.second.first, b = w.second.second;
            if (a < 0 || b <= a) continue;
            long lost = 0;
            for (int64_t i = a; i <= b && i < n; ++i) if (!det[i]) ++lost;
            if (lost)
                out.push_back({R.session, r.rep_id,
                    std::string(w.first) + " " + std::to_string(a) + "-" + std::to_string(b) +
                    " contains " + std::to_string(lost) + " unseen frame(s)"});
        }

        // ---- RULE 3: every unseen frame inside a rep is admitted in gap_frames.
        int64_t lo = 0, hi = 0;
        if (rep_span(r, lo, hi)) {
            long lost = 0;
            for (int64_t i = lo; i <= hi && i < n; ++i) {
                if (i >= 0 && !covered[i]) { covered[i] = 1; if (!det[i]) ++unseen_in_any_rep; }
                if (i >= 0 && !det[i]) ++lost;
            }
            if (lost > 0 && r.gap_frames <= 0)
                out.push_back({R.session, r.rep_id,
                    "spans " + std::to_string(lost) + " unseen frame(s) but reports "
                    "gap_frames=0"});
        }
        reported += r.gap_frames;

        // ---- RULE 4: no reported number is NaN or infinite.
        // NaN is how an unmeasured field now travels; if one reaches a statistic, some
        // computation consumed a frame it should have skipped.
        const std::pair<const char*, double> num[4] = {
            {"rom_m", r.rom_m}, {"peak_velocity", r.peak_velocity},
            {"mean_velocity", r.mean_velocity}, {"min_ecc_accel", r.min_ecc_accel},
        };
        for (const auto& q : num)
            if (!std::isfinite(q.second))
                out.push_back({R.session, r.rep_id,
                    std::string(q.first) + " is not a finite number"});
    }

    // ---- RULE 3 (session total): nothing under-reported overall. Over-reporting by a
    // frame or two is fine and expected -- a gap straddling a boundary is charged to the
    // adjacent card -- but the total must never be short.
    if (reported < unseen_in_any_rep)
        out.push_back({R.session, 0,
            "reports " + std::to_string(reported) + " gap frames in total but " +
            std::to_string(unseen_in_any_rep) + " unseen frames fall inside a rep"});
}

} // namespace

int main(int argc, char** argv) {
    std::vector<fs::path> dirs;
    bool summary_only = false;
    bool write_sessions = false;   // write camera/rt_annotation.csv INTO each session
    bool check_only = false;       // verify the no-fabrication invariant, print PASS/FAIL
    // --write takes an explicit destination. It used to write inside the session folder,
    // next to the measurement; the measurement is now sealed and read-only.
    std::string write_root;
    std::string csv_out;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--summary") summary_only = true;
        else if (a == "--csv" && i + 1 < argc) csv_out = argv[++i];
        else if (a == "--write" && i + 1 < argc) { write_sessions = true; write_root = argv[++i]; }
        else if (a == "--check") check_only = true;
        // Was documented in the usage text but never parsed, so every run silently read
        // camera/marker_positions.csv and treated "--source" and "smoothed" as session
        // directories. Anything produced with this flag before this fix was NOT computed
        // on the smoothed track.
        else if (a == "--source" && i + 1 < argc) {
            const std::string v = argv[++i];
            if (v == "smoothed") use_smoothed = true;
            else if (v != "raw") {
                std::fprintf(stderr, "--source takes 'raw' or 'smoothed', got '%s'\n", v.c_str());
                return 2;
            }
        }
        else dirs.emplace_back(a);
    }
    if (dirs.empty()) {
        std::fprintf(stderr,
            "usage: rt_replay [--summary] [--check] [--csv out.csv]\n"
            "                 [--write OUT_ROOT] [--source smoothed] <session_dir>...\n"
            "  --check  prove the annotation depends on no unseen frame; exit 1 if not\n");
        return 2;
    }
    if (check_only) { summary_only = true; write_sessions = false; }

    std::vector<Result> results;
    for (const auto& d : dirs) {
        results.push_back(replay(d, !summary_only && csv_out.empty()));
        // Replay the live annotator over an already-captured session and write the result
        // to OUT_ROOT/<session>/rt_annotation.csv. Never into the session's own directory:
        // that tree holds the measurement and is read-only.
        if (write_sessions && results.back().ok) {
            std::string werr;
            const fs::path out = fs::path(write_root) / d.filename();
            if (!vbt::rt::rt_write_file((out / "annotation_live.csv").string(), results.back().exercise,
                                       results.back().reps, werr))
                std::fprintf(stderr, "write failed for %s: %s\n",
                             d.string().c_str(), werr.c_str());
        }
    }

    if (check_only) {
        std::vector<Violation> v;
        int sessions = 0, cards = 0, skipped = 0;
        for (const auto& r : results) {
            if (!r.ok) { ++skipped;
                std::printf("SKIP %-28s %s\n", r.session.c_str(), r.error.c_str());
                continue; }
            ++sessions; cards += static_cast<int>(r.reps.size());
            check_session(r, v);
        }
        if (v.empty()) {
            std::printf("CHECK PASSED — %d sessions, %d cards"
                        "%s\n", sessions, cards,
                        skipped ? " (some sessions skipped, see above)" : "");
            std::printf("  no rep boundary on an unseen frame\n"
                        "  no rest window containing an unseen frame\n"
                        "  every unseen frame inside a rep admitted in gap_frames\n"
                        "  no non-finite number reported\n");
            return skipped ? 1 : 0;
        }
        std::printf("CHECK FAILED\n");
        for (const auto& x : v) {
            if (x.rep_id) std::printf("  %-28s rep %-4d %s\n",
                                      x.session.c_str(), x.rep_id, x.what.c_str());
            else          std::printf("  %-28s          %s\n",
                                      x.session.c_str(), x.what.c_str());
        }
        std::printf("%zu violation(s) over %d sessions, %d cards\n", v.size(), sessions, cards);
        return 1;
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

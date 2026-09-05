#include "offline/OfflinePipeline.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <fstream>
#include <map>
#include <cstdlib>
#include <sstream>

#include <nlohmann/json.hpp>

#include "rt_annotator/RtAnnotator.h"

namespace fs = std::filesystem;

namespace vbt::offline {
namespace {

constexpr double kFps = 90.0;

std::vector<std::string> split(const std::string& s, char d) {
    std::vector<std::string> out; std::string cur;
    std::istringstream is(s);
    while (std::getline(is, cur, d)) out.push_back(cur);
    return out;
}

/// Column name -> index, from a CSV header line. Trailing \r is stripped: a header
/// written by a tool that used \r\n once made the reader see "detected\r" and silently
/// skip every session.
std::map<std::string, size_t> header_index(const std::string& line) {
    std::map<std::string, size_t> ix;
    auto cols = split(line, ',');
    for (size_t i = 0; i < cols.size(); ++i) {
        std::string c = cols[i];
        while (!c.empty() && (c.back() == '\r' || c.back() == '\n' || c.back() == ' ')) c.pop_back();
        ix[c] = i;
    }
    return ix;
}

double to_d(const std::string& s) {
    try { return std::stod(s); } catch (...) { return std::nan(""); }
}

} // namespace

bool OfflinePipeline::is_down_first(const std::string& e) {
    return e == "bench_press" || e == "back_squat";
}

double OfflinePipeline::rom_prior_for(const std::string& e) {
    if (e == "deadlift")    return 0.60;
    if (e == "back_squat")  return 0.55;
    if (e == "bench_press") return 0.45;
    return 0.50;
}

GravityFrame OfflinePipeline::gravity_frame(const fs::path& camera_imu_csv) {
    GravityFrame gf;
    std::ifstream f(camera_imu_csv);
    if (!f) return gf;
    std::string line;
    if (!std::getline(f, line)) return gf;
    auto ix = header_index(line);
    if (!ix.count("kind") || !ix.count("x") || !ix.count("y") || !ix.count("z")) return gf;

    std::vector<std::array<double, 3>> A;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split(line, ',');
        if (v.size() <= ix["z"]) continue;
        if (v[ix["kind"]] != "accel") continue;
        A.push_back({to_d(v[ix["x"]]), to_d(v[ix["y"]]), to_d(v[ix["z"]])});
    }
    // The still period before the set. An accelerometer at rest reads specific force,
    // which points UP.
    if (A.size() < 200) return gf;
    const size_t n = std::max<size_t>(200, A.size() / 5);
    double g[3] = {0, 0, 0};
    for (size_t i = 0; i < n; ++i) for (int k = 0; k < 3; ++k) g[k] += A[i][k];
    for (int k = 0; k < 3; ++k) g[k] /= (double)n;
    const double mag = std::sqrt(g[0]*g[0] + g[1]*g[1] + g[2]*g[2]);
    if (!(mag > 1e-6)) return gf;

    // DIRECTION ONLY. Normalising cancels this part's ~6% scale error exactly.
    double up[3] = {g[0]/mag, g[1]/mag, g[2]/mag};
    double down[3] = {-up[0], -up[1], -up[2]};

    // Which way the camera faces is invisible to gravity, so it is a stated convention:
    // the optical axis is "forward". That only decides how horizontal motion splits into
    // forward vs sideways; it never touches the vertical.
    double cz[3] = {0, 0, 1};
    const double dot = cz[0]*down[0] + cz[1]*down[1] + cz[2]*down[2];
    double fwd[3] = {cz[0] - dot*down[0], cz[1] - dot*down[1], cz[2] - dot*down[2]};
    const double fn = std::sqrt(fwd[0]*fwd[0] + fwd[1]*fwd[1] + fwd[2]*fwd[2]);
    if (!(fn > 1e-9)) return gf;
    for (int k = 0; k < 3; ++k) fwd[k] /= fn;

    // x = down x fwd, so x cross y = z as in the camera frame.
    double x[3] = {down[1]*fwd[2] - down[2]*fwd[1],
                   down[2]*fwd[0] - down[0]*fwd[2],
                   down[0]*fwd[1] - down[1]*fwd[0]};

    for (int k = 0; k < 3; ++k) { gf.R[0][k] = x[k]; gf.R[1][k] = down[k]; gf.R[2][k] = fwd[k]; }
    for (int k = 0; k < 3; ++k) { gf.up[k] = up[k]; gf.accel_mean[k] = g[k]; }
    gf.accel_magnitude = mag;
    // Angle between the camera's own down (+y) and true down.
    gf.tilt_deg = std::acos(std::max(-1.0, std::min(1.0, down[1]))) * 180.0 / M_PI;
    gf.samples  = (long)n;
    gf.valid    = true;
    return gf;
}

PipelineResult OfflinePipeline::run(const SessionPaths& paths, const ProgressFn& prog) const {
    PipelineResult R;
    R.session_id = paths.id.empty() ? paths.dir.filename().string() : paths.id;
    auto step = [&](const char* s, float f) { if (prog) prog(s, f); };

    // ---- which lift, from the session's own metadata -------------------------------
    {
        std::ifstream mf(paths.metadata());
        if (!mf) { R.message = "cannot read metadata.json"; return R; }
        try {
            nlohmann::json j; mf >> j;
            R.exercise     = j.value("exercise", "");
            R.session_date = j.value("date", "");
            R.subject_id   = j.value("subject_id", "");
            R.target_reps  = j.value("target_reps", 0);
            R.load_kg      = j.value("barbell_weight_kg", 0.0) + j.value("added_weight_kg", 0.0);
        } catch (const std::exception& e) {
            R.message = std::string("metadata.json: ") + e.what(); return R;
        }
    }
    if (R.exercise.empty()) { R.message = "metadata.json has no exercise"; return R; }
    R.down_first = is_down_first(R.exercise);

    // ---- 1. the camera's own tilt ---------------------------------------------------
    step("Reading the camera's tilt", 0.05f);
    R.frame = gravity_frame(paths.camera_imu());
    if (!R.frame.valid) { R.message = "not enough accelerometer data to fix the tilt"; return R; }

    // ---- 2. sealed raw track, on the camera's own frame numbering --------------------
    step("Reading the sealed marker track", 0.15f);
    {
        // WHY THE CAMERA'S FRAME COUNTER MATTERS. marker_positions.csv holds one row per
        // frame the camera DELIVERED. When the camera drops a frame it is simply not
        // there, so treating the row index as the time base closes the hole up: 11.1 ms
        // of the session disappears at every drop, the smoother is told the bar covered
        // two frames' distance in one frame's time, and the rest of the session is early.
        // Measured over this corpus: 731 dropped frames, and 28% of repetitions contain
        // at least one. video_frames.csv carries the camera's own hardware frame counter,
        // which says exactly where the holes are.
        std::vector<long long> fnum;
        {
            std::ifstream vf(paths.frames());
            if (vf) {
                std::string line;
                if (std::getline(vf, line)) {
                    auto vix = header_index(line);
                    if (vix.count("frame_number")) {
                        while (std::getline(vf, line)) {
                            if (line.empty()) continue;
                            auto v = split(line, ',');
                            if (v.size() <= vix["frame_number"]) continue;
                            fnum.push_back(std::atoll(v[vix["frame_number"]].c_str()));
                        }
                    }
                }
            }
        }

        std::ifstream f(paths.markers());
        if (!f) { R.message = "cannot read camera/marker_positions.csv"; return R; }
        std::string line;
        if (!std::getline(f, line)) { R.message = "marker_positions.csv is empty"; return R; }
        auto ix = header_index(line);
        for (const char* need : {"x_m", "y_m", "z_m", "detected"})
            if (!ix.count(need)) { R.message = std::string("marker_positions.csv has no ") + need; return R; }

        // read the delivered rows first, then place them on the true grid
        struct Row { bool detected; double p[3]; double raw_y; };
        std::vector<Row> rows;
        while (std::getline(f, line)) {
            if (line.empty()) continue;
            auto v = split(line, ',');
            if (v.size() <= ix["detected"]) continue;
            Row r{};
            r.detected = std::atoi(v[ix["detected"]].c_str()) != 0;
            r.raw_y    = std::nan("");
            if (r.detected) {
                const double p[3] = {to_d(v[ix["x_m"]]), to_d(v[ix["y_m"]]), to_d(v[ix["z_m"]])};
                if (std::isfinite(p[0]) && std::isfinite(p[1]) && std::isfinite(p[2])) {
                    for (int k = 0; k < 3; ++k)
                        r.p[k] = R.frame.R[k][0]*p[0] + R.frame.R[k][1]*p[1] + R.frame.R[k][2]*p[2];
                    r.raw_y = -to_d(v[ix["y_m"]]);   // the vertical as recorded: up is -y
                } else {
                    r.detected = false;   // detected=1 with a non-finite position is not a measurement
                }
            }
            rows.push_back(r);
        }
        if (rows.empty()) { R.message = "marker_positions.csv has no rows"; return R; }

        // The two files are written together, one row each per delivered frame. If they
        // disagree the counter cannot be trusted, so fall back to the row index and say so.
        const bool have_counter = (fnum.size() == rows.size()) && fnum.size() > 1
                                  && fnum.back() >= fnum.front();
        const long long span = have_counter ? (fnum.back() - fnum.front() + 1)
                                            : (long long)rows.size();
        if (!have_counter && !fnum.empty())
            R.message = "video_frames.csv does not line up with marker_positions.csv; "
                        "dropped frames could not be located";

        R.samples.assign((size_t)span, Sample3{});
        R.raw_pos.assign((size_t)span, std::nan(""));
        R.video_row.assign((size_t)span, -1);
        for (size_t i = 0; i < R.samples.size(); ++i) {
            R.samples[i].frame_idx = (int64_t)i;
            R.samples[i].t_s       = (double)i / kFps;
            R.samples[i].detected  = false;      // a hole until something is placed in it
        }
        for (size_t i = 0; i < rows.size(); ++i) {
            const long long at = have_counter ? (fnum[i] - fnum.front()) : (long long)i;
            if (at < 0 || at >= span) continue;
            auto& s = R.samples[(size_t)at];
            s.detected = rows[i].detected;
            for (int k = 0; k < 3; ++k) s.p[k] = rows[i].p[k];
            R.raw_pos[(size_t)at]   = rows[i].raw_y;
            R.video_row[(size_t)at] = (int)i;    // where this frame sits in ir_video.mp4
        }
        for (size_t i = 0; i < R.samples.size(); ++i) {
            if (R.video_row[i] < 0)             ++R.dropped_frames;   // never delivered
            else if (!R.samples[i].detected)    ++R.lost_frames;      // delivered, marker unseen
        }
    }
    if (R.samples.size() < 32) { R.message = "too few frames"; return R; }

    // ---- 3. the frame rate, from the camera's own trigger pulses ---------------------
    step("Reading the frame rate from the trigger", 0.30f);
    R.sync = build_sync_map(paths.dir, R.video_row, R.samples.size());
    {
        // The camera does not run at 90.000 Hz. Its own pulses, read inside the inertial
        // stream, put it at 89.8654 -- consistently, to within 35 ppm in all 84 sessions.
        // Every sample is re-dated on the measured period, so no rate is assumed.
        const double dt = R.sync.frame_period;
        for (size_t i = 0; i < R.samples.size(); ++i) R.samples[i].t_s = (double)i * dt;
    }

    // ---- 4. smooth the whole session, both ways ------------------------------------
    step("Smoothing the session forwards and backwards", 0.45f);
    {
        RtsSmoother::Config sc;
        sc.dt = R.sync.frame_period;
        RtsSmoother sm(sc);
        R.smoothed = sm.run(R.samples);
        sm.innovation_consistency(R.samples, R.nis);
    }
    R.track = OfflineAnnotator::track_from(R.smoothed);

    // ---- 5. the same causal annotator, re-run on the corrected track ----------------
    step("Re-running the live annotator on the corrected track", 0.72f);
    {
        rt::RtAnnotator::Config cfg;
        cfg.down_first  = R.down_first;
        cfg.rom_prior_m = rom_prior_for(R.exercise);
        rt::RtAnnotator ann(cfg);
        for (size_t i = 0; i < R.smoothed.size(); ++i) {
            rt::RtSample s;
            s.frame_idx = (int64_t)i;
            s.t_s       = (double)i * R.sync.frame_period;
            s.y_m       = R.smoothed[i].pos[1];   // the annotator negates it internally
            // On the smoothed track every frame carries an estimate, so every frame is
            // usable. `measured` is still carried through the annotation, so a rep
            // resting on reconstructed frames is still reported as such.
            s.detected   = true;
            s.confidence = 0.70;
            ann.push(s);
        }
        R.online = ann.reps();
    }

    // ---- 6. the post-session annotation ---------------------------------------------
    step("Finding the reps and their boundaries", 0.90f);
    {
        OfflineAnnotator off;
        R.annotation = off.run(R.track, R.online, R.down_first);
    }
    if (!R.annotation.lines.valid)
        R.message = "the live pass found too few middle reps to place the lines";

    step("Done", 1.0f);
    R.ok = true;
    return R;
}

namespace {

void write_rep_header(std::ofstream& o) {
    o << "rep_id,eccentric_start_frame,eccentric_end_frame,concentric_start_frame,"
         "concentric_end_frame,rom_m,peak_velocity,gap_frames,started_below,started_above,"
         "rejected\n";
}

void write_rep(std::ofstream& o, const OfflineRep& r) {
    o << r.rep_id << ',' << r.eccentric_start_frame << ',' << r.eccentric_end_frame << ','
      << r.concentric_start_frame << ',' << r.concentric_end_frame << ','
      << r.rom_m << ',' << r.peak_velocity << ',' << r.gap_frames << ','
      << (r.started_below ? 1 : 0) << ',' << (r.started_above ? 1 : 0) << ','
      << (r.rejected ? 1 : 0) << '\n';
}

} // namespace

bool OfflinePipeline::write(const PipelineResult& R, const SessionPaths& paths,
                            std::string& err) const {
    std::error_code ec;
    if (!fs::exists(paths.dir, ec)) { err = "no such session directory"; return false; }

    // the camera's tilt, and the fact that only its direction was used
    {
        nlohmann::json j;
        j["session_id"]  = R.session_id;
        j["source"]      = "<session>/imu/camera_imu.csv, accel rows";
        j["samples_used"] = R.frame.samples;
        j["accel_mean_camera_frame"] = {R.frame.accel_mean[0], R.frame.accel_mean[1], R.frame.accel_mean[2]};
        j["accel_magnitude"] = R.frame.accel_magnitude;
        j["accel_magnitude_note"] =
            "direction only is used; normalising cancels this part's scale error exactly";
        j["tilt_deg"] = R.frame.tilt_deg;
        j["yaw_note"] =
            "yaw is not observable from gravity; the optical axis is 'forward' by convention";
        j["rotation_rows_are_new_axes_in_camera_frame"] = {
            {R.frame.R[0][0], R.frame.R[0][1], R.frame.R[0][2]},
            {R.frame.R[1][0], R.frame.R[1][1], R.frame.R[1][2]},
            {R.frame.R[2][0], R.frame.R[2][1], R.frame.R[2][2]}};
        std::ofstream o(paths.rotation());
        if (!o) { err = "cannot write rotation.json"; return false; }
        o << j.dump(1) << '\n';
    }

    // the smoothed track
    {
        std::ofstream o(paths.smoothed());
        if (!o) { err = "cannot write smoothed.csv"; return false; }
        o << "frame_idx,t_s,measured,pos_x,pos_y,pos_z,vel_x,vel_y,vel_z,acc_x,acc_y,acc_z,"
             "jerk_x,jerk_y,jerk_z,pos_sd_x,pos_sd_y,pos_sd_z,vel_sd_x,vel_sd_y,vel_sd_z,"
             "acc_sd_x,acc_sd_y,acc_sd_z,jerk_sd_x,jerk_sd_y,jerk_sd_z\n";
        o.setf(std::ios::fixed); o.precision(9);
        for (const auto& s : R.smoothed) {
            o << s.frame_idx << ',' << s.t_s << ',' << (s.measured ? 1 : 0);
            for (int k = 0; k < 3; ++k) o << ',' << s.pos[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.vel[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.acc[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.jerk[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.pos_sd[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.vel_sd[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.acc_sd[k];
            for (int k = 0; k < 3; ++k) o << ',' << s.jerk_sd[k];
            o << '\n';
        }
    }

    // which inertial sample is which camera frame
    {
        std::string serr;
        if (!write_sync_map(R.sync, paths.dir / "sync_map.csv", serr)) { err = serr; return false; }
    }

    // the causal pass on this track
    {
        std::ofstream o(paths.online());
        if (!o) { err = "cannot write annotation_online.csv"; return false; }
        o << "rep_id,concentric_start_frame,concentric_end_frame,eccentric_start_frame,"
             "eccentric_end_frame,rom_m,peak_velocity,gap_frames,confirmed\n";
        for (const auto& r : R.online)
            o << r.rep_id << ',' << r.concentric_start_frame << ',' << r.concentric_end_frame
              << ',' << r.eccentric_start_frame << ',' << r.eccentric_end_frame << ','
              << r.rom_m << ',' << r.peak_velocity << ',' << r.gap_frames << ','
              << (r.confirmed ? 1 : 0) << '\n';
    }

    // the post-session annotation
    {
        nlohmann::json meta;
        meta["session_id"]  = R.session_id;
        meta["exercise"]    = R.exercise;
        meta["down_first"]  = R.down_first ? 1 : 0;
        meta["line_low_m"]  = R.annotation.lines.low;
        meta["line_mid_m"]  = R.annotation.lines.mid;
        meta["line_high_m"] = R.annotation.lines.high;
        meta["n_reps"]      = (int)R.annotation.reps.size();
        meta["lost_frames"]    = R.lost_frames;      // marker not seen
        meta["dropped_frames"] = R.dropped_frames;   // camera never delivered the frame
        meta["smoother_nis"]   = {R.nis[0], R.nis[1], R.nis[2]};
        meta["frame_period_s"] = R.sync.frame_period;
        meta["frame_rate_hz"]  = 1.0 / R.sync.frame_period;
        meta["produced_by"] = "app: OfflinePipeline";

        std::ofstream o(paths.offline());
        if (!o) { err = "cannot write annotation_offline.csv"; return false; }
        o << "# " << meta.dump() << '\n';
        write_rep_header(o);
        for (const auto& r : R.annotation.reps) write_rep(o, r);
    }
    return true;
}

bool OfflinePipeline::load_annotation(const SessionPaths& paths, Annotation& out) {
    std::ifstream f(paths.offline());
    if (!f) return false;
    std::string line;
    out.reps.clear();
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        if (line[0] == '#') {
            try {
                auto j = nlohmann::json::parse(line.substr(1));
                out.down_first  = j.value("down_first", 0) != 0;
                out.lines.low   = j.value("line_low_m", 0.0);
                out.lines.mid   = j.value("line_mid_m", 0.0);
                out.lines.high  = j.value("line_high_m", 0.0);
                out.lines.valid = true;
            } catch (...) {}
            continue;
        }
        if (line.rfind("rep_id", 0) == 0) continue;
        auto v = split(line, ',');
        if (v.size() < 11) continue;
        OfflineRep r;
        r.rep_id                 = std::atoi(v[0].c_str());
        r.eccentric_start_frame  = std::atoll(v[1].c_str());
        r.eccentric_end_frame    = std::atoll(v[2].c_str());
        r.concentric_start_frame = std::atoll(v[3].c_str());
        r.concentric_end_frame   = std::atoll(v[4].c_str());
        r.rom_m                  = to_d(v[5]);
        r.peak_velocity          = to_d(v[6]);
        r.gap_frames             = std::atoi(v[7].c_str());
        r.started_below          = std::atoi(v[8].c_str()) != 0;
        r.started_above          = std::atoi(v[9].c_str()) != 0;
        r.rejected               = std::atoi(v[10].c_str()) != 0;
        out.reps.push_back(r);
    }
    return !out.reps.empty();
}

uint64_t OfflinePipeline::fingerprint(const Annotation& an) {
    // FNV-1a over the boundaries. Cheap, dependency-free, and enough to notice that the
    // annotation changed under a review.
    uint64_t h = 1469598103934665603ull;
    auto mix = [&](int64_t v) {
        for (int i = 0; i < 8; ++i) { h ^= (uint64_t)((v >> (i * 8)) & 0xff); h *= 1099511628211ull; }
    };
    for (const auto& r : an.reps) {
        mix(r.rep_id);
        mix(r.eccentric_start_frame);  mix(r.eccentric_end_frame);
        mix(r.concentric_start_frame); mix(r.concentric_end_frame);
    }
    return h;
}

bool OfflinePipeline::write_review(const SessionPaths& paths, const Annotation& an,
                                   std::string& err) {
    // The reviewer's decisions live in their own file. annotation/offline.csv stays
    // exactly as the algorithm produced it, so what was refused is visible as a refusal
    // rather than as an absence.
    std::ofstream o(paths.reviewed());
    if (!o) { err = "cannot write annotation_reviewed.csv"; return false; }
    o << "# reviewer accept/reject over annotation_offline.csv; the algorithm's own "
         "output is not edited\n";
    o << "# annotation_fingerprint=" << fingerprint(an)
      << "   (the boundaries this judgement was made against)\n";
    o << "rep_id,accepted\n";
    for (const auto& r : an.reps) o << r.rep_id << ',' << (r.rejected ? 0 : 1) << '\n';
    return true;
}

bool OfflinePipeline::load_review(const SessionPaths& paths, Annotation& an, bool* stale) {
    std::ifstream f(paths.reviewed());
    if (stale) *stale = false;
    if (!f) return false;
    std::string line; bool any = false;
    while (std::getline(f, line)) {
        if (line.rfind("# annotation_fingerprint=", 0) == 0) {
            const uint64_t was = std::strtoull(line.c_str() + 25, nullptr, 10);
            if (stale && was != 0 && was != fingerprint(an)) *stale = true;
            continue;
        }
        if (line.empty() || line[0] == '#' || line.rfind("rep_id", 0) == 0) continue;
        auto v = split(line, ',');
        if (v.size() < 2) continue;
        const int id = std::atoi(v[0].c_str());
        const bool accepted = std::atoi(v[1].c_str()) != 0;
        for (auto& r : an.reps) if (r.rep_id == id) { r.rejected = !accepted; any = true; }
    }
    return any;
}

bool OfflinePipeline::export_ground_truth(const fs::path& datasets_root,
                                          const PipelineResult& R, std::string& err) {
    const fs::path out = datasets_root / "ground_truth.csv";

    // EVERY COLUMN A LATER STAGE COULD NEED. The point of this file is that nothing
    // downstream has to open a session directory to understand a rep: identity, when it
    // happened, what was on the bar, where the rep started and stopped in both frames and
    // seconds, what the bar did, and how well it was measured. A refused rep is not here
    // at all -- refusals live in the session's own annotation_reviewed.csv.
    static const char* kHeader =
        "session_id,session_date,subject_id,exercise,down_first,load_kg,target_reps,"
        "reviewed,rep_id,annotation_rep_id,"
        "rep_start_frame,turnaround_frame,rep_end_frame,"
        "concentric_start_frame,concentric_end_frame,"
        "eccentric_start_frame,eccentric_end_frame,"
        "rep_start_s,rep_end_s,duration_s,concentric_s,eccentric_s,"
        "height_start_m,height_turn_m,height_end_m,rom_m,return_error_m,"
        "con_mean_velocity_ms,con_peak_velocity_ms,con_time_to_peak_s,"
        "ecc_mean_velocity_ms,ecc_peak_velocity_ms,"
        "con_peak_accel_ms2,ecc_peak_accel_ms2,peak_jerk_ms3,"
        "gap_frames,measured_fraction,pos_sd_median_m,vel_sd_median_ms,"
        "started_below_band,started_above_band,"
        "line_low_m,line_mid_m,line_high_m,camera_tilt_deg,smoother_nis_vertical";

    // Read what is there, drop this session's rows, write it back.
    std::vector<std::string> keep;
    {
        std::ifstream f(out);
        std::string line;
        while (std::getline(f, line)) {
            if (line.empty() || line.rfind("session_id", 0) == 0) continue;
            if (line.rfind(R.session_id + ",", 0) == 0) continue;
            keep.push_back(line);
        }
    }
    std::ofstream o(out);
    if (!o) { err = "cannot write ground_truth.csv"; return false; }
    o << kHeader << '\n';
    for (const auto& l : keep) o << l << '\n';

    const auto&  T   = R.track;
    const size_t n   = T.size();
    // seconds come from the period measured for THIS session, not from 90.000
    const double fps = R.sync.frame_period;
    auto med = [](std::vector<double> v) {
        if (v.empty()) return 0.0;
        std::sort(v.begin(), v.end());
        return v[v.size() / 2];
    };

    o.setf(std::ios::fixed);
    int released = 0;
    for (const auto& r : R.annotation.reps) {
        if (r.rejected) continue;             // a refused rep is not ground truth
        ++released;
        const int64_t a = std::min(r.concentric_start_frame, r.eccentric_start_frame);
        const int64_t b = std::max(r.concentric_end_frame,   r.eccentric_end_frame);
        const int64_t t = R.down_first ? r.eccentric_end_frame : r.concentric_end_frame;
        if (a < 0 || b < 0 || (size_t)b >= n || b <= a) continue;

        // concentric and eccentric, each over its own frames only
        double con_sum = 0, con_pk = 0, con_pk_a = 0; int con_n = 0; int64_t con_pk_f = a;
        for (int64_t i = r.concentric_start_frame; i <= r.concentric_end_frame && i >= 0 && (size_t)i < n; ++i) {
            con_sum += T.vel[i]; ++con_n;
            if (std::fabs(T.vel[i]) > std::fabs(con_pk)) { con_pk = T.vel[i]; con_pk_f = i; }
            if (std::fabs(T.acc[i]) > std::fabs(con_pk_a)) con_pk_a = T.acc[i];
        }
        double ecc_sum = 0, ecc_pk = 0, ecc_pk_a = 0; int ecc_n = 0;
        for (int64_t i = r.eccentric_start_frame; i <= r.eccentric_end_frame && i >= 0 && (size_t)i < n; ++i) {
            ecc_sum += T.vel[i]; ++ecc_n;
            if (std::fabs(T.vel[i]) > std::fabs(ecc_pk)) ecc_pk = T.vel[i];
            if (std::fabs(T.acc[i]) > std::fabs(ecc_pk_a)) ecc_pk_a = T.acc[i];
        }
        double pk_jerk = 0; int measured = 0;
        std::vector<double> psd, vsd;
        for (int64_t i = a; i <= b; ++i) {
            const double j = -R.smoothed[i].jerk[1];
            if (std::fabs(j) > std::fabs(pk_jerk)) pk_jerk = j;
            if (T.measured[i]) ++measured;
            psd.push_back(R.smoothed[i].pos_sd[1]);
            vsd.push_back(R.smoothed[i].vel_sd[1]);
        }

        o << R.session_id << ',' << R.session_date << ',' << R.subject_id << ','
          << R.exercise << ',' << (R.down_first ? 1 : 0) << ',';
        o.precision(2); o << R.load_kg << ',';
        // released reps are numbered 1..N with no gaps; a refused rep leaves no hole
        o << R.target_reps << ',' << (R.reviewed ? 1 : 0) << ','
          << released << ',' << r.rep_id << ','
          << a << ',' << t << ',' << b << ','
          << r.concentric_start_frame << ',' << r.concentric_end_frame << ','
          << r.eccentric_start_frame  << ',' << r.eccentric_end_frame  << ',';
        o.precision(4);
        o << a * fps << ',' << b * fps << ',' << (b - a) * fps << ','
          << std::max<int64_t>(0, r.concentric_end_frame - r.concentric_start_frame) * fps << ','
          << std::max<int64_t>(0, r.eccentric_end_frame  - r.eccentric_start_frame)  * fps << ',';
        o.precision(6);
        o << T.pos[a] << ',' << T.pos[t] << ',' << T.pos[b] << ','
          << r.rom_m << ',' << (T.pos[b] - T.pos[a]) << ',';
        o.precision(4);
        o << (con_n ? con_sum / con_n : 0.0) << ',' << con_pk << ','
          << (con_pk_f - r.concentric_start_frame) * fps << ','
          << (ecc_n ? ecc_sum / ecc_n : 0.0) << ',' << ecc_pk << ','
          << con_pk_a << ',' << ecc_pk_a << ',' << pk_jerk << ',';
        o << r.gap_frames << ',';
        o.precision(4);
        o << (double)measured / (double)(b - a + 1) << ',';
        o.precision(6);
        o << med(psd) << ',' << med(vsd) << ',';
        o << (r.started_below ? 1 : 0) << ',' << (r.started_above ? 1 : 0) << ','
          << R.annotation.lines.low << ',' << R.annotation.lines.mid << ','
          << R.annotation.lines.high << ',';
        o.precision(3);
        o << R.frame.tilt_deg << ',' << R.nis[1] << '\n';
        o.precision(6);
    }
    return true;
}

} // namespace vbt::offline

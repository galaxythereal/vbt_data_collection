/**
 * @file SessionData.cpp
 * @brief Loaders for the annotation studio's read model.
 *
 * Strategy: streaming CSV parsing with std::getline; no allocations per
 * row beyond the column splits. Sessions are typically ~50k IMU rows and
 * a few thousand camera rows; this is fast enough that we do it
 * synchronously without a worker thread.
 */
#include "annotation/SessionData.h"
#include "utils/Uuid.h"
#include <spdlog/spdlog.h>
#include <fstream>
#include <sstream>
#include <algorithm>
#include <cmath>
#include <numeric>

namespace fs = std::filesystem;

namespace vbt {

namespace {

// Tokenize a CSV row; supports quoted fields (rare in our schema).
std::vector<std::string> split_csv_row(const std::string& line) {
    std::vector<std::string> out;
    out.reserve(20);
    std::string cur;
    bool in_quotes = false;
    for (char c : line) {
        if (in_quotes) {
            if (c == '"') in_quotes = false;
            else cur += c;
        } else if (c == '"') {
            in_quotes = true;
        } else if (c == ',') {
            out.emplace_back(std::move(cur));
            cur.clear();
        } else {
            cur += c;
        }
    }
    out.emplace_back(std::move(cur));
    return out;
}

// Resolve column indices once from header so we don't hard-code positions
// (CSV columns drifted across schema versions).
struct ColumnIndex {
    std::unordered_map<std::string, int> idx;
    int operator[](const std::string& k) const {
        auto it = idx.find(k);
        return it == idx.end() ? -1 : it->second;
    }
    int require(const std::string& k, std::vector<std::string>& errs,
                const std::string& file) const {
        int i = (*this)[k];
        if (i < 0) errs.push_back(file + ": missing column '" + k + "'");
        return i;
    }
    static ColumnIndex from_header(const std::string& header) {
        ColumnIndex c;
        auto cols = split_csv_row(header);
        for (size_t i = 0; i < cols.size(); ++i) c.idx[cols[i]] = (int)i;
        return c;
    }
};

float to_float(const std::string& s, float fallback = 0.0f) {
    if (s.empty()) return fallback;
    try { return std::stof(s); } catch (...) { return fallback; }
}
double to_double(const std::string& s, double fallback = 0.0) {
    if (s.empty()) return fallback;
    try { return std::stod(s); } catch (...) { return fallback; }
}
int to_int(const std::string& s, int fallback = 0) {
    if (s.empty()) return fallback;
    try { return std::stoi(s); } catch (...) { return fallback; }
}

} // namespace

int VideoIndex::nearest_to(double t_s) const {
    if (unified_t_s.empty()) return -1;
    auto it = std::lower_bound(unified_t_s.begin(), unified_t_s.end(), t_s);
    if (it == unified_t_s.begin()) return 0;
    if (it == unified_t_s.end()) return (int)unified_t_s.size() - 1;
    int hi = (int)(it - unified_t_s.begin());
    int lo = hi - 1;
    return (t_s - unified_t_s[lo]) < (unified_t_s[hi] - t_s) ? lo : hi;
}

void SessionData::clear() {
    session_dir_.clear();
    info_ = {};
    imu_   = {};
    marker_ = {};
    video_idx_ = {};
    reps_.clear();
    manifest_.clear();
    events_.clear();
    loaded_ = false;
    reps_dirty_ = false;
    meta_dirty_ = false;
}

bool SessionData::load(const fs::path& session_dir, SessionLoadDiag& diag) {
    clear();
    session_dir_ = session_dir;
    if (!fs::exists(session_dir)) {
        diag.errors.push_back("Session directory does not exist: " + session_dir.string());
        return false;
    }

    bool any_ok = true;
    any_ok &= load_meta_json_(session_dir / "metadata.json", diag);
    any_ok &= load_imu_csv_(session_dir / "imu" / "raw_imu.csv", diag);
    load_marker_csv_(session_dir / "camera" / "marker_positions.csv", diag); // optional
    load_video_index_csv_(session_dir / "camera" / "video_frames.csv", diag);
    load_reps_json_(session_dir / "annotations" / "rep_segments.json", diag);
    load_manifest_(session_dir / "manifest.json", diag);
    load_events_(session_dir / "events.jsonl", diag);

    fixup_legacy_imu_unified_time_();
    fixup_subject_uuid_();
    fixup_legacy_sets_();

    loaded_ = any_ok && diag.ok();
    if (loaded_) {
        spdlog::info("AnnotationStudio: loaded session '{}': {} IMU rows, {} marker rows, "
                     "{} video frames, {} reps, {} sets",
                     session_dir.string(),
                     imu_.size(), marker_.size(), video_idx_.size(),
                     reps_.size(), info_.sets.size());
    }
    return loaded_;
}

void SessionData::fixup_subject_uuid_() {
    // Mint a stable UUID once per subject. We can't reuse one across
    // sessions automatically (we'd need a subjects.json registry), so the
    // policy is: if blank, mint now and mark dirty so the user gets
    // prompted to confirm/save. Operators who run the same subject again
    // can paste the existing UUID into the metadata panel.
    if (info_.subject_uuid.empty()) {
        info_.subject_uuid = make_uuid_v4();
        meta_dirty_ = true;
    }
}

void SessionData::fixup_legacy_sets_() {
    // Pre-v4 sessions had no `sets` vector; promote the top-level
    // weight/RPE/target_reps fields to a single SetInfo so the studio's
    // multi-set UI is uniform across schema versions.
    if (info_.sets.empty()) {
        SetInfo s;
        s.set_id            = std::max(1, info_.set_number);
        s.barbell_weight_kg = info_.barbell_weight_kg;
        s.added_weight_kg   = info_.added_weight_kg;
        s.total_weight_kg   = info_.total_weight_kg;
        s.percent_1rm       = info_.percent_1rm;
        s.target_reps       = info_.target_reps;
        s.rpe               = info_.rpe;
        s.notes             = info_.notes;
        s.t_start_unified_s = imu_.size() ? imu_.unified_t_s.front() : 0.0;
        s.t_end_unified_s   = imu_.size() ? imu_.unified_t_s.back()  : 0.0;
        s.completed_reps    = (int)reps_.size();
        info_.sets.push_back(s);
        meta_dirty_ = true;
    }
    // Backfill any unset rep.set_id (legacy reps stored set_id=0 or the
    // pre-v4 file simply didn't have the column) → first set.
    if (!info_.sets.empty()) {
        const int first_set_id = info_.sets.front().set_id;
        for (auto& r : reps_) if (r.set_id <= 0) r.set_id = first_set_id;
    }
}

bool SessionData::load_imu_csv_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) {
        diag.errors.push_back("Cannot open IMU CSV: " + p.string());
        return false;
    }
    std::string line;
    if (!std::getline(f, line)) {
        diag.errors.push_back("Empty IMU CSV: " + p.string());
        return false;
    }
    auto C = ColumnIndex::from_header(line);
    int i_esp   = C.require("esp_timestamp_us",  diag.errors, "raw_imu.csv");
    int i_host  = C.require("host_timestamp_s",  diag.errors, "raw_imu.csv");
    int i_uni   = C["unified_time_s"];   // optional on legacy sessions
    int i_ax    = C.require("accel_x_g", diag.errors, "raw_imu.csv");
    int i_ay    = C.require("accel_y_g", diag.errors, "raw_imu.csv");
    int i_az    = C.require("accel_z_g", diag.errors, "raw_imu.csv");
    int i_gx    = C.require("gyro_x_dps", diag.errors, "raw_imu.csv");
    int i_gy    = C.require("gyro_y_dps", diag.errors, "raw_imu.csv");
    int i_gz    = C.require("gyro_z_dps", diag.errors, "raw_imu.csv");
    int i_temp  = C["temperature_c"];
    int i_fsync = C["fsync_flag"];
    if (!diag.ok()) return false;

    imu_ = {};
    imu_.esp_ts_us.reserve(50000);
    imu_.host_ts_s.reserve(50000);
    imu_.unified_t_s.reserve(50000);
    imu_.ax_g.reserve(50000);
    imu_.ay_g.reserve(50000);
    imu_.az_g.reserve(50000);
    imu_.gx_dps.reserve(50000);
    imu_.gy_dps.reserve(50000);
    imu_.gz_dps.reserve(50000);
    imu_.temp_c.reserve(50000);
    imu_.fsync_flag.reserve(50000);

    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split_csv_row(line);
        if ((int)v.size() <= i_az) continue;
        imu_.esp_ts_us.push_back(to_double(v[i_esp]));
        imu_.host_ts_s.push_back(to_double(v[i_host]));
        imu_.unified_t_s.push_back(i_uni >= 0 ? to_double(v[i_uni]) : 0.0);
        imu_.ax_g.push_back(to_float(v[i_ax]));
        imu_.ay_g.push_back(to_float(v[i_ay]));
        imu_.az_g.push_back(to_float(v[i_az]));
        imu_.gx_dps.push_back(to_float(v[i_gx]));
        imu_.gy_dps.push_back(to_float(v[i_gy]));
        imu_.gz_dps.push_back(to_float(v[i_gz]));
        imu_.temp_c.push_back(i_temp >= 0 ? to_float(v[i_temp]) : 0.0f);
        imu_.fsync_flag.push_back(i_fsync >= 0 ? (uint8_t)to_int(v[i_fsync]) : 0);
    }
    return true;
}

bool SessionData::load_marker_csv_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) {
        diag.warnings.push_back("No marker_positions.csv (no camera data?)");
        return false;
    }
    std::string line;
    if (!std::getline(f, line)) return false;
    auto C = ColumnIndex::from_header(line);
    int i_t = C["timestamp_s"];           // legacy: this is the unified time
    if (i_t < 0) i_t = C["unified_time_s"]; // post-rewrite name
    int i_x = C["x_m"], i_y = C["y_m"], i_z = C["z_m"];
    int i_pu = C["pixel_u"], i_pv = C["pixel_v"];
    int i_conf = C["confidence"];
    int i_snr  = C["snr"];
    int i_circ = C["circularity"];
    int i_src  = C["depth_source"];
    int i_det  = C["detected"];
    if (i_t < 0 || i_x < 0 || i_y < 0) {
        diag.warnings.push_back("marker_positions.csv missing essential columns");
        return false;
    }

    marker_ = {};
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split_csv_row(line);
        if ((int)v.size() <= i_y) continue;
        marker_.unified_t_s.push_back(to_double(v[i_t]));
        marker_.x_m.push_back(to_float(v[i_x]));
        marker_.y_m.push_back(to_float(v[i_y]));
        marker_.z_m.push_back(i_z >= 0 ? to_float(v[i_z]) : 0.0f);
        marker_.pixel_u.push_back(i_pu >= 0 ? to_int(v[i_pu]) : 0);
        marker_.pixel_v.push_back(i_pv >= 0 ? to_int(v[i_pv]) : 0);
        marker_.confidence.push_back(i_conf >= 0 ? to_float(v[i_conf]) : 0.0f);
        marker_.snr.push_back(i_snr >= 0 ? to_float(v[i_snr]) : 0.0f);
        marker_.circularity.push_back(i_circ >= 0 ? to_float(v[i_circ]) : 0.0f);
        marker_.depth_source.push_back(i_src >= 0 ? v[i_src] : "");
        marker_.detected.push_back(i_det >= 0 ? (uint8_t)to_int(v[i_det]) : 0);
    }
    marker_.clean_dirty = true;
    return true;
}

bool SessionData::load_video_index_csv_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) {
        diag.warnings.push_back("No video_frames.csv (no IR video?)");
        return false;
    }
    std::string line;
    if (!std::getline(f, line)) return false;
    auto C = ColumnIndex::from_header(line);
    int i_idx = C["frame_idx"];
    int i_uni = C["unified_time_s"];
    int i_hw  = C["hw_timestamp_s"];
    int i_num = C["frame_number"];

    video_idx_ = {};
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split_csv_row(line);
        if ((int)v.size() <= std::max({i_idx, i_uni, i_num})) continue;
        video_idx_.frame_idx.push_back(i_idx >= 0 ? to_int(v[i_idx]) : (int)video_idx_.size());
        // Prefer unified_time_s; fall back to hw_timestamp_s for legacy.
        double t = i_uni >= 0 ? to_double(v[i_uni]) : 0.0;
        if (t < 1.0 && i_hw >= 0) t = to_double(v[i_hw]);
        video_idx_.unified_t_s.push_back(t);
        video_idx_.frame_number.push_back(i_num >= 0 ? to_int(v[i_num]) : 0);
    }
    return true;
}

bool SessionData::load_reps_json_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) {
        diag.warnings.push_back("No rep_segments.json — session has no rep annotations");
        return false;
    }
    nlohmann::json j;
    try { f >> j; }
    catch (const std::exception& e) {
        diag.errors.push_back(std::string("rep_segments.json parse error: ") + e.what());
        return false;
    }
    reps_.clear();
    auto& arr = j.is_array() ? j : (j.contains("reps") ? j["reps"] : j);
    if (!arr.is_array()) return false;
    for (const auto& el : arr) {
        try { reps_.push_back(RepAnnotation::from_json(el)); }
        catch (...) { /* skip malformed rep */ }
    }
    return true;
}

bool SessionData::load_meta_json_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) {
        diag.errors.push_back("Cannot open metadata.json: " + p.string());
        return false;
    }
    nlohmann::json j;
    try { f >> j; }
    catch (const std::exception& e) {
        diag.errors.push_back(std::string("metadata.json parse error: ") + e.what());
        return false;
    }
    try { info_ = j.get<SessionInfo>(); }
    catch (const std::exception& e) {
        diag.warnings.push_back(std::string("metadata.json deserialization warning: ") + e.what());
    }
    return true;
}

bool SessionData::load_manifest_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) return false;
    try { f >> manifest_; }
    catch (const std::exception& e) {
        diag.warnings.push_back(std::string("manifest.json parse error: ") + e.what());
    }
    return true;
}

bool SessionData::load_events_(const fs::path& p, SessionLoadDiag& diag) {
    std::ifstream f(p);
    if (!f.is_open()) return true;
    std::string line;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        try { events_.push_back(nlohmann::json::parse(line)); }
        catch (...) { /* skip malformed */ }
    }
    return true;
}

void SessionData::fixup_legacy_imu_unified_time_() {
    if (imu_.size() == 0) return;
    // Detect: unified_time_s all zero or all sub-1e9 → legacy session.
    double max_uni = 0;
    for (double v : imu_.unified_t_s) max_uni = std::max(max_uni, v);
    if (max_uni > 1e9) return;  // already wall-clock

    // Compute mono→wall offset from video_frames.csv. The hw_timestamp_s is
    // RealSense GLOBAL_TIME (Unix epoch) and host_timestamp_s is the
    // process's steady_clock. We take the median of the difference. The
    // VideoIndex doesn't carry host_timestamp_s; fall back to reparsing
    // video_frames.csv directly.
    fs::path vf = session_dir_ / "camera" / "video_frames.csv";
    std::ifstream f(vf);
    if (!f.is_open()) return;
    std::string line;
    if (!std::getline(f, line)) return;
    auto C = ColumnIndex::from_header(line);
    int i_host = C["host_timestamp_s"];
    int i_hw   = C["hw_timestamp_s"];
    if (i_host < 0 || i_hw < 0) return;
    std::vector<double> deltas;
    deltas.reserve(5000);
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split_csv_row(line);
        if ((int)v.size() <= std::max(i_host, i_hw)) continue;
        double host = to_double(v[i_host]);
        double hw   = to_double(v[i_hw]);
        if (hw > 1e9) deltas.push_back(hw - host);
    }
    if (deltas.empty()) return;
    std::sort(deltas.begin(), deltas.end());
    double mono_to_wall = deltas[deltas.size() / 2];

    for (size_t k = 0; k < imu_.unified_t_s.size(); ++k) {
        imu_.unified_t_s[k] = imu_.host_ts_s[k] + mono_to_wall;
    }
    spdlog::warn("AnnotationStudio: IMU unified_time_s missing in legacy session; "
                 "back-filled using mono→wall offset = {:.6f} s", mono_to_wall);
}

// ── Cleaning pipeline ──────────────────────────────────────────────
namespace {

// In-place Hampel rolling-median outlier replacement.
void hampel(std::vector<float>& x, int win, float n_sigmas) {
    if ((int)x.size() < win) return;
    int half = win / 2;
    const float k = 1.4826f;
    std::vector<float> out = x;
    std::vector<float> buf;
    buf.reserve(win);
    for (int i = 0; i < (int)x.size(); ++i) {
        int lo = std::max(0, i - half);
        int hi = std::min((int)x.size(), i + half + 1);
        buf.assign(x.begin() + lo, x.begin() + hi);
        std::nth_element(buf.begin(), buf.begin() + buf.size() / 2, buf.end());
        float med = buf[buf.size() / 2];
        std::vector<float> dev = buf;
        for (auto& d : dev) d = std::fabs(d - med);
        std::nth_element(dev.begin(), dev.begin() + dev.size() / 2, dev.end());
        float mad = k * dev[dev.size() / 2];
        if (mad > 0 && std::fabs(x[i] - med) > n_sigmas * mad) out[i] = med;
    }
    x = std::move(out);
}

// 2nd-order Butterworth low-pass via biquad transposed-direct-form II,
// with manual filtfilt (forward + reversed-forward) for zero phase.
struct Biquad {
    float b0, b1, b2, a1, a2;
};
Biquad butter2_lp(float fc_hz, float fs_hz) {
    // Standard bilinear transform of H(s) = 1/(s^2 + sqrt(2)s + 1).
    float w = std::tan(static_cast<float>(M_PI) * fc_hz / fs_hz);
    float n = 1.0f + std::sqrt(2.0f) * w + w * w;
    Biquad q;
    q.b0 = w * w / n;
    q.b1 = 2.0f * q.b0;
    q.b2 = q.b0;
    q.a1 = (2.0f * (w * w - 1.0f)) / n;
    q.a2 = (1.0f - std::sqrt(2.0f) * w + w * w) / n;
    return q;
}
void apply_biquad(const Biquad& q, const std::vector<float>& in, std::vector<float>& out) {
    out.resize(in.size());
    float x1 = 0, x2 = 0, y1 = 0, y2 = 0;
    for (size_t i = 0; i < in.size(); ++i) {
        float x0 = in[i];
        float y0 = q.b0 * x0 + q.b1 * x1 + q.b2 * x2 - q.a1 * y1 - q.a2 * y2;
        out[i] = y0;
        x2 = x1; x1 = x0;
        y2 = y1; y1 = y0;
    }
}
void filtfilt(const Biquad& q, std::vector<float>& v) {
    std::vector<float> tmp;
    apply_biquad(q, v, tmp);
    std::reverse(tmp.begin(), tmp.end());
    apply_biquad(q, tmp, v);
    std::reverse(v.begin(), v.end());
}

} // namespace

void SessionData::recompute_clean_signal(const MarkerCleanConfig& cfg) {
    if (marker_.size() == 0) return;
    clean_cfg_ = cfg;
    const size_t N = marker_.size();

    std::vector<float> pos_up(N);
    std::vector<float> t(N);
    std::vector<uint8_t> keep(N, 0);
    for (size_t i = 0; i < N; ++i) {
        pos_up[i] = -marker_.y_m[i];   // world up = -y_camera
        t[i] = (float)marker_.unified_t_s[i];
        keep[i] = marker_.detected[i]
                  && marker_.confidence[i] >= cfg.conf_min
                  && marker_.snr[i]        >= cfg.snr_min
                  && marker_.circularity[i] >= cfg.circ_min ? 1 : 0;
    }

    // Linearly interpolate over masked-out samples so the filter doesn't see
    // discontinuities.
    int last_good = -1;
    for (int i = 0; i < (int)N; ++i) {
        if (keep[i]) last_good = i;
        else if (last_good >= 0) pos_up[i] = pos_up[last_good];
    }
    int first_good = -1;
    for (int i = 0; i < (int)N; ++i) if (keep[i]) { first_good = i; break; }
    if (first_good > 0) {
        for (int i = 0; i < first_good; ++i) pos_up[i] = pos_up[first_good];
    }

    hampel(pos_up, cfg.hampel_window, cfg.hampel_sigmas);
    if (cfg.lp_cutoff_hz > 0 && cfg.lp_cutoff_hz < cfg.fps / 2) {
        Biquad q = butter2_lp(cfg.lp_cutoff_hz, cfg.fps);
        filtfilt(q, pos_up);
    }

    // Numerical derivative; cap velocities outside the exercise envelope.
    std::vector<float> vz(N, 0.0f);
    for (size_t i = 1; i + 1 < N; ++i) {
        double dt = marker_.unified_t_s[i + 1] - marker_.unified_t_s[i - 1];
        if (dt > 1e-6) vz[i] = (pos_up[i + 1] - pos_up[i - 1]) / (float)dt;
    }
    if (N >= 2) {
        vz[0] = (pos_up[1] - pos_up[0])
              / (float)std::max(marker_.unified_t_s[1] - marker_.unified_t_s[0], 1e-6);
        vz[N - 1] = (pos_up[N - 1] - pos_up[N - 2])
              / (float)std::max(marker_.unified_t_s[N - 1] - marker_.unified_t_s[N - 2], 1e-6);
    }
    if (cfg.v_max_mps > 0) {
        for (size_t i = 0; i < N; ++i) {
            if (std::fabs(vz[i]) > cfg.v_max_mps) {
                // Replace with neighbor median to avoid creating new spikes.
                int lo = std::max((int)i - 3, 0);
                int hi = std::min((int)i + 4, (int)N);
                std::vector<float> w(vz.begin() + lo, vz.begin() + hi);
                std::nth_element(w.begin(), w.begin() + w.size() / 2, w.end());
                vz[i] = w[w.size() / 2];
            }
        }
    }

    marker_.pos_up_clean_m = std::move(pos_up);
    marker_.vz_clean_mps   = std::move(vz);

    // ── Detection: zero-crossings and local extrema ──────────────────
    // Anything below this magnitude isn't a real rep — kills jitter.
    constexpr float vz_min_extremum = 0.15f;
    marker_.zero_crossings.clear();
    marker_.peak_vel_pos_idx.clear();
    marker_.peak_vel_neg_idx.clear();
    marker_.pos_max_idx.clear();
    marker_.pos_min_idx.clear();
    const auto& vc = marker_.vz_clean_mps;
    const auto& pc = marker_.pos_up_clean_m;
    for (size_t i = 1; i < vc.size(); ++i) {
        if ((vc[i - 1] < 0 && vc[i] >= 0) || (vc[i - 1] > 0 && vc[i] <= 0)) {
            // Zero crossing — pick the index whose vz is smallest in mag.
            int idx = (std::fabs(vc[i - 1]) < std::fabs(vc[i])) ? (int)i - 1 : (int)i;
            marker_.zero_crossings.push_back(idx);
        }
    }
    // Peak detection between consecutive zero-crossings.
    int prev_zc = 0;
    for (size_t k = 0; k < marker_.zero_crossings.size(); ++k) {
        int zc = marker_.zero_crossings[k];
        int lo = prev_zc, hi = zc;
        if (hi > lo + 5) {
            int peak_pos = lo, peak_neg = lo;
            for (int j = lo; j < hi; ++j) {
                if (vc[j] > vc[peak_pos]) peak_pos = j;
                if (vc[j] < vc[peak_neg]) peak_neg = j;
                if (pc[j] > pc[lo]) peak_pos = peak_pos;  // (placeholder)
            }
            if (vc[peak_pos] >  vz_min_extremum) marker_.peak_vel_pos_idx.push_back(peak_pos);
            if (vc[peak_neg] < -vz_min_extremum) marker_.peak_vel_neg_idx.push_back(peak_neg);
        }
        prev_zc = zc;
    }
    // Position extrema between zero-crossings: at zero-crossing of vz, by
    // definition position is at a turning point (top or bottom).
    for (int zc : marker_.zero_crossings) {
        if (zc <= 0 || zc + 1 >= (int)pc.size()) continue;
        bool falling_then_rising = vc[zc - 1] < 0 && (zc + 1 < (int)vc.size() ? vc[zc + 1] > 0 : false);
        bool rising_then_falling = vc[zc - 1] > 0 && (zc + 1 < (int)vc.size() ? vc[zc + 1] < 0 : false);
        if (rising_then_falling) marker_.pos_max_idx.push_back(zc);
        if (falling_then_rising) marker_.pos_min_idx.push_back(zc);
    }
    marker_.clean_dirty = false;
}

// ── Per-rep metric recomputation ───────────────────────────────────
void SessionData::recompute_all_rep_metrics() {
    for (size_t i = 0; i < reps_.size(); ++i) recompute_rep_metrics((int)i);
}

void SessionData::recompute_rep_metrics(int rep_index) {
    if (rep_index < 0 || rep_index >= (int)reps_.size()) return;
    if (marker_.clean_dirty) recompute_clean_signal(clean_cfg_);
    if (marker_.size() == 0) return;
    auto& r = reps_[rep_index];
    auto idx_at = [&](double t) -> int {
        auto it = std::lower_bound(marker_.unified_t_s.begin(),
                                    marker_.unified_t_s.end(), t);
        return (int)(it - marker_.unified_t_s.begin());
    };
    int i0 = idx_at(r.concentric.t_start_s);
    int i1 = idx_at(r.concentric.t_end_s);
    int j0 = idx_at(r.eccentric.t_start_s);
    int j1 = idx_at(r.eccentric.t_end_s);
    if (i1 <= i0 || j1 <= j0) return;

    float peak = -1e9f;
    double sum = 0; int n = 0;
    for (int i = i0; i < i1 && i < (int)marker_.size(); ++i) {
        float v = marker_.vz_clean_mps[i];
        if (v > peak) peak = v;
        if (v > 0.05f) { sum += v; ++n; }
    }
    r.concentric.peak_velocity_mps = peak;
    r.peak_concentric_velocity     = peak;
    r.mean_concentric_velocity     = n ? (float)(sum / n) : 0.0f;

    int k0 = std::min(i0, j0);
    int k1 = std::max(i1, j1);
    float pmin = 1e9f, pmax = -1e9f;
    for (int k = k0; k < k1 && k < (int)marker_.size(); ++k) {
        float p = marker_.pos_up_clean_m[k];
        if (p < pmin) pmin = p;
        if (p > pmax) pmax = p;
    }
    r.rom_m = (pmax > pmin) ? pmax - pmin : 0.0f;
}

} // namespace vbt

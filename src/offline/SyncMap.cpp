#include "offline/SyncMap.h"

#include <algorithm>
#include <cmath>
#include <cstdlib>
#include <fstream>
#include <map>
#include <sstream>

namespace fs = std::filesystem;

namespace vbt::offline {
namespace {

constexpr double kNominal = 1.0 / 90.0;

std::vector<std::string> split(const std::string& s, char d) {
    std::vector<std::string> out; std::string cur;
    std::istringstream is(s);
    while (std::getline(is, cur, d)) out.push_back(cur);
    return out;
}

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

/// A trigger pulse: when it happened in the bar's clock, and which inertial sample it
/// landed on.
struct Pulse { double t; int64_t sample; };

std::vector<Pulse> read_pulses(const fs::path& raw_imu) {
    std::vector<Pulse> out;
    std::ifstream f(raw_imu);
    if (!f) return out;
    std::string line;
    if (!std::getline(f, line)) return out;
    auto ix = header_index(line);
    if (!ix.count("fsync_flag") || !ix.count("esp_timestamp_us")) return out;
    int64_t row = 0;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split(line, ',');
        if (v.size() <= ix["fsync_flag"]) { ++row; continue; }
        if (v[ix["fsync_flag"]] != "0" && !v[ix["fsync_flag"]].empty())
            out.push_back({std::atoll(v[ix["esp_timestamp_us"]].c_str()) * 1e-6, row});
        ++row;
    }
    return out;
}

/// A pulse tagged twice inside half a frame period is one pulse.
void merge_duplicates(std::vector<Pulse>& p) {
    std::vector<Pulse> out;
    for (const auto& x : p)
        if (out.empty() || (x.t - out.back().t) > 0.5 * kNominal) out.push_back(x);
    p.swap(out);
}

} // namespace

SyncMap build_sync_map(const fs::path& session_dir,
                       const std::vector<int>& video_row,
                       size_t n_frames) {
    SyncMap m;
    m.frames.resize(n_frames);
    for (size_t i = 0; i < n_frames; ++i) {
        m.frames[i].frame     = (int64_t)i;
        m.frames[i].video_row = i < video_row.size() ? video_row[i] : -1;
    }

    auto pulses = read_pulses(session_dir / "imu" / "raw_imu.csv");
    merge_duplicates(pulses);
    if (pulses.size() < 100) {
        m.note = "too few trigger pulses in the inertial stream; nominal 90 Hz assumed";
        m.frame_period = kNominal;
        for (size_t i = 0; i < n_frames; ++i) m.frames[i].t_bar_s = (double)i * kNominal;
        return m;
    }
    m.pulses = (long)pulses.size();

    // THE FRAME PERIOD, from this session's own pulses. A gap counts as the number of
    // frame periods it spans, so a pulse the inertial stream missed shortens nothing.
    long long periods = 0;
    for (size_t i = 1; i < pulses.size(); ++i) {
        const long long k = std::max(1LL, (long long)std::llround((pulses[i].t - pulses[i-1].t) / kNominal));
        periods += k;
    }
    m.missed = (long)(periods - (long long)(pulses.size() - 1));
    m.frame_period = (pulses.back().t - pulses.front().t) / (double)periods;
    m.t0_bar_s     = pulses.front().t;

    // A pulse's position in the frame sequence, counted the same way.
    std::vector<long long> at(pulses.size(), 0);
    for (size_t i = 1; i < pulses.size(); ++i)
        at[i] = at[i-1] + std::max(1LL, (long long)std::llround((pulses[i].t - pulses[i-1].t) / kNominal));

    // Lay the pulses onto the track. The track begins at the first delivered frame, which
    // is the first pulse: the camera cannot deliver a frame it never exposed.
    for (size_t i = 0; i < pulses.size(); ++i) {
        const long long f = at[i];
        if (f < 0 || (size_t)f >= n_frames) continue;
        m.frames[(size_t)f].t_bar_s    = pulses[i].t;
        m.frames[(size_t)f].imu_sample = pulses[i].sample;
        m.frames[(size_t)f].tagged     = true;
    }
    // Frames whose pulse the inertial stream missed sit between two it did record, so
    // their time follows from the measured period rather than from a guess.
    for (size_t i = 0; i < n_frames; ++i) {
        if (m.frames[i].tagged) continue;
        m.frames[i].t_bar_s = m.t0_bar_s + (double)i * m.frame_period;
        // nearest recorded sample, so a consumer still has somewhere to start
        long long best = -1; double bd = 1e30;
        for (const auto& p : pulses) {
            const double d = std::fabs(p.t - m.frames[i].t_bar_s);
            if (d < bd) { bd = d; best = p.sample; }
            else if (p.t > m.frames[i].t_bar_s) break;
        }
        m.frames[i].imu_sample = best;
    }
    m.valid = true;
    return m;
}

bool write_sync_map(const SyncMap& m, const fs::path& out_csv, std::string& err) {
    std::ofstream o(out_csv);
    if (!o) { err = "cannot write " + out_csv.string(); return false; }
    o << "# which inertial sample is which camera frame, from the hardware trigger\n";
    o << "# frame_period_s=" << std::fixed;
    o.precision(9);
    o << m.frame_period << "  (" << (1.0 / m.frame_period) << " Hz, measured from this "
         "session's own trigger pulses -- the camera does not run at 90.000)\n";
    o << "# trigger_pulses=" << m.pulses << "  pulses_the_imu_missed=" << m.missed << "\n";
    o << "# t_bar_s is the exposure time in the BAR's clock, the same clock as "
         "esp_timestamp_us in imu/raw_imu.csv\n";
    o << "# imu_sample is a row index into imu/raw_imu.csv; tagged=0 means the inertial "
         "stream missed that pulse and the time is interpolated\n";
    if (!m.note.empty()) o << "# note: " << m.note << "\n";
    o << "frame,video_row,t_bar_s,imu_sample,tagged\n";
    for (const auto& f : m.frames) {
        o << f.frame << ',' << f.video_row << ',';
        o.precision(6); o << f.t_bar_s << ',';
        o << f.imu_sample << ',' << (f.tagged ? 1 : 0) << '\n';
    }
    return true;
}

} // namespace vbt::offline

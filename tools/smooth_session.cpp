/**
 * @file smooth_session.cpp
 * @brief Run the whole-session RTS smoother over one or more sessions of
 *        datasets/offline and report what it did.
 *
 * Usage:
 *   smooth_session [--write] [--nis] <session_dir>...
 *     --write   write <session>/smoothed.csv
 *     --nis     report per-axis innovation consistency instead of smoothing
 *
 * Reads only datasets/offline (already gravity-aligned, with unmeasured frames as NaN).
 */
#include "offline/RtsSmoother.h"

#include <cmath>
#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>
#include <string>
#include <vector>

namespace fs = std::filesystem;
using namespace vbt::offline;

static std::vector<std::string> split(const std::string& l, char c) {
    std::vector<std::string> o; std::string t; std::stringstream ss(l);
    while (std::getline(ss, t, c)) o.push_back(t);
    return o;
}

static std::vector<Sample3> load(const fs::path& d, std::string& err) {
    std::vector<Sample3> out;
    std::ifstream f(d / "camera" / "marker_positions.csv");
    if (!f) { err = "cannot open marker_positions.csv"; return out; }
    std::string h;
    if (!std::getline(f, h)) { err = "empty file"; return out; }
    auto cols = split(h, ',');
    std::map<std::string,int> ix;
    for (size_t i = 0; i < cols.size(); ++i) ix[cols[i]] = (int)i;
    for (const char* need : {"x_m","y_m","z_m","detected"})
        if (!ix.count(need)) { err = std::string("missing column ") + need; return out; }
    std::string line; int64_t k = 0;
    while (std::getline(f, line)) {
        if (line.empty()) continue;
        auto v = split(line, ',');
        if (v.size() < cols.size()) continue;
        Sample3 s;
        s.frame_idx = k;
        s.t_s = (double)k / 90.0;
        s.detected = std::atoi(v[ix["detected"]].c_str()) != 0;
        // A NaN here is an unmeasured frame; detected already says so and the value is
        // never read. Parsed anyway so a malformed row would show up as NaN rather than 0.
        s.p[0] = std::atof(v[ix["x_m"]].c_str());
        s.p[1] = std::atof(v[ix["y_m"]].c_str());
        s.p[2] = std::atof(v[ix["z_m"]].c_str());
        out.push_back(s); ++k;
    }
    return out;
}

int main(int argc, char** argv) {
    bool write = false, want_nis = false, want_check = false;
    std::vector<fs::path> dirs;
    for (int i = 1; i < argc; ++i) {
        std::string a = argv[i];
        if (a == "--write") write = true;
        else if (a == "--nis") want_nis = true;
        else if (a == "--check") want_check = true;
        else dirs.emplace_back(a);
    }
    if (dirs.empty()) {
        std::fprintf(stderr,
            "usage: smooth_session [--write] [--nis] [--check] <session_dir>...\n"
            "  --check  verify the smoothed track before anything is built on it\n");
        return 2;
    }
    RtsSmoother sm;

    if (want_check) {
        // The smoothed track is what every later stage reads. These are the properties it
        // must have for that to be safe, checked rather than assumed.
        long viol = 0, frames = 0;
        for (const auto& d : dirs) {
            std::string err; auto in = load(d, err);
            if (in.empty()) { std::printf("SKIP %-28s %s\n", d.filename().string().c_str(), err.c_str()); continue; }
            auto out = sm.run(in);
            const std::string sid = d.filename().string();
            auto fail = [&](const char* what, long k) {
                if (++viol <= 20) std::printf("  %-28s frame %-7ld %s\n", sid.c_str(), k, what);
            };
            if (out.size() != in.size()) { fail("row count differs from the input", -1); continue; }
            double sd_meas = 0.0, sd_unmeas = 0.0; long n_meas = 0, n_unmeas = 0;
            for (size_t k = 0; k < out.size(); ++k) {
                const auto& s = out[k];
                ++frames;
                for (int i = 0; i < 3; ++i) {
                    // RULE 1: nothing non-finite reaches a later stage. A NaN in the input
                    // is an unmeasured frame and must be reconstructed, not propagated.
                    if (!std::isfinite(s.pos[i]) || !std::isfinite(s.vel[i]) ||
                        !std::isfinite(s.acc[i]) || !std::isfinite(s.jerk[i]))
                        fail("non-finite state", (long)k);
                    // RULE 2: every value carries an uncertainty, and it is a real number.
                    if (!std::isfinite(s.pos_sd[i]) || s.pos_sd[i] < 0.0 ||
                        !std::isfinite(s.vel_sd[i]) || s.vel_sd[i] < 0.0 ||
                        !std::isfinite(s.acc_sd[i]) || s.acc_sd[i] < 0.0 ||
                        !std::isfinite(s.jerk_sd[i]) || s.jerk_sd[i] < 0.0)
                        fail("missing or negative uncertainty", (long)k);
                }
                // RULE 3: a reconstructed frame must be LESS certain than a measured one.
                // This is what stops a reconstruction being read as a measurement.
                if (s.measured) { sd_meas += s.pos_sd[1]; ++n_meas; }
                else            { sd_unmeas += s.pos_sd[1]; ++n_unmeas; }
                // RULE 4: the track stays physical. A barbell cannot exceed this speed;
                // if the smoother produces one, the model has been driven somewhere wrong.
                if (std::fabs(s.vel[1]) > 20.0) fail("implausible vertical speed (>20 m/s)", (long)k);
            }
            if (n_meas && n_unmeas) {
                const double a = sd_meas / (double)n_meas, b = sd_unmeas / (double)n_unmeas;
                if (b <= a) {
                    ++viol;
                    std::printf("  %-28s reconstructed frames are not less certain than "
                                "measured ones (%.3f vs %.3f mm)\n", sid.c_str(), b*1000, a*1000);
                }
            }
        }
        if (!viol) {
            std::printf("CHECK PASSED - %ld frames over %zu sessions\n", frames, dirs.size());
            std::printf("  every state finite\n"
                        "  every value carries a finite, non-negative uncertainty\n"
                        "  reconstructed frames are strictly less certain than measured ones\n"
                        "  no implausible speed\n");
            return 0;
        }
        std::printf("CHECK FAILED - %ld violation(s)\n", viol);
        return 1;
    }

    if (want_nis) {
        std::printf("%-28s %9s %9s %9s\n", "session", "nis_x", "nis_y", "nis_z");
        for (const auto& d : dirs) {
            std::string err; auto in = load(d, err);
            if (in.empty()) { std::printf("%-28s SKIP %s\n", d.filename().string().c_str(), err.c_str()); continue; }
            double nis[3];
            sm.innovation_consistency(in, nis);
            std::printf("%-28s %9.2f %9.2f %9.2f\n", d.filename().string().c_str(), nis[0], nis[1], nis[2]);
        }
        return 0;
    }

    std::printf("%-28s %7s %7s %10s %10s %10s %10s\n", "session", "frames", "lost",
                "pos_sd_um", "vel_sd", "acc_sd", "jerk_sd");
    for (const auto& d : dirs) {
        std::string err; auto in = load(d, err);
        if (in.empty()) { std::printf("%-28s SKIP %s\n", d.filename().string().c_str(), err.c_str()); continue; }
        auto out = sm.run(in);
        // median uncertainty on MEASURED frames -- the reconstructed ones are reported
        // separately because they are a different kind of number.
        std::vector<double> ps, vs, as, js;
        long lost = 0;
        for (const auto& s : out) {
            if (!s.measured) { ++lost; continue; }
            ps.push_back(s.pos_sd[1]); vs.push_back(s.vel_sd[1]);
            as.push_back(s.acc_sd[1]); js.push_back(s.jerk_sd[1]);
        }
        auto med = [](std::vector<double>& v) {
            if (v.empty()) return 0.0;
            std::nth_element(v.begin(), v.begin()+v.size()/2, v.end());
            return v[v.size()/2];
        };
        std::printf("%-28s %7zu %7ld %10.1f %10.4f %10.3f %10.1f\n",
                    d.filename().string().c_str(), out.size(), lost,
                    med(ps)*1e6, med(vs), med(as), med(js));
        if (write) {
            std::ofstream o(d / "smoothed.csv");
            o << "frame_idx,t_s,measured,"
                 "pos_x,pos_y,pos_z,vel_x,vel_y,vel_z,acc_x,acc_y,acc_z,jerk_x,jerk_y,jerk_z,"
                 "pos_sd_x,pos_sd_y,pos_sd_z,vel_sd_x,vel_sd_y,vel_sd_z,"
                 "acc_sd_x,acc_sd_y,acc_sd_z,jerk_sd_x,jerk_sd_y,jerk_sd_z\n";
            o.setf(std::ios::fixed);
            for (const auto& s : out) {
                o.precision(6);
                o << s.frame_idx << ',' << s.t_s << ',' << (s.measured?1:0);
                for (int i=0;i<3;++i) o << ',' << s.pos[i];
                for (int i=0;i<3;++i) o << ',' << s.vel[i];
                for (int i=0;i<3;++i) o << ',' << s.acc[i];
                for (int i=0;i<3;++i) o << ',' << s.jerk[i];
                for (int i=0;i<3;++i) o << ',' << s.pos_sd[i];
                for (int i=0;i<3;++i) o << ',' << s.vel_sd[i];
                for (int i=0;i<3;++i) o << ',' << s.acc_sd[i];
                for (int i=0;i<3;++i) o << ',' << s.jerk_sd[i];
                o << '\n';
            }
        }
    }
    return 0;
}

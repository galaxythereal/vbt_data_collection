#include "rt_annotator/RtAnnotationIO.h"

#include <cstdio>
#include <cstdlib>
#include <filesystem>
#include <fstream>
#include <map>
#include <sstream>

namespace fs = std::filesystem;

namespace vbt::rt {

bool rt_down_first(const std::string& exercise) {
    return exercise == "bench_press" || exercise == "back_squat";
}

double rt_rom_prior_m(const std::string& exercise) {
    if (exercise == "deadlift")    return 0.60;
    if (exercise == "back_squat")  return 0.55;
    if (exercise == "bench_press") return 0.45;
    return 0.50;   // biceps_curl, barbell_row
}

RtAnnotator::Config rt_config_for(const std::string& exercise) {
    RtAnnotator::Config c;
    c.down_first  = rt_down_first(exercise);
    c.rom_prior_m = rt_rom_prior_m(exercise);
    return c;
}

bool rt_write_file(const std::string& file,
                   const std::string& exercise,
                   const std::vector<RtRep>& reps,
                   std::string& err) {
    // The CALLER names the file. The live annotation is DERIVED from the measurement, so
    // it is written beside camera/ and imu/ rather than inside them: those two hold the
    // measurement and are sealed read-only.
    try {
        const fs::path tmp = fs::path(file).string() + ".tmp";
        const fs::path out = file;
        {
            std::ofstream f(tmp, std::ios::trunc);
            if (!f) { err = "cannot open " + tmp.string(); return false; }
            f << "# real-time (causal) annotation produced during acquisition\n"
              << "# exercise=" << exercise
              << " rom_prior_m=" << rt_rom_prior_m(exercise)
              << " down_first=" << (rt_down_first(exercise) ? 1 : 0) << "\n"
              << "# gap_frames counts the frames of the rep on which the marker was NOT "
                 "seen. A missing measurement is never interpolated or extrapolated: no "
                 "boundary, rest or statistic in this file is derived from an unseen "
                 "frame.\n"
              << "# confirmed=1 means the rep's cycle CLOSED (the bar returned to the "
                 "level it started from). Provisional reps (confirmed=0) completed a "
                 "concentric but never returned - typically an unrack/rack/pickup.\n"
              << "# down_first=1 means a rep of this exercise STARTS with the eccentric "
                 "(bench, squat), so the phases run eccentric -> bottom_rest -> "
                 "concentric. down_first=0 runs concentric -> top_rest -> eccentric. "
                 "The bit moves only where the rep boundary is drawn.\n"
              << "rep_id,concentric_start_frame,concentric_end_frame,"
                 "eccentric_start_frame,eccentric_end_frame,"
                 "top_rest_start_frame,top_rest_end_frame,"
                 "bottom_rest_start_frame,bottom_rest_end_frame,rom_m,peak_velocity,"
                 "mean_velocity,confirmed,dropped_eccentric,tracking_gap,gap_frames\n";
            for (const auto& r : reps) {
                f << r.rep_id << ','
                  << r.concentric_start_frame  << ',' << r.concentric_end_frame  << ','
                  << r.eccentric_start_frame   << ',' << r.eccentric_end_frame   << ','
                  << r.top_rest_start_frame    << ',' << r.top_rest_end_frame    << ','
                  << r.bottom_rest_start_frame << ',' << r.bottom_rest_end_frame << ','
                  << r.rom_m << ',' << r.peak_velocity << ',' << r.mean_velocity << ','
                  << (r.confirmed ? 1 : 0) << ','
                  << (r.dropped_eccentric ? 1 : 0) << ','
                  << (r.tracking_gap ? 1 : 0) << ',' << r.gap_frames << '\n';
            }
            f.flush();
            if (!f) { err = "write failed: " + tmp.string(); return false; }
        }
        fs::rename(tmp, out);
        return true;
    } catch (const std::exception& e) {
        err = e.what();
        return false;
    }
}

bool rt_read_file(const std::string& file, std::vector<RtRep>& out, std::string& err) {
    out.clear();
    const fs::path p = file;
    std::error_code ec;
    if (!fs::exists(p, ec)) return true;          // absent is not an error

    std::ifstream f(p);
    if (!f) { err = "cannot open " + p.string(); return false; }

    std::string line;
    std::map<std::string, int> ix;
    while (std::getline(f, line)) {
        if (line.empty() || line[0] == '#') continue;
        std::vector<std::string> cell;
        {
            std::stringstream ss(line);
            std::string c;
            while (std::getline(ss, c, ',')) cell.push_back(c);
        }
        if (ix.empty()) {                          // header row
            for (size_t i = 0; i < cell.size(); ++i) ix[cell[i]] = static_cast<int>(i);
            continue;
        }
        auto get = [&](const char* k) -> std::string {
            auto it = ix.find(k);
            return (it != ix.end() && it->second < (int)cell.size()) ? cell[it->second] : "";
        };
        RtRep r;
        r.rep_id                 = std::atoi(get("rep_id").c_str());
        r.concentric_start_frame = std::atoll(get("concentric_start_frame").c_str());
        r.concentric_end_frame   = std::atoll(get("concentric_end_frame").c_str());
        r.eccentric_start_frame  = std::atoll(get("eccentric_start_frame").c_str());
        r.eccentric_end_frame    = std::atoll(get("eccentric_end_frame").c_str());
        // Rest columns are optional: a file written before the phase-order bit reached
        // rep construction has no bottom_rest, and an absent column means "no pause",
        // which is -1, not 0.
        auto rest_col = [&](const char* k) -> int64_t {
            const std::string v = get(k);
            return v.empty() ? -1 : std::atoll(v.c_str());
        };
        r.top_rest_start_frame    = rest_col("top_rest_start_frame");
        r.top_rest_end_frame      = rest_col("top_rest_end_frame");
        r.bottom_rest_start_frame = rest_col("bottom_rest_start_frame");
        r.bottom_rest_end_frame   = rest_col("bottom_rest_end_frame");
        r.rom_m                  = std::atof(get("rom_m").c_str());
        r.peak_velocity          = std::atof(get("peak_velocity").c_str());
        r.mean_velocity          = std::atof(get("mean_velocity").c_str());
        r.confirmed              = std::atoi(get("confirmed").c_str()) != 0;
        r.dropped_eccentric      = std::atoi(get("dropped_eccentric").c_str()) != 0;
        r.tracking_gap           = std::atoi(get("tracking_gap").c_str()) != 0;
        r.gap_frames             = std::atoi(get("gap_frames").c_str());
        out.push_back(r);
    }
    return true;
}

} // namespace vbt::rt

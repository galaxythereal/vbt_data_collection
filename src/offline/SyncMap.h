#pragma once

/**
 * @file SyncMap.h
 * @brief Which inertial sample is which camera frame, taken from the hardware trigger.
 *
 * WHY NOT JUST DIVIDE BY 90. The pipeline used to date every frame at `index / 90`. The
 * camera does not run at 90 Hz. Measured from its own trigger pulses, read inside the
 * inertial stream, it runs at 89.8654 Hz -- and it does so to within 35 ppm in every one
 * of the 84 sessions. Assuming 90.000 is therefore a systematic 1500 ppm error, always in
 * the same direction. It costs almost nothing in velocity (1.6 mm/s on a 1.08 m/s peak,
 * against 8 mm/s of measurement noise), but it costs 90 ms of absolute time over a
 * one-minute session -- ninety inertial samples -- which is the whole point of having a
 * shared clock in the first place.
 *
 * WHAT THIS PRODUCES. Two things:
 *
 *   the frame period, measured from THIS session's own pulses, so no rate is assumed
 *
 *   a row per camera frame saying which inertial sample is the same instant, so anything
 *   built on the inertial stream can look the correspondence up instead of re-deriving
 *   the synchronisation and getting it subtly wrong
 *
 * HOW THE PULSES ARE COUNTED. A tag is placed on the inertial sampling grid, so the gap
 * between consecutive tags alternates between ten and eleven sample periods and no single
 * gap is the frame period. The period comes from the whole span divided by the number of
 * frame periods it contains, with a gap counted as the number of periods it spans -- which
 * is also what makes it survive the pulses the inertial stream missed (up to 309 in one
 * session, and every one of those sessions still lands within 50 ppm of the rest).
 */

#include <cstdint>
#include <filesystem>
#include <string>
#include <vector>

namespace vbt::offline {

/// One camera frame, and the inertial sample that is the same instant.
struct FrameTime {
    int64_t frame      = -1;   ///< index into the track, the same one the annotation uses
    int     video_row  = -1;   ///< row in ir_video.mp4 / marker_positions.csv, -1 if dropped
    double  t_bar_s    = 0.0;  ///< when the exposure happened, in the bar's clock
    int64_t imu_sample = -1;   ///< row of imu/raw_imu.csv at that instant, -1 if unknown
    bool    tagged     = false;///< a real trigger pulse, rather than filled in between two
};

struct SyncMap {
    bool   valid        = false;
    double frame_period = 1.0 / 90.0;  ///< seconds, measured from this session
    long   pulses       = 0;           ///< trigger pulses found in the inertial stream
    long   missed       = 0;           ///< pulses the inertial stream did not record
    double t0_bar_s     = 0.0;         ///< the bar's clock at the first frame
    std::vector<FrameTime> frames;
    std::string note;
};

/// Read the trigger pulses out of imu/raw_imu.csv and build the map. `n_frames` is the
/// length of the track the annotation runs on; the map is padded or trimmed to it.
/// A session whose inertial stream is unusable returns valid == false and the caller
/// falls back to the nominal rate, saying so rather than pretending.
SyncMap build_sync_map(const std::filesystem::path& session_dir,
                       const std::vector<int>& video_row,
                       size_t n_frames);

/// Write it beside the measurement as <session>/sync_map.csv.
bool write_sync_map(const SyncMap& m, const std::filesystem::path& out_csv, std::string& err);

} // namespace vbt::offline

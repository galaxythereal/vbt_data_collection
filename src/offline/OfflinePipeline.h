#pragma once

/**
 * @file OfflinePipeline.h
 * @brief The whole post-session pass, in one call, inside the app.
 *
 * Every stage either removes something untrue or adds something measured:
 *
 *   1. Read the camera's own tilt from its own accelerometer. DIRECTION ONLY, so the
 *      part's ~6% scale error (it reads 9.198 where gravity is 9.807) cancels exactly.
 *      Gravity fixes pitch and roll; which way the camera faces is invisible to any
 *      accelerometer and is a stated convention, not a measurement.
 *   2. Read the sealed raw marker track, blank every position the acquisition tracker
 *      fabricated on a frame it could not see, and rotate the rest upright.
 *   3. Smooth the whole session forwards and backwards. This also fills the gaps, by
 *      pinning the trajectory between two anchors instead of extrapolating off one.
 *   4. Re-run the SAME causal annotator the acquisition loop ran, on the corrected track,
 *      so anything taken from it afterwards refers to the data actually being worked on.
 *   5. Take the three lines from its middle reps and annotate. See OfflineAnnotator.h.
 *
 * Nothing here writes inside camera/ or imu/. Those are the measurement; everything
 * this produces is written beside them in the session directory.
 */

#include <cstdint>
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

#include "offline/OfflineAnnotator.h"
#include "offline/RtsSmoother.h"
#include "rt_annotator/RtTypes.h"

namespace vbt::offline {

/// One session is one directory. camera/ and imu/ inside it are the measurement and stay
/// read-only; everything derived sits beside them.
struct SessionPaths {
    std::filesystem::path dir;    ///< datasets/<session>
    std::string           id;

    std::filesystem::path markers()    const { return dir / "camera" / "marker_positions.csv"; }
    std::filesystem::path camera_imu() const { return dir / "imu" / "camera_imu.csv"; }
    std::filesystem::path metadata()   const { return dir / "metadata.json"; }
    std::filesystem::path rotation()   const { return dir / "rotation.json"; }
    std::filesystem::path smoothed()   const { return dir / "smoothed.csv"; }
    std::filesystem::path live()       const { return dir / "annotation_live.csv"; }
    std::filesystem::path online()     const { return dir / "annotation_online.csv"; }
    std::filesystem::path offline()    const { return dir / "annotation_offline.csv"; }
    std::filesystem::path reviewed()   const { return dir / "annotation_reviewed.csv"; }
};

/// The camera's tilt, from its own accelerometer. Direction only.
struct GravityFrame {
    double R[3][3]   = {{1,0,0},{0,1,0},{0,0,1}};  ///< rows: new axes in camera axes
    double up[3]     = {0,-1,0};
    double accel_mean[3] = {0,0,0};
    double accel_magnitude = 0.0;   ///< reported, never used: only the direction is
    double tilt_deg  = 0.0;
    long   samples   = 0;
    bool   valid     = false;
};

/// Everything the pass produced for one session.
struct PipelineResult {
    bool         ok = false;
    std::string  message;
    std::string  session_id;
    std::string  exercise;
    bool         down_first = false;

    // Carried through from the session's own metadata so the ground truth needs nothing
    // else to be read: whoever consumes it should not have to open a second file.
    std::string  session_date;      ///< ISO local time the set was recorded
    std::string  subject_id;        ///< anonymised
    double       load_kg   = 0.0;   ///< bar + added
    int          target_reps = 0;   ///< what was PRESCRIBED. Never a count of what happened.
    bool         reviewed  = false; ///< a person has judged this session

    GravityFrame           frame;
    std::vector<Sample3>   samples;    ///< rotated, fabricated frames blanked
    /// The vertical exactly as recorded -- NOT rotated, NOT smoothed, NaN where the
    /// marker was not seen. This is the signal the live annotator worked on, so the live
    /// annotation is drawn over THIS and the post-session annotation over the corrected
    /// track. Showing both on the same curve would misrepresent what each one saw.
    std::vector<double>    raw_pos;
    std::vector<Smoothed3> smoothed;
    Track                  track;      ///< up-positive vertical, for the annotator and audit
    std::vector<rt::RtRep> online;     ///< the causal pass, re-run on this track
    Annotation             annotation; ///< the post-session pass
    double                 nis[3] = {0,0,0};   ///< smoother innovation consistency, per axis
    long                   lost_frames = 0;
};

class OfflinePipeline {
public:
    /// Called between stages so the app can show what it is doing. Never called from
    /// another thread than the one that called run().
    using ProgressFn = std::function<void(const char* stage, float frac)>;

    /// Run the whole pass. Reads only the measurement. Writes nothing.
    PipelineResult run(const SessionPaths& paths, const ProgressFn& on_progress = {}) const;

    /// Write the result into the session directory.
    /// Never touches an existing reviewed.csv -- a reviewer's decisions are not output.
    bool write(const PipelineResult& r, const SessionPaths& paths, std::string& err) const;

    /// Load a previously written annotation back, so a session already processed opens
    /// without re-running the pass.
    static bool load_annotation(const SessionPaths& paths, Annotation& out);

    /// The reviewer's accept/reject, kept in its own file so the algorithm's output stays
    /// exactly as the algorithm produced it.
    static bool write_review(const SessionPaths& paths, const Annotation& an, std::string& err);
    /// Returns true if a review was found. `stale` is set when the review was recorded
    /// against a DIFFERENT annotation than the one now in hand -- a reviewer's judgement
    /// belongs to the boundaries they were shown, so a later change to the algorithm must
    /// not silently inherit it.
    static bool load_review(const SessionPaths& paths, Annotation& an, bool* stale = nullptr);

    /// A content fingerprint of the rep boundaries. Not a security hash: it exists only
    /// so a recorded review can say which annotation it was made against.
    static uint64_t fingerprint(const Annotation& an);

    /// Append every accepted rep of this session to datasets/ground_truth.csv,
    /// replacing any rows this session had there before.
    static bool export_ground_truth(const std::filesystem::path& datasets_root,
                                    const PipelineResult& r, std::string& err);

    /// Which lifts start with the eccentric. The one per-session bit.
    static bool is_down_first(const std::string& exercise);
    static double rom_prior_for(const std::string& exercise);

    /// Stage 1 on its own; also used to report the tilt before anything is run.
    static GravityFrame gravity_frame(const std::filesystem::path& camera_imu_csv);
};

} // namespace vbt::offline

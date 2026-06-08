#pragma once

/**
 * @file GroundTruthIO.h
 * @brief Load/save the frame-indexed ground_truth.json + reference trace.csv,
 *        and convert between the studio's editable (time-based) reps + GT
 *        attributes and the on-disk GroundTruthLabel[] (camera-only frames).
 *
 * Camera-only frame base. Boundaries are edited in seconds (reusing the legacy
 * RepAnnotation drag UX) and snapped to an integer frame_idx on save via the
 * caller-supplied TimeToFrame/FrameToTime callbacks — which the studio backs with
 * VideoIndex (camera/video_frames.csv per-frame timestamps), so the snap is EXACT
 * at the true ~89.7 fps cadence, NOT round(t * 90). Nothing here touches the
 * dataset directory — labels live under a separate labels root.
 */

#include "annotation/GroundTruthLabel.h"
#include "processing/RepAnnotation.h"
#include <filesystem>
#include <functional>
#include <string>
#include <vector>

namespace vbt::ground_truth_io {

constexpr double kCameraFps = 90.0;   // camera-only base (FOUNDATION §0.4)

/// One row of the pipeline reference trace (frame_idx, t_s, s, v).
struct TracePoint { int frame_idx; double t_s; double s; double v; };

/// Maps an edit-axis time (s) → integer camera frame_idx, and the inverse.
/// The studio supplies these from VideoIndex (video_frames.csv) so the snap is
/// EXACT at the true ~89.7 fps cadence (not round(t·90)), camera-derived, no IMU.
using TimeToFrame = std::function<int(double)>;
using FrameToTime = std::function<double(int)>;

/// Combine the time-based boundary editor (reps) + the parallel GT attributes
/// into frame-indexed labels, snapping each boundary to a frame via `t2f`.
std::vector<GroundTruthLabel> reps_to_labels(
    const std::vector<RepAnnotation>& reps,
    const std::vector<GtAttr>& attrs,
    const TimeToFrame& t2f);

/// Inverse: build editable reps + aligned attrs from labels (prefill / resume),
/// placing boundaries on the edit axis via `f2t`.
void labels_to_reps(
    const std::vector<GroundTruthLabel>& labels, const FrameToTime& f2t,
    std::vector<RepAnnotation>& reps_out, std::vector<GtAttr>& attrs_out);

/// Path helpers under the labels root: <labels_root>/<session_id>/<name>.json
std::filesystem::path label_dir(const std::filesystem::path& labels_root,
                                const std::string& session_id);

/// Atomic write of ground_truth.json (a JSON array) to the labels root.
bool save(const std::filesystem::path& labels_root, const std::string& session_id,
          const std::vector<GroundTruthLabel>& labels, std::string& err);

/// Read a JSON-array label file (ground_truth.json or *.candidate.json).
bool load(const std::filesystem::path& file,
          std::vector<GroundTruthLabel>& out, std::string& err);

/// Read the reference trace.csv produced by export_prefill_for_studio.py.
bool load_trace(const std::filesystem::path& csv,
                std::vector<TracePoint>& out, std::string& err);

} // namespace vbt::ground_truth_io

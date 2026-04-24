#pragma once

/**
 * @file MarkerTracker.h
 * @brief IR reflective marker detection and 3D position tracking.
 *
 * Detects bright blobs in the IR image, validates them as retroreflective
 * markers, and deprojects to 3D using depth data and camera intrinsics.
 * Includes tracking confidence metrics for data quality validation.
 */

#include <opencv2/core.hpp>
#include <librealsense2/rs.hpp>
#include <vector>
#include <deque>
#include "app/Config.h"

namespace vbt {

// ============================================================================
// Marker Detection Result
// ============================================================================
struct MarkerDetection {
    // 2D detection in IR image
    float pixel_u      = 0.0f;   // Pixel column
    float pixel_v      = 0.0f;   // Pixel row
    float blob_area    = 0.0f;   // Blob area in pixels
    float circularity  = 0.0f;   // 0-1, how circular the blob is
    float snr          = 0.0f;   // Signal-to-noise ratio

    // 3D position in camera frame (meters)
    float x_m = 0.0f;
    float y_m = 0.0f;
    float z_m = 0.0f;    // Depth

    // Depth source
    enum class DepthSource { DEPTH_STREAM, STEREO_TRIANGULATION, INTERPOLATED, NONE };
    DepthSource depth_source = DepthSource::NONE;

    // Quality
    float confidence = 0.0f;     // 0-1 overall tracking confidence
    bool  detected   = false;
};

// ============================================================================
// Marker Tracker Class
// ============================================================================
class MarkerTracker {
public:
    MarkerTracker();
    ~MarkerTracker() = default;

    // Configure
    void configure(const CameraConfig& config);

    // Process a frame pair to detect and localize the marker
    MarkerDetection process(
        const cv::Mat& ir_left,
        const cv::Mat& ir_right,
        const cv::Mat& depth,
        const rs2_intrinsics& ir_intrinsics,
        const rs2_intrinsics& depth_intrinsics
    );

    // Get tracking statistics
    struct TrackingStats {
        uint64_t total_frames    = 0;
        uint64_t detected_frames = 0;
        uint64_t lost_frames     = 0;
        float    detection_rate  = 0.0f;   // 0-1
        float    avg_confidence  = 0.0f;
        float    avg_snr         = 0.0f;
    };
    TrackingStats get_stats() const { return stats_; }

    // Get debug visualization image
    cv::Mat get_debug_image() const;

private:
    // Detection pipeline
    cv::Mat threshold_ir(const cv::Mat& ir);
    std::vector<MarkerDetection> find_blobs(const cv::Mat& binary, const cv::Mat& ir);
    MarkerDetection select_best_blob(const std::vector<MarkerDetection>& candidates);
    void deproject_to_3d(MarkerDetection& det,
                         const cv::Mat& depth,
                         const rs2_intrinsics& depth_intrinsics);
    void stereo_triangulate(MarkerDetection& det,
                            const cv::Mat& ir_left,
                            const cv::Mat& ir_right,
                            const rs2_intrinsics& ir_intrinsics);

    // Prediction (for when marker is briefly lost)
    MarkerDetection predict_from_history();

    CameraConfig config_;
    TrackingStats stats_;

    // History for temporal filtering and prediction
    std::deque<MarkerDetection> history_;
    static constexpr size_t MAX_HISTORY = 10;

    // Debug image
    mutable std::mutex debug_mutex_;
    cv::Mat debug_image_;
};

} // namespace vbt

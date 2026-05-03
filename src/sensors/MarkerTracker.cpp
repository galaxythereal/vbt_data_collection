/**
 * @file MarkerTracker.cpp
 * @brief IR marker detection and 3D localization.
 */

#include "sensors/MarkerTracker.h"
#include <opencv2/imgproc.hpp>
#include <spdlog/spdlog.h>
#include <cmath>
#include <algorithm>

namespace vbt {

MarkerTracker::MarkerTracker() = default;

void MarkerTracker::configure(const CameraConfig& config) { config_ = config; }

cv::Mat MarkerTracker::threshold_ir(const cv::Mat& ir) {
    cv::Mat binary;
    cv::threshold(ir, binary, config_.marker_threshold, 255, cv::THRESH_BINARY);
    // Asymmetric morphology (ported from ir_tracker.cpp): small open kernel
    // removes single-pixel salt-and-pepper noise, larger close kernel fills
    // small holes inside the marker without smearing it.
    cv::Mat k3 = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(3, 3));
    cv::Mat k5 = cv::getStructuringElement(cv::MORPH_ELLIPSE, cv::Size(5, 5));
    cv::morphologyEx(binary, binary, cv::MORPH_OPEN,  k3);
    cv::morphologyEx(binary, binary, cv::MORPH_CLOSE, k5);
    return binary;
}

float MarkerTracker::robust_depth_median(const cv::Mat& depth_mm, int cx, int cy, int win) {
    // Collect non-zero depth samples in a (2*win+1)² patch and return median.
    // Median is the right central tendency here because the depth stream has
    // sparse holes / quantisation artefacts; mean would be biased by them.
    if (depth_mm.empty()) return 0.0f;
    int w = depth_mm.cols, h = depth_mm.rows;
    std::vector<float> samples;
    samples.reserve((2*win+1)*(2*win+1));
    for (int dy = -win; dy <= win; ++dy) {
        for (int dx = -win; dx <= win; ++dx) {
            int px = cx + dx, py = cy + dy;
            if (px < 0 || py < 0 || px >= w || py >= h) continue;
            uint16_t d = depth_mm.at<uint16_t>(py, px);
            if (d > 10) samples.push_back(d * 0.001f);  // ignore <1cm (noise) and zero (hole)
        }
    }
    if (samples.empty()) return 0.0f;
    auto mid = samples.begin() + samples.size() / 2;
    std::nth_element(samples.begin(), mid, samples.end());
    return *mid;
}

std::vector<MarkerDetection> MarkerTracker::find_blobs(const cv::Mat& binary, const cv::Mat& ir) {
    std::vector<std::vector<cv::Point>> contours;
    cv::findContours(binary, contours, cv::RETR_EXTERNAL, cv::CHAIN_APPROX_SIMPLE);

    std::vector<MarkerDetection> candidates;
    for (const auto& contour : contours) {
        float area = static_cast<float>(cv::contourArea(contour));
        if (area < config_.marker_min_area || area > config_.marker_max_area) continue;

        // Compute circularity
        float perimeter = static_cast<float>(cv::arcLength(contour, true));
        float circularity = (perimeter > 0) ? (4.0f * M_PI * area) / (perimeter * perimeter) : 0.0f;
        if (circularity < 0.3f) continue;  // Reject non-circular blobs

        // Compute centroid (sub-pixel via moments)
        cv::Moments m = cv::moments(contour);
        if (m.m00 < 1e-6) continue;

        MarkerDetection det;
        det.pixel_u = static_cast<float>(m.m10 / m.m00);
        det.pixel_v = static_cast<float>(m.m01 / m.m00);
        det.blob_area = area;
        det.circularity = circularity;

        // Compute SNR: mean marker brightness vs background
        cv::Rect bbox = cv::boundingRect(contour);
        cv::Mat roi = ir(bbox);
        cv::Scalar mean_val, stddev_val;
        cv::meanStdDev(roi, mean_val, stddev_val);
        float bg_mean = static_cast<float>(cv::mean(ir)[0]);
        det.snr = (bg_mean > 0) ? (mean_val[0] - bg_mean) / (stddev_val[0] + 1e-6f) : 0.0f;

        det.detected = true;
        candidates.push_back(det);
    }
    return candidates;
}

MarkerDetection MarkerTracker::select_best_blob(const std::vector<MarkerDetection>& candidates) {
    if (candidates.empty()) return {};

    // If we have history, prefer the blob closest to predicted position
    if (!history_.empty()) {
        const auto& last = history_.back();
        float min_dist = 1e9f;
        int best_idx = 0;
        for (size_t i = 0; i < candidates.size(); i++) {
            float dx = candidates[i].pixel_u - last.pixel_u;
            float dy = candidates[i].pixel_v - last.pixel_v;
            float dist = std::sqrt(dx * dx + dy * dy);
            if (dist < min_dist) { min_dist = dist; best_idx = (int)i; }
        }
        // Reject if too far from prediction (>100 pixels)
        if (min_dist < 100.0f) return candidates[best_idx];
    }

    // No history or all too far: pick brightest (highest SNR)
    auto it = std::max_element(candidates.begin(), candidates.end(),
        [](const MarkerDetection& a, const MarkerDetection& b) { return a.snr < b.snr; });
    return *it;
}

void MarkerTracker::deproject_to_3d(MarkerDetection& det, const cv::Mat& depth,
                                     const rs2_intrinsics& depth_intrinsics) {
    if (depth.empty()) { det.depth_source = MarkerDetection::DepthSource::NONE; return; }

    int u = std::clamp((int)std::round(det.pixel_u), 0, depth.cols - 1);
    int v = std::clamp((int)std::round(det.pixel_v), 0, depth.rows - 1);

    // Median over an 11×11 patch (win=5). More robust to depth holes / outliers
    // than the previous 5×5 mean.
    float depth_m = robust_depth_median(depth, u, v, 5);
    if (depth_m > 0.01f) {
        float pixel[2] = { det.pixel_u, det.pixel_v };
        float point[3];
        rs2_deproject_pixel_to_point(point, &depth_intrinsics, pixel, depth_m);
        det.x_m = point[0]; det.y_m = point[1]; det.z_m = point[2];
        det.depth_source = MarkerDetection::DepthSource::DEPTH_STREAM;
    } else {
        det.depth_source = MarkerDetection::DepthSource::NONE;
    }
}

void MarkerTracker::stereo_triangulate(MarkerDetection& det,
                                        const cv::Mat& ir_left, const cv::Mat& ir_right,
                                        const rs2_intrinsics& ir_intrinsics) {
    // Stereo triangulation fallback when depth stream fails
    // Find corresponding blob in right IR image
    cv::Mat binary_r = threshold_ir(ir_right);
    auto right_blobs = find_blobs(binary_r, ir_right);
    if (right_blobs.empty()) return;

    // Match by vertical proximity (epipolar constraint: same row)
    float best_match_dist = 1e9f;
    int best_idx = -1;
    for (size_t i = 0; i < right_blobs.size(); i++) {
        float dy = std::abs(right_blobs[i].pixel_v - det.pixel_v);
        if (dy < 5.0f && dy < best_match_dist) {
            best_match_dist = dy; best_idx = (int)i;
        }
    }
    if (best_idx < 0) return;

    float disparity = det.pixel_u - right_blobs[best_idx].pixel_u;
    if (disparity < 1.0f) return;  // Too small disparity

    // D455 baseline = 95mm
    float baseline = 0.095f;
    float depth_m = (baseline * ir_intrinsics.fx) / disparity;
    float pixel[2] = { det.pixel_u, det.pixel_v };
    float point[3];
    rs2_deproject_pixel_to_point(point, &ir_intrinsics, pixel, depth_m);
    det.x_m = point[0]; det.y_m = point[1]; det.z_m = point[2];
    det.depth_source = MarkerDetection::DepthSource::STEREO_TRIANGULATION;
}

MarkerDetection MarkerTracker::predict_from_history() {
    if (history_.size() < 2) return {};
    // Simple linear prediction from last two detections
    const auto& h1 = history_[history_.size() - 2];
    const auto& h2 = history_[history_.size() - 1];
    MarkerDetection pred;
    pred.pixel_u = 2 * h2.pixel_u - h1.pixel_u;
    pred.pixel_v = 2 * h2.pixel_v - h1.pixel_v;
    pred.x_m = 2 * h2.x_m - h1.x_m;
    pred.y_m = 2 * h2.y_m - h1.y_m;
    pred.z_m = 2 * h2.z_m - h1.z_m;
    pred.confidence = 0.3f;
    pred.detected = false;
    return pred;
}

MarkerDetection MarkerTracker::process(const cv::Mat& ir_left, const cv::Mat& ir_right,
                                        const cv::Mat& depth,
                                        const rs2_intrinsics& ir_intrinsics,
                                        const rs2_intrinsics& depth_intrinsics) {
    stats_.total_frames++;

    // Step 1: Threshold IR image
    cv::Mat binary = threshold_ir(ir_left);

    // Step 2: Find blobs
    auto candidates = find_blobs(binary, ir_left);

    // Step 3: Select best blob
    MarkerDetection det = select_best_blob(candidates);

    if (det.detected) {
        // Step 4a: Deproject to 3D using depth stream
        deproject_to_3d(det, depth, depth_intrinsics);

        // Step 4b: If depth failed, try stereo triangulation
        if (det.depth_source == MarkerDetection::DepthSource::NONE) {
            stereo_triangulate(det, ir_left, ir_right, ir_intrinsics);
        }

        // Compute confidence
        det.confidence = std::min(1.0f, det.circularity * 0.4f + std::min(det.snr / 20.0f, 1.0f) * 0.4f
                         + (det.depth_source != MarkerDetection::DepthSource::NONE ? 0.2f : 0.0f));

        stats_.detected_frames++;
    } else {
        // Use prediction
        det = predict_from_history();
        stats_.lost_frames++;
    }

    // Update history
    history_.push_back(det);
    if (history_.size() > MAX_HISTORY) history_.pop_front();

    // Update stats
    stats_.detection_rate = (float)stats_.detected_frames / std::max(stats_.total_frames, (uint64_t)1);
    stats_.avg_confidence = (stats_.avg_confidence * (stats_.total_frames - 1) + det.confidence) / stats_.total_frames;
    stats_.avg_snr = (stats_.avg_snr * (stats_.total_frames - 1) + det.snr) / stats_.total_frames;

    // Update debug image
    {
        std::lock_guard<std::mutex> lock(debug_mutex_);
        cv::cvtColor(ir_left, debug_image_, cv::COLOR_GRAY2BGR);
        if (det.detected) {
            cv::circle(debug_image_, cv::Point((int)det.pixel_u, (int)det.pixel_v), 15, cv::Scalar(0, 255, 0), 2);
            cv::putText(debug_image_, cv::format("%.3fm", det.z_m),
                        cv::Point((int)det.pixel_u + 20, (int)det.pixel_v),
                        cv::FONT_HERSHEY_SIMPLEX, 0.5, cv::Scalar(0, 255, 0), 1);
        }
    }

    return det;
}

cv::Mat MarkerTracker::get_debug_image() const {
    std::lock_guard<std::mutex> lock(debug_mutex_);
    return debug_image_.clone();
}

} // namespace vbt

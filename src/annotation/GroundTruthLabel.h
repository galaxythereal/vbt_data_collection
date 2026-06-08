#pragma once

/**
 * @file GroundTruthLabel.h
 * @brief Frame-indexed ground-truth label model for the Step-7 labeling tool.
 *
 * This is a SEPARATE model from the legacy time-based RepAnnotation
 * (processing/RepAnnotation.h) — the legacy `annotations/rep_segments.json`
 * path stays isolated and read-only. A GroundTruthLabel is one rep/attempt in
 * the EXACT schema that the Python `metrics/eval.py` consumes: integer frame
 * indices on the camera-only time base (t = frame_idx / 90), plus an
 * IntervalOutcome status (FOUNDATION §0.5), ROM-completeness, and pause tag.
 *
 * On-disk: `ground_truth.json` is a JSON ARRAY of these objects, written to the
 * labels root (NOT into the read-only dataset). `eccentric_*` are null when the
 * eccentric is absent (dropped/concentric-only), serialized as -1 in memory.
 */

#include <array>
#include <string>
#include <nlohmann/json.hpp>

namespace vbt {

/// Final per-rep/attempt status. Mirrors FOUNDATION §0.5 IntervalOutcome
/// (Python is the contract; the round-trip test asserts the string values match).
enum class IntervalOutcome {
    CompletedRep,            // completed_rep                  (COUNTS)
    CompletedRepReducedRom,  // completed_rep_reduced_rom      (COUNTS)
    ConcentricOnly,          // concentric_only                (COUNTS)
    PartialFailed,           // partial_failed                 (no count)
    EccentricOnly,           // eccentric_only                 (no count)
    Transport,               // transport
    TrackingInvalid,         // tracking_invalid
    UncertainReview,         // uncertain_review
};

inline const std::array<const char*, 8>& outcome_values() {
    static const std::array<const char*, 8> v = {
        "completed_rep", "completed_rep_reduced_rom", "concentric_only",
        "partial_failed", "eccentric_only", "transport",
        "tracking_invalid", "uncertain_review"};
    return v;
}

inline std::string outcome_to_string(IntervalOutcome o) {
    return outcome_values()[static_cast<size_t>(o)];
}

inline IntervalOutcome outcome_from_string(const std::string& s) {
    const auto& v = outcome_values();
    for (size_t i = 0; i < v.size(); ++i)
        if (s == v[i]) return static_cast<IntervalOutcome>(i);
    return IntervalOutcome::UncertainReview;   // unknown → flag for review
}

/// Whether a status increments the rep count (FOUNDATION §0.5 counting rule).
inline bool outcome_counts(IntervalOutcome o) {
    return o == IntervalOutcome::CompletedRep
        || o == IntervalOutcome::CompletedRepReducedRom
        || o == IntervalOutcome::ConcentricOnly;
}

struct GroundTruthLabel {
    int             set_id = 1;
    IntervalOutcome status = IntervalOutcome::CompletedRep;
    int             concentric_start_frame = -1;
    int             concentric_end_frame   = -1;
    int             eccentric_start_frame  = -1;   // -1 ⇒ null on disk
    int             eccentric_end_frame    = -1;   // -1 ⇒ null on disk
    double          rom              = 0.0;
    double          rom_completeness = 1.0;
    bool            has_pause  = false;
    std::string     pause_kind;                    // "", top_hold, chest_pause, …
    std::string     source = "manual";             // manual | pipeline_prefill

    nlohmann::json to_json() const {
        auto frame_or_null = [](int f) -> nlohmann::json {
            return f < 0 ? nlohmann::json(nullptr) : nlohmann::json(f);
        };
        return {
            {"set_id", set_id},
            {"status", outcome_to_string(status)},
            {"concentric_start_frame", concentric_start_frame},
            {"concentric_end_frame", concentric_end_frame},
            {"eccentric_start_frame", frame_or_null(eccentric_start_frame)},
            {"eccentric_end_frame", frame_or_null(eccentric_end_frame)},
            {"rom", rom},
            {"rom_completeness", rom_completeness},
            {"has_pause", has_pause},
            {"pause_kind", pause_kind},
            {"source", source},
        };
    }

    static GroundTruthLabel from_json(const nlohmann::json& j) {
        GroundTruthLabel g;
        auto frame = [](const nlohmann::json& v) -> int {
            return v.is_null() ? -1 : v.get<int>();
        };
        g.set_id = j.value("set_id", 1);
        g.status = outcome_from_string(j.value("status", "completed_rep"));
        g.concentric_start_frame = j.value("concentric_start_frame", -1);
        g.concentric_end_frame   = j.value("concentric_end_frame", -1);
        g.eccentric_start_frame = j.contains("eccentric_start_frame")
                                  ? frame(j.at("eccentric_start_frame")) : -1;
        g.eccentric_end_frame   = j.contains("eccentric_end_frame")
                                  ? frame(j.at("eccentric_end_frame")) : -1;
        g.rom              = j.value("rom", 0.0);
        g.rom_completeness = j.value("rom_completeness", 1.0);
        g.has_pause   = j.value("has_pause", false);
        g.pause_kind  = j.value("pause_kind", std::string());
        g.source      = j.value("source", std::string("manual"));
        return g;
    }
};

/// Editable per-rep attributes that are NOT boundary timestamps. Kept in a
/// vector aligned to SessionData::reps() (the existing time-based boundary
/// editor) so the legacy drag UX is reused unchanged; combined with the rep
/// boundaries at save time into GroundTruthLabel[].
struct GtAttr {
    IntervalOutcome status = IntervalOutcome::CompletedRep;
    bool            has_pause = false;
    std::string     pause_kind;
    float           rom_completeness = 1.0f;
    std::string     source = "manual";
};

} // namespace vbt

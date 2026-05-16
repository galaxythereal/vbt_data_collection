#pragma once

/**
 * @file SessionLibrary.h
 * @brief Discovers and indexes saved sessions in dataset_root.
 *
 * Walks dataset_root recursively for any directory containing
 * metadata.json. Reads a small "summary" subset of each session's metadata
 * (subject, exercise, weight, rep count, date) without loading the full
 * IMU/camera streams — that's what SessionData is for. The result powers
 * the AnnotationStudio's left-pane session browser, with sortable columns
 * and a search filter.
 *
 * Refresh is cheap and idempotent — a few hundred metadata.json reads per
 * second on cold cache.
 */

#include <string>
#include <vector>
#include <filesystem>

namespace vbt {

struct SessionSummary {
    std::filesystem::path dir;             // absolute path
    std::string label;                     // dir name relative to dataset_root
    std::string subject_id;
    std::string exercise;
    std::string variant;
    std::string date;                      // ISO-8601, or "" if not parseable
    int   rep_count   = 0;
    float total_weight_kg = 0;
    int   set_number  = 0;
    int   target_reps = 0;
    int   rpe         = 0;
    bool  partial     = false;             // .partial directory not yet committed
    bool  has_video   = false;
    bool  has_imu     = false;
    /// True if the session's manifest sha256 verifies. Recomputed lazily.
    enum class Integrity { Unknown, Ok, Bad };
    Integrity integrity = Integrity::Unknown;
};

class SessionLibrary {
public:
    explicit SessionLibrary(std::string dataset_root)
        : root_(std::move(dataset_root)) {}

    void set_root(std::string root) { root_ = std::move(root); }
    const std::string& root() const { return root_; }

    /// Enumerate sessions under root_. Sorted by date (descending) so the
    /// freshest session sits at the top of the browser.
    void refresh();

    const std::vector<SessionSummary>& sessions() const { return sessions_; }

    /// Filter the visible set; called after every keystroke in the search
    /// box. Match is case-insensitive substring against label, subject_id,
    /// exercise, variant.
    std::vector<int> filter(const std::string& query) const;

    /// Format a single line for the side-panel list: never longer than
    /// `max_chars` so it lays out nicely.
    static std::string format_label(const SessionSummary& s, int max_chars = 64);

private:
    void load_summary_(const std::filesystem::path& p, SessionSummary& s) const;

    std::string root_;
    std::vector<SessionSummary> sessions_;
};

} // namespace vbt

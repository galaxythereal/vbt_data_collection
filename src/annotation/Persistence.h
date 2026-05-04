#pragma once

/**
 * @file Persistence.h
 * @brief Atomic save-back for rep edits + metadata edits + manifest refresh.
 *
 * Mutations from the studio go through here. Two-phase save:
 *   1. Write to <file>.tmp,
 *   2. Recompute manifest entries (size + sha256),
 *   3. Append an audit row to events.jsonl with the operator's edit
 *      summary,
 *   4. fsync + rename for atomicity.
 *
 * No partial-state failure modes: if any step fails, the originals on
 * disk are unchanged and Persistence::save_*() returns false with the
 * reason in `last_error()`.
 */

#include "annotation/SessionData.h"
#include <string>

namespace vbt {

struct SaveOptions {
    /// If true, also re-emit per-rep summary stats (peak/mean velocity,
    /// ROM, durations) computed from the cleaned camera signal before
    /// writing rep_segments.json.
    bool recompute_metrics = true;
    /// If true, append a session-level audit entry to events.jsonl with
    /// who ran the studio (best-effort: $USER + git_sha).
    bool append_audit = true;
    /// Operator-supplied annotation summary surfaced in the audit row.
    std::string note;
};

class Persistence {
public:
    /// Persist rep edits. Writes annotations/rep_segments.json,
    /// recomputes manifest entries for that file, refreshes events.jsonl
    /// audit. Returns true on success.
    static bool save_reps(SessionData& session, const SaveOptions& opt);

    /// Persist metadata edits. Same atomic-write protocol.
    static bool save_metadata(SessionData& session, const SaveOptions& opt);

    /// Convenience: save everything dirty, then clear flags.
    static bool save_all(SessionData& session, const SaveOptions& opt);

    /// Last error message from a failed save, for the UI banner.
    static const std::string& last_error() { return last_error_; }

private:
    /// SHA-256 hex of a file's contents — stable, used to refresh manifest.
    static std::string sha256_of_file_(const std::filesystem::path& p);
    /// Write `content` to <path>.tmp + atomic rename to `path`.
    static bool atomic_write_(const std::filesystem::path& path, const std::string& content);
    /// Update manifest.files[] entry for `relpath` with current size + sha.
    static void update_manifest_entry_(SessionData& session,
                                        const std::string& relpath);
    /// Append a JSON row to events.jsonl. Always appends; if the file is
    /// missing, creates it.
    static bool append_event_(const std::filesystem::path& session_dir,
                              const std::string& level,
                              const std::string& code,
                              const std::string& msg,
                              const nlohmann::json& payload);

    static std::string last_error_;
};

} // namespace vbt

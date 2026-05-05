#pragma once

/**
 * @file SubjectRegistry.h
 * @brief Read-only scan of dataset_root to enumerate prior subjects.
 *
 * Lets the recording UI auto-increment the short anonymised `S01` /
 * `S02` / ... handle when a new subject is set up, and recover the
 * existing handle when an operator pastes a known subject_uuid (so the
 * folder naming stays consistent across sessions for the same person).
 *
 * No registry file is written — we always derive from the metadata.json
 * files present on disk. That keeps the dataset self-describing.
 */

#include <filesystem>
#include <optional>
#include <string>
#include <vector>

namespace vbt {

struct SubjectRecord {
    std::string subject_uuid;
    std::string subject_id;     // "S01" / "S07" / etc. — short anonymised
    std::string subject_name;   // human display
    std::filesystem::path latest_session;   // most recent session_dir for this subject
};

/// Scan `dataset_root/sessions` (and any *.partial siblings) for
/// metadata.json files and return one record per unique subject_uuid.
/// Sessions with no UUID are skipped — they pre-date the v4 schema.
std::vector<SubjectRecord> scan_subject_registry(const std::filesystem::path& dataset_root);

/// Find a SubjectRecord whose subject_uuid matches. Empty optional on
/// miss. Convenience wrapper around scan_subject_registry.
std::optional<SubjectRecord> lookup_subject_by_uuid(const std::filesystem::path& dataset_root,
                                                     const std::string& uuid);

/// Compute the next unused short ID following the "S{NN}" convention,
/// where NN is zero-padded to two digits. Returns "S01" if the registry
/// is empty or contains no IDs that match the pattern.
std::string next_subject_id(const std::filesystem::path& dataset_root);

} // namespace vbt

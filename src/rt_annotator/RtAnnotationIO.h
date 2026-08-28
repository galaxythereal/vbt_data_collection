#pragma once

/**
 * @file RtAnnotationIO.h
 * @brief Per-exercise configuration + on-disk format for the real-time annotation.
 *
 * The exercise profile lives here (one place, not three) so the acquisition app, the
 * annotation studio and the offline replay harness all configure the annotator
 * identically — otherwise "what the live annotator did" and "what replay says it did"
 * could silently diverge.
 *
 * On-disk: `<session>/camera/rt_annotation.csv`, written at save() time. It is the
 * live annotator's own output, so it is what the studio loads by default — the labels
 * the operator saw while recording are the labels they start reviewing from.
 */

#include "rt_annotator/RtAnnotator.h"

#include <string>
#include <vector>

namespace vbt::rt {

/// The cycle opens with the eccentric for bench/squat; the others start at the bottom.
bool rt_down_first(const std::string& exercise);

/// Nominal ROM per lift (m) — a PHYSIOLOGICAL prior (human anatomy), matching
/// vbt_gt/config.py EXERCISE_CONFIG. Never fitted to a session. Used only to establish
/// that a movement is on the scale of a human repetition (tracker jitter is ~5-12 mm,
/// a rep is ~0.5 m; no gravity-derived quantity separates those, amplitude does).
double rt_rom_prior_m(const std::string& exercise);

/// Fully configured annotator settings for an exercise name.
RtAnnotator::Config rt_config_for(const std::string& exercise);

/// Write reps to `<session_dir>/camera/rt_annotation.csv`. Returns false on IO error.
bool rt_write_csv(const std::string& out_dir,
                  const std::string& exercise,
                  const std::vector<RtRep>& reps,
                  std::string& err);

/// Read that file back. Missing file is NOT an error — `out` is left empty.
bool rt_read_csv(const std::string& in_dir,
                 std::vector<RtRep>& out,
                 std::string& err);

} // namespace vbt::rt

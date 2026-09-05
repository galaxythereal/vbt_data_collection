#pragma once

/**
 * @file AuditImage.h
 * @brief The post-session audit, drawn to a file.
 *
 * The review window is for judging one session in front of you. The audit is for looking
 * at many afterwards, and for putting a session in front of somebody who is not sitting at
 * the app. It is drawn at a fixed size from the data alone, so the same session always
 * produces the same image no matter how the window happened to be sized.
 *
 * Three panels, the same ones the window shows:
 *   height  the track, the three lines, every rep shaded, start and end marked
 *   speed   with each boundary marked -- filled where the bar arrived, hollow where it
 *           was let go while still travelling
 *   push    with the smoother's own uncertainty about it shaded; inside that band the
 *           direction of the push is not read
 *
 * Written to <session>/audit_post_session.png.
 */

#include <filesystem>
#include <string>

#include "offline/OfflinePipeline.h"

namespace vbt::offline {

/// Draw the audit for one session and write it. Returns false and fills `err` on failure.
bool write_audit_image(const PipelineResult& r,
                       const std::filesystem::path& out_png,
                       std::string& err);

} // namespace vbt::offline

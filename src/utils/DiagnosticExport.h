#pragma once

/**
 * @file DiagnosticExport.h
 * @brief Bundle current app state + recent toasts + version info into a JSON
 *        file (and adjacent README) for sharing with collaborators when
 *        something is broken. Does NOT include subject data.
 */

#include "app/Application.h"
#include <string>

namespace vbt {

/// Writes <out_path>.json (app state) and <out_path>.txt (human summary).
/// Returns the JSON path written, or empty on failure.
std::string export_diagnostic_bundle(const Application& app, const std::string& out_dir);

} // namespace vbt

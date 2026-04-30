#pragma once
/**
 * @file Fonts.h
 * @brief Shared TrueType fonts loaded once during ImGui init.
 *
 * Loaded from system DejaVu paths in Application::init_imgui(). All globals
 * may be nullptr if loading failed — callers must null-check before PushFont.
 */
#include <imgui.h>

namespace vbt {

extern ImFont* g_font_default;   // 16 px, default for body text
extern ImFont* g_font_title;     // 24 px bold, for hero headers
extern ImFont* g_font_mono;      // 15 px monospace, for raw values
extern ImFont* g_font_metric;    // 20 px bold, for big inline metric values

} // namespace vbt

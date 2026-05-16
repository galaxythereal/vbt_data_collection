#pragma once

/**
 * @file AudioCue.h
 * @brief Cross-platform short-blip audio cues for operator feedback.
 *
 * Uses platform-native APIs (System Sound on macOS, console BEL on Linux,
 * Beep() on Windows). No external dependencies; falls back silently if the
 * platform API is unavailable. Cues are fire-and-forget and run in a
 * detached thread to avoid blocking the GUI.
 */

#include <string>

namespace vbt {

enum class Cue {
    LiftOff,     // short ascending blip
    Lockout,     // double low blip
    SetComplete, // triumphant
    Warning,     // low buzzer
    Error,       // double low buzzer
    StartRecord, // single tone
    StopRecord,  // descending blip
};

class AudioCue {
public:
    static void play(Cue cue);
    static void set_enabled(bool enabled);
    static bool enabled();
};

} // namespace vbt

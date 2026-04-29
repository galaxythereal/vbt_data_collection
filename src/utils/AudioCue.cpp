#include "utils/AudioCue.h"
#include <atomic>
#include <thread>
#include <chrono>
#include <cstdlib>
#include <cstdio>
#include <string>

#ifdef __APPLE__
  // Use afplay /System/Sound files via fork+exec — no AudioToolbox dependency.
#elif defined(_WIN32)
  #include <windows.h>
#endif

namespace vbt {

namespace {
std::atomic<bool> g_enabled{true};

void play_blocking(Cue cue) {
#ifdef __APPLE__
    // System sounds — short, distinct, no extra deps
    const char* sound = "/System/Library/Sounds/Tink.aiff";
    switch (cue) {
        case Cue::LiftOff:     sound = "/System/Library/Sounds/Pop.aiff";     break;
        case Cue::Lockout:     sound = "/System/Library/Sounds/Tink.aiff";    break;
        case Cue::SetComplete: sound = "/System/Library/Sounds/Glass.aiff";   break;
        case Cue::Warning:     sound = "/System/Library/Sounds/Sosumi.aiff";  break;
        case Cue::Error:       sound = "/System/Library/Sounds/Basso.aiff";   break;
        case Cue::StartRecord: sound = "/System/Library/Sounds/Ping.aiff";    break;
        case Cue::StopRecord:  sound = "/System/Library/Sounds/Bottle.aiff";  break;
    }
    std::string cmd = std::string("afplay ") + sound + " > /dev/null 2>&1";
    (void)std::system(cmd.c_str());
#elif defined(_WIN32)
    switch (cue) {
        case Cue::LiftOff:     Beep(880, 80);  break;
        case Cue::Lockout:     Beep(440, 60); Beep(330, 60); break;
        case Cue::SetComplete: Beep(660, 80); Beep(880, 80); Beep(990, 100); break;
        case Cue::Warning:     Beep(220, 200); break;
        case Cue::Error:       Beep(180, 150); Beep(180, 150); break;
        case Cue::StartRecord: Beep(660, 100); break;
        case Cue::StopRecord:  Beep(440, 100); break;
    }
#else
    // Linux: console BEL — depends on terminal emulator. Best-effort.
    std::fputc('\a', stderr);
    std::fflush(stderr);
    (void)cue;
#endif
}
} // namespace

void AudioCue::play(Cue cue) {
    if (!g_enabled.load()) return;
    std::thread([cue]{ play_blocking(cue); }).detach();
}

void AudioCue::set_enabled(bool enabled) { g_enabled.store(enabled); }
bool AudioCue::enabled() { return g_enabled.load(); }

} // namespace vbt

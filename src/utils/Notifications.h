#pragma once

/**
 * @file Notifications.h
 * @brief Lightweight toast-style notification queue for ImGui.
 *
 * Push from anywhere (sensor callbacks, sync engine, validator); render once
 * per frame from the main thread. Each toast has a severity level that
 * controls its colour, an optional auto-dismiss timer, and is appended to
 * a ring buffer for the diagnostic export bundle.
 */

#include <string>
#include <deque>
#include <mutex>
#include <chrono>

namespace vbt {

enum class ToastLevel { Info, Success, Warning, Error };

struct Toast {
    ToastLevel  level    = ToastLevel::Info;
    std::string text;
    std::chrono::steady_clock::time_point created;
    float       lifetime_s = 4.0f;
};

class Notifications {
public:
    static Notifications& get();

    void push(ToastLevel level, std::string text, float lifetime_s = 4.0f);
    void info   (const std::string& s) { push(ToastLevel::Info,    s); }
    void success(const std::string& s) { push(ToastLevel::Success, s); }
    void warn   (const std::string& s) { push(ToastLevel::Warning, s, 6.0f); }
    void error  (const std::string& s) { push(ToastLevel::Error,   s, 8.0f); }

    /// Render at the bottom-right of the current viewport. Call once per frame.
    void render();

    /// Diagnostic export — copy of recent toasts.
    std::deque<Toast> snapshot() const;

    void clear();

private:
    Notifications() = default;
    mutable std::mutex mu_;
    std::deque<Toast> active_;     // currently displayed
    std::deque<Toast> history_;    // last N for diagnostics
    static constexpr size_t kMaxHistory = 200;
};

} // namespace vbt

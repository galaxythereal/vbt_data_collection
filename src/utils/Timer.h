#pragma once

/**
 * @file Timer.h
 * @brief High-resolution timer utilities.
 */

#include <chrono>

namespace vbt {

class Timer {
public:
    void start() { start_ = std::chrono::steady_clock::now(); }

    double elapsed_s() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration<double>(now - start_).count();
    }

    double elapsed_ms() const { return elapsed_s() * 1000.0; }
    double elapsed_us() const { return elapsed_s() * 1e6; }

    static double now_s() {
        return std::chrono::duration<double>(
            std::chrono::steady_clock::now().time_since_epoch()).count();
    }

private:
    std::chrono::steady_clock::time_point start_;
};

} // namespace vbt

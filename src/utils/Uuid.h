#pragma once

/**
 * @file Uuid.h
 * @brief Minimal RFC4122-v4 UUID generator (no external library).
 *
 * Used to mint subject UUIDs at session creation time. We seed once per
 * process from std::random_device + the high-resolution clock so two
 * sessions started in the same wall-clock second still get distinct
 * UUIDs.
 */

#include <array>
#include <chrono>
#include <cstdio>
#include <random>
#include <string>

namespace vbt {

inline std::string make_uuid_v4() {
    static thread_local std::mt19937_64 rng{[]{
        std::random_device rd;
        // Mix in nanoseconds since epoch — random_device on some platforms
        // is /dev/urandom, on others it's a weak fallback.
        auto ns = std::chrono::high_resolution_clock::now().time_since_epoch().count();
        return (uint64_t)rd() ^ (uint64_t)ns
             ^ ((uint64_t)rd() << 32);
    }()};
    std::array<uint8_t, 16> b{};
    uint64_t a = rng();
    uint64_t c = rng();
    for (int i = 0; i < 8; ++i) { b[i]     = (a >> (8 * i)) & 0xff; }
    for (int i = 0; i < 8; ++i) { b[8 + i] = (c >> (8 * i)) & 0xff; }
    // RFC4122 v4: version nibble = 0100, variant bits = 10xxxxxx
    b[6] = (b[6] & 0x0f) | 0x40;
    b[8] = (b[8] & 0x3f) | 0x80;
    char buf[37];
    std::snprintf(buf, sizeof(buf),
                   "%02x%02x%02x%02x-%02x%02x-%02x%02x-%02x%02x-%02x%02x%02x%02x%02x%02x",
                   b[0],b[1],b[2],b[3], b[4],b[5], b[6],b[7],
                   b[8],b[9], b[10],b[11],b[12],b[13],b[14],b[15]);
    return std::string(buf);
}

} // namespace vbt

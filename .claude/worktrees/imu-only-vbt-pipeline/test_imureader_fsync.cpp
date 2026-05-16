// Compile + run while camera is in master mode:
//   g++ -std=c++17 -Isrc -I_deps/spdlog-src/include test_imureader_fsync.cpp \
//       libvbt_core.a -lpthread -o test_fsync
//   ./test_fsync /dev/ttyUSB0 10
//
// Counts how many parsed IMU samples have fsync_tagged=true. If the bar's
// firmware-side g_fsync_hits keeps incrementing (visible on /dev/ttyUSB1
// console) but this counter stays 0, the production C++ parser is the bug.

#include "sensors/IMUReader.h"
#include <atomic>
#include <chrono>
#include <cstdio>
#include <cstdlib>
#include <thread>

int main(int argc, char** argv) {
    if (argc < 3) { std::fprintf(stderr, "usage: %s <port> <seconds>\n", argv[0]); return 1; }
    const char* port = argv[1];
    int secs = std::atoi(argv[2]);

    vbt::IMUConfig cfg;
    cfg.port      = port;
    cfg.baud_rate = 921600;

    vbt::IMUReader r;
    if (!r.open(cfg)) { std::fprintf(stderr, "open failed\n"); return 1; }

    std::atomic<uint64_t> total{0}, hits{0};
    r.set_callback([&](const vbt::IMUSample& s){
        total++;
        if (s.fsync_tagged) hits++;
    });
    r.start();
    std::this_thread::sleep_for(std::chrono::seconds(secs));
    r.stop();
    r.close();

    std::printf("samples=%lu  fsync_tagged=%lu  ratio=%.3f\n",
                (unsigned long)total.load(), (unsigned long)hits.load(),
                total ? (double)hits/total : 0.0);
    return 0;
}

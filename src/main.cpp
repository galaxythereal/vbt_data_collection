/**
 * @file main.cpp
 * @brief Entry point for VBT Data Collection System.
 */

#include "app/Application.h"
#include <spdlog/spdlog.h>
#include <spdlog/sinks/stdout_color_sinks.h>
#include <spdlog/sinks/basic_file_sink.h>

int main(int argc, char** argv) {
    // Setup logging
    try {
        auto console_sink = std::make_shared<spdlog::sinks::stdout_color_sink_mt>();
        console_sink->set_level(spdlog::level::info);

        auto file_sink = std::make_shared<spdlog::sinks::basic_file_sink_mt>(
            "vbt_data_collection.log", true);
        file_sink->set_level(spdlog::level::debug);

        auto logger = std::make_shared<spdlog::logger>(
            "vbt", spdlog::sinks_init_list{console_sink, file_sink});
        logger->set_level(spdlog::level::debug);
        spdlog::set_default_logger(logger);
        spdlog::set_pattern("[%Y-%m-%d %H:%M:%S.%e] [%^%l%$] [%s:%#] %v");
    } catch (const spdlog::spdlog_ex& ex) {
        fprintf(stderr, "Log init failed: %s\n", ex.what());
        return 1;
    }

    spdlog::info("=== VBT Data Collection System v1.0 ===");
    spdlog::info("PhD-Grade Barbell Velocity & Position Tracking");

    vbt::Application app;

    if (!app.init(argc, argv)) {
        spdlog::critical("Application initialization failed");
        return 1;
    }

    app.run();
    app.shutdown();

    spdlog::info("Application terminated normally");
    return 0;
}

#include "Config.h"
#include <fstream>
#include <spdlog/spdlog.h>

namespace vbt {

// Config loading/saving is handled via nlohmann_json macros in the header.
// This file provides utility functions.

bool load_config(const std::string& path, AppConfig& config) {
    try {
        std::ifstream f(path);
        if (!f.is_open()) {
            spdlog::warn("Config file not found: {}, using defaults", path);
            return false;
        }
        nlohmann::json j;
        f >> j;
        config = j.get<AppConfig>();
        spdlog::info("Config loaded from {}", path);
        return true;
    } catch (const std::exception& e) {
        spdlog::error("Failed to load config: {}", e.what());
        return false;
    }
}

bool save_config(const std::string& path, const AppConfig& config) {
    try {
        nlohmann::json j = config;
        std::ofstream f(path);
        f << j.dump(2);
        spdlog::info("Config saved to {}", path);
        return true;
    } catch (const std::exception& e) {
        spdlog::error("Failed to save config: {}", e.what());
        return false;
    }
}

} // namespace vbt

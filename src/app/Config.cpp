#include "Config.h"
#include "app/Version.h"
#include <fstream>
#include <spdlog/spdlog.h>

namespace vbt {

BuildProvenance BuildProvenance::current() {
    BuildProvenance p;
    p.app_version     = kAppVersion;
    p.git_sha         = kGitSha;
    p.git_branch      = kGitBranch;
    p.git_dirty       = kGitDirty;
    p.build_timestamp = kBuildTimestamp;
    p.build_type      = kBuildType;
    p.compiler        = kCompilerId;
    p.system          = kSystemName;
    p.arch            = kSystemArch;
    return p;
}

std::vector<ExerciseProfile> default_exercise_profiles() {
    std::vector<ExerciseProfile> v;
    auto add = [&](const char* n, const char* dn, float lp, float pv, float pvmax) {
        ExerciseProfile p;
        p.name = n; p.display_name = dn;
        p.lowpass_cutoff_hz = lp;
        p.expected_peak_v_mps = pv;
        p.expected_peak_v_max_mps = pvmax;
        v.push_back(p);
    };
    add("back_squat",        "Back Squat",         10.0f, 1.0f, 2.0f);
    add("front_squat",       "Front Squat",        10.0f, 1.0f, 2.0f);
    add("bench_press",       "Bench Press",         8.0f, 0.6f, 1.2f);
    add("incline_bench_press","Incline Bench Press",8.0f, 0.6f, 1.2f);
    add("close_grip_bench_press","Close-Grip Bench",8.0f, 0.6f, 1.2f);
    add("deadlift",          "Deadlift",            6.0f, 0.8f, 1.5f);
    add("romanian_deadlift", "Romanian Deadlift",   6.0f, 0.7f, 1.4f);
    add("sumo_deadlift",     "Sumo Deadlift",       6.0f, 0.7f, 1.4f);
    add("overhead_press",    "Overhead Press",      8.0f, 0.6f, 1.2f);
    add("push_press",        "Push Press",         12.0f, 1.2f, 2.2f);
    add("barbell_row",       "Barbell Row",         8.0f, 1.0f, 2.0f);
    add("pendlay_row",       "Pendlay Row",         8.0f, 1.0f, 2.0f);
    add("barbell_curl",      "Barbell Curl",        8.0f, 0.5f, 1.2f);
    add("hip_thrust",        "Hip Thrust",          8.0f, 0.6f, 1.4f);
    add("clean",             "Clean",              20.0f, 2.0f, 3.5f);
    add("power_clean",       "Power Clean",        20.0f, 2.0f, 3.5f);
    add("snatch",            "Snatch",             25.0f, 2.5f, 4.0f);
    add("other",             "Other",              10.0f, 1.0f, 2.5f);
    return v;
}

const ExerciseProfile* find_exercise_profile(const AppConfig& cfg, const std::string& name) {
    for (const auto& p : cfg.exercise_profiles) {
        if (p.name == name) return &p;
    }
    return nullptr;
}

bool load_config(const std::string& path, AppConfig& config) {
    try {
        std::ifstream f(path);
        if (!f.is_open()) {
            spdlog::warn("Config file '{}' not found — falling back to defaults. "
                         "Will write a fresh one on save.", path);
            config.exercise_profiles = default_exercise_profiles();
            return false;
        }
        nlohmann::json j;
        f >> j;
        // Schema-version migration hook
        int schema = j.value("schema_version", 1);
        if (schema > 2) {
            spdlog::error("Config schema version {} is newer than this build supports (max 2). "
                          "Update vbt_data_collection or use the older config.", schema);
            return false;
        }
        config = j.get<AppConfig>();
        if (config.exercise_profiles.empty()) {
            config.exercise_profiles = default_exercise_profiles();
        }
        spdlog::info("Config loaded from {} (schema v{})", path, schema);
        return true;
    } catch (const std::exception& e) {
        spdlog::error("Failed to load config '{}': {}. Using defaults.", path, e.what());
        config.exercise_profiles = default_exercise_profiles();
        return false;
    }
}

bool save_config(const std::string& path, const AppConfig& config) {
    try {
        AppConfig out = config;
        if (out.exercise_profiles.empty()) {
            out.exercise_profiles = default_exercise_profiles();
        }
        nlohmann::json j = out;
        std::ofstream f(path);
        f << j.dump(2);
        spdlog::info("Config saved to {}", path);
        return true;
    } catch (const std::exception& e) {
        spdlog::error("Failed to save config to '{}': {}", path, e.what());
        return false;
    }
}

} // namespace vbt

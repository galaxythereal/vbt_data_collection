#include "processing/RepAnnotation.h"

#include <algorithm>
#include <string>

namespace vbt {

nlohmann::json RepAnnotation::to_json() const {
    return {{"rep_id", rep_id},
            {"set_id", set_id},
            {"phase_order", phase_order},
            {"t_start", t_start_s > 0.0 ? t_start_s : concentric.t_start_s},
            {"t_end",   t_end_s   > 0.0 ? t_end_s   : rest.t_end_s},
            {"concentric", {{"t_start", concentric.t_start_s}, {"t_end", concentric.t_end_s},
                           {"peak_vel", concentric.peak_velocity_mps}, {"source", concentric.source}}},
            {"top_rest",  {{"t_start", top_rest.t_start_s}, {"t_end", top_rest.t_end_s}}},
            {"bottom_rest", {{"t_start", bottom_rest.t_start_s}, {"t_end", bottom_rest.t_end_s}}},
            {"eccentric", {{"t_start", eccentric.t_start_s}, {"t_end", eccentric.t_end_s},
                          {"source", eccentric.source}}},
            {"rest", {{"t_start", rest.t_start_s}, {"t_end", rest.t_end_s}}},
            {"mean_concentric_velocity", mean_concentric_velocity},
            {"peak_concentric_velocity", peak_concentric_velocity},
            {"rom_m", rom_m}};
}

namespace {
const nlohmann::json* json_object_field(const nlohmann::json& j, const char* key) {
    if (!j.is_object()) return nullptr;
    auto it = j.find(key);
    if (it == j.end() || !it->is_object()) return nullptr;
    return &(*it);
}

const nlohmann::json* json_field(const nlohmann::json& j, const char* key) {
    if (!j.is_object()) return nullptr;
    auto it = j.find(key);
    if (it == j.end() || it->is_null()) return nullptr;
    return &(*it);
}

double json_double(const nlohmann::json& j, const char* key, double fallback) {
    const auto* v = json_field(j, key);
    if (!v) return fallback;
    try {
        if (v->is_number()) return v->get<double>();
        if (v->is_string()) return std::stod(v->get<std::string>());
    } catch (...) {}
    return fallback;
}

float json_float(const nlohmann::json& j, const char* key, float fallback) {
    return (float)json_double(j, key, fallback);
}

int json_int(const nlohmann::json& j, const char* key, int fallback) {
    const auto* v = json_field(j, key);
    if (!v) return fallback;
    try {
        if (v->is_number_integer()) return v->get<int>();
        if (v->is_number()) return (int)v->get<double>();
        if (v->is_string()) return std::stoi(v->get<std::string>());
    } catch (...) {}
    return fallback;
}

std::string json_string(const nlohmann::json& j, const char* key,
                        const std::string& fallback) {
    const auto* v = json_field(j, key);
    if (!v) return fallback;
    try {
        if (v->is_string()) return v->get<std::string>();
    } catch (...) {}
    return fallback;
}
} // namespace

RepAnnotation RepAnnotation::from_json(const nlohmann::json& j) {
    RepAnnotation r;
    r.rep_id = json_int(j, "rep_id", 0);
    // set_id is new in v4 — legacy reps default to set 1.
    r.set_id = json_int(j, "set_id", 1);
    r.phase_order = json_string(j, "phase_order", "concentric_first");

    const auto* concentric = json_object_field(j, "concentric");
    const auto* eccentric = json_object_field(j, "eccentric");
    const auto* rest = json_object_field(j, "rest");
    if (concentric) {
        r.concentric.t_start_s = json_double(*concentric, "t_start", 0.0);
        r.concentric.t_end_s = json_double(*concentric, "t_end", 0.0);
        r.concentric.peak_velocity_mps = json_float(*concentric, "peak_vel", 0.0f);
        r.concentric.source = json_string(*concentric, "source", "auto");
    }
    if (eccentric) {
        r.eccentric.t_start_s = json_double(*eccentric, "t_start", 0.0);
        r.eccentric.t_end_s = json_double(*eccentric, "t_end", 0.0);
        r.eccentric.source = json_string(*eccentric, "source", "auto");
    }
    if (rest) {
        r.rest.t_start_s = json_double(*rest, "t_start", 0.0);
        r.rest.t_end_s = json_double(*rest, "t_end", 0.0);
    }
    // top_rest is optional for backward compat with pre-2026-05-05 sessions.
    // Default to a zero-width segment at concentric.t_end so downstream
    // code never sees an inverted interval.
    const auto* top_rest = json_object_field(j, "top_rest");
    if (!top_rest) top_rest = json_object_field(j, "top_dwell");
    if (top_rest) {
        r.top_rest.t_start_s = json_double(*top_rest, "t_start", r.concentric.t_end_s);
        r.top_rest.t_end_s   = json_double(*top_rest, "t_end",   r.concentric.t_end_s);
    } else {
        r.top_rest.t_start_s = r.concentric.t_end_s;
        r.top_rest.t_end_s   = r.eccentric.t_start_s;
    }
    const auto* bottom_rest = json_object_field(j, "bottom_rest");
    if (!bottom_rest) bottom_rest = json_object_field(j, "bottom_dwell");
    if (bottom_rest) {
        r.bottom_rest.t_start_s = json_double(*bottom_rest, "t_start", r.eccentric.t_end_s);
        r.bottom_rest.t_end_s   = json_double(*bottom_rest, "t_end",   r.concentric.t_start_s);
    } else {
        r.bottom_rest.t_start_s = r.eccentric.t_end_s;
        r.bottom_rest.t_end_s   = r.concentric.t_start_s;
    }
    const double inferred_start = std::min(r.concentric.t_start_s, r.eccentric.t_start_s);
    const double inferred_end = r.rest.t_end_s > 0.0
        ? r.rest.t_end_s
        : std::max({r.concentric.t_end_s, r.eccentric.t_end_s,
                    r.top_rest.t_end_s, r.bottom_rest.t_end_s});
    r.t_start_s = json_double(j, "t_start", inferred_start);
    r.t_end_s   = json_double(j, "t_end", inferred_end);
    r.mean_concentric_velocity = json_float(j, "mean_concentric_velocity", 0.0f);
    r.peak_concentric_velocity = json_float(j, "peak_concentric_velocity", 0.0f);
    r.rom_m = json_float(j, "rom_m", 0.0f);
    return r;
}

} // namespace vbt

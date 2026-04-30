#pragma once

/**
 * @file EventLog.h
 * @brief Append-only timestamped event log written alongside sensor data.
 *
 * Each event is one JSON-line with: unified_time_s, host_time_s, source,
 * level, code, message, payload. Used for:
 *   - operator notes during a session ("subject lost balance")
 *   - automated events (sync rearm fired, IMU dropped packets)
 *   - rep-level meta (manual rep accept/reject, form rating)
 *   - calibration provenance (gyro bias completed, tap-test result)
 *
 * The log is the audit trail that lets a future analyst explain outliers.
 * Append-only — never overwritten — and hashed at session finalize.
 */

#include <string>
#include <fstream>
#include <mutex>
#include <chrono>
#include <nlohmann/json.hpp>

namespace vbt {

class EventLog {
public:
    EventLog() = default;
    ~EventLog();

    /// Open `<dir>/events.jsonl` for append. Creates the directory if needed.
    bool open(const std::string& path);
    void close();
    bool is_open() const { return file_.is_open(); }

    /// Log an event. `payload` may be any JSON value (default {}).
    void log(const std::string& source, const std::string& level,
             const std::string& code, const std::string& message,
             const nlohmann::json& payload = nlohmann::json::object(),
             double unified_time_s = -1.0);

    /// Convenience helpers
    void info   (const std::string& source, const std::string& code, const std::string& msg)
        { log(source, "info",    code, msg); }
    void warn   (const std::string& source, const std::string& code, const std::string& msg)
        { log(source, "warning", code, msg); }
    void error  (const std::string& source, const std::string& code, const std::string& msg)
        { log(source, "error",   code, msg); }
    void op_note(const std::string& msg) { log("operator", "note", "operator_note", msg); }

    /// Compute SHA-256 of the file as written so far. Stored in manifest.
    std::string compute_sha256() const;

    std::string path() const { return path_; }

private:
    std::string   path_;
    std::ofstream file_;
    mutable std::mutex mu_;
};

} // namespace vbt

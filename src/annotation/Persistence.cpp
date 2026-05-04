/**
 * @file Persistence.cpp
 */
#include "annotation/Persistence.h"
#include "core/EventLog.h"
#include <spdlog/spdlog.h>
#include <fstream>
#include <sstream>
#include <chrono>
#include <iomanip>
#include <cstdlib>
#include <cstdio>

namespace fs = std::filesystem;

namespace vbt {

std::string Persistence::last_error_;

namespace {

// SHA-256 implementation. We don't link OpenSSL in this app, so a
// self-contained implementation. Adapted from the public-domain
// reference at https://github.com/B-Con/crypto-algorithms — small,
// portable, single-file. We only use it for manifest checksums on
// save-back; performance isn't critical (KBs, not GBs).

static const uint32_t k_sha256[64] = {
    0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
    0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
    0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
    0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
    0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
    0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
    0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
    0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
};

struct Sha256 {
    uint32_t state[8] = {
        0x6a09e667,0xbb67ae85,0x3c6ef372,0xa54ff53a,
        0x510e527f,0x9b05688c,0x1f83d9ab,0x5be0cd19
    };
    uint64_t bitlen = 0;
    uint8_t  data[64] = {};
    uint32_t datalen = 0;

    static uint32_t rotr(uint32_t x, uint32_t n) { return (x >> n) | (x << (32 - n)); }

    void transform() {
        uint32_t m[64];
        for (uint32_t i = 0, j = 0; i < 16; ++i, j += 4) {
            m[i] = ((uint32_t)data[j]<<24) | ((uint32_t)data[j+1]<<16)
                 | ((uint32_t)data[j+2]<<8) |  (uint32_t)data[j+3];
        }
        for (uint32_t i = 16; i < 64; ++i) {
            uint32_t s0 = rotr(m[i-15],7) ^ rotr(m[i-15],18) ^ (m[i-15]>>3);
            uint32_t s1 = rotr(m[i-2],17) ^ rotr(m[i-2],19) ^ (m[i-2]>>10);
            m[i] = m[i-16] + s0 + m[i-7] + s1;
        }
        uint32_t a=state[0],b=state[1],c=state[2],d=state[3];
        uint32_t e=state[4],f=state[5],g=state[6],h=state[7];
        for (uint32_t i = 0; i < 64; ++i) {
            uint32_t S1 = rotr(e,6) ^ rotr(e,11) ^ rotr(e,25);
            uint32_t ch = (e & f) ^ ((~e) & g);
            uint32_t t1 = h + S1 + ch + k_sha256[i] + m[i];
            uint32_t S0 = rotr(a,2) ^ rotr(a,13) ^ rotr(a,22);
            uint32_t mj = (a & b) ^ (a & c) ^ (b & c);
            uint32_t t2 = S0 + mj;
            h=g; g=f; f=e; e=d+t1;
            d=c; c=b; b=a; a=t1+t2;
        }
        state[0]+=a; state[1]+=b; state[2]+=c; state[3]+=d;
        state[4]+=e; state[5]+=f; state[6]+=g; state[7]+=h;
    }

    void update(const uint8_t* p, size_t len) {
        for (size_t i = 0; i < len; ++i) {
            data[datalen++] = p[i];
            if (datalen == 64) {
                transform();
                bitlen += 512;
                datalen = 0;
            }
        }
    }

    std::string finalize_hex() {
        uint64_t bits = bitlen + (uint64_t)datalen * 8;
        data[datalen++] = 0x80;
        if (datalen > 56) {
            while (datalen < 64) data[datalen++] = 0;
            transform();
            datalen = 0;
        }
        while (datalen < 56) data[datalen++] = 0;
        for (int i = 7; i >= 0; --i) data[datalen++] = (uint8_t)(bits >> (i*8));
        transform();
        char hex[65];
        for (int i = 0; i < 8; ++i) {
            std::snprintf(hex + i*8, 9, "%08x", state[i]);
        }
        hex[64] = 0;
        return std::string(hex);
    }
};

std::string iso_now_utc() {
    auto now = std::chrono::system_clock::now();
    auto t   = std::chrono::system_clock::to_time_t(now);
    std::ostringstream ss;
    ss << std::put_time(std::gmtime(&t), "%Y-%m-%dT%H:%M:%SZ");
    return ss.str();
}

std::string operator_id() {
    if (const char* u = std::getenv("USER")) return u;
    if (const char* u = std::getenv("USERNAME")) return u;
    return "unknown";
}

} // namespace

std::string Persistence::sha256_of_file_(const fs::path& p) {
    std::ifstream f(p, std::ios::binary);
    if (!f.is_open()) return {};
    Sha256 h;
    char buf[8192];
    while (f) {
        f.read(buf, sizeof(buf));
        std::streamsize n = f.gcount();
        if (n > 0) h.update(reinterpret_cast<uint8_t*>(buf), (size_t)n);
    }
    return h.finalize_hex();
}

bool Persistence::atomic_write_(const fs::path& path, const std::string& content) {
    fs::path tmp = path;
    tmp += ".tmp";
    {
        std::ofstream f(tmp, std::ios::binary);
        if (!f.is_open()) {
            last_error_ = "Cannot open " + tmp.string() + " for writing";
            return false;
        }
        f.write(content.data(), (std::streamsize)content.size());
        f.flush();
        if (!f.good()) {
            last_error_ = "Failed to write " + tmp.string();
            return false;
        }
    }
    std::error_code ec;
    fs::rename(tmp, path, ec);
    if (ec) {
        last_error_ = "Atomic rename failed: " + ec.message();
        return false;
    }
    return true;
}

void Persistence::update_manifest_entry_(SessionData& session,
                                         const std::string& relpath) {
    auto& root_manifest = const_cast<nlohmann::json&>(session.manifest());
    if (!root_manifest.contains("files") || !root_manifest["files"].is_array()) {
        root_manifest["files"] = nlohmann::json::array();
    }
    fs::path full = session.path() / relpath;
    if (!fs::exists(full)) return;
    nlohmann::json entry;
    entry["path"] = relpath;
    entry["bytes"] = (uint64_t)fs::file_size(full);
    entry["sha256"] = sha256_of_file_(full);
    bool found = false;
    for (auto& f : root_manifest["files"]) {
        if (f.value("path", std::string{}) == relpath) {
            f = entry;
            found = true;
            break;
        }
    }
    if (!found) root_manifest["files"].push_back(entry);
}

bool Persistence::append_event_(const fs::path& session_dir,
                                const std::string& level,
                                const std::string& code,
                                const std::string& msg,
                                const nlohmann::json& payload) {
    fs::path p = session_dir / "events.jsonl";
    std::ofstream f(p, std::ios::app);
    if (!f.is_open()) return false;
    nlohmann::json row = {
        {"wallclock", iso_now_utc()},
        {"source", "annotation_studio"},
        {"level",  level},
        {"code",   code},
        {"msg",    msg},
        {"operator", operator_id()},
        {"data",   payload},
    };
    f << row.dump() << "\n";
    return f.good();
}

bool Persistence::save_reps(SessionData& session, const SaveOptions& opt) {
    if (!session.is_loaded()) {
        last_error_ = "No session loaded";
        return false;
    }
    if (opt.recompute_metrics) session.recompute_all_rep_metrics();

    nlohmann::json arr = nlohmann::json::array();
    int next_id = 1;
    for (auto& r : session.mutable_reps()) {
        if (r.rep_id <= 0) r.rep_id = next_id++;
        else next_id = std::max(next_id, r.rep_id + 1);
        arr.push_back(r.to_json());
    }
    fs::path p = session.path() / "annotations" / "rep_segments.json";
    fs::create_directories(p.parent_path());
    if (!atomic_write_(p, arr.dump(2))) return false;

    update_manifest_entry_(session, "annotations/rep_segments.json");
    fs::path mp = session.path() / "manifest.json";
    if (!atomic_write_(mp, session.manifest().dump(2))) return false;

    if (opt.append_audit) {
        nlohmann::json payload = {
            {"rep_count", session.reps().size()},
            {"recompute_metrics", opt.recompute_metrics},
        };
        append_event_(session.path(), "info", "annotation.reps_saved",
                      opt.note.empty() ? "Rep edits committed" : opt.note,
                      payload);
    }
    spdlog::info("AnnotationStudio: saved {} reps to {}", session.reps().size(), p.string());
    return true;
}

bool Persistence::save_metadata(SessionData& session, const SaveOptions& opt) {
    if (!session.is_loaded()) {
        last_error_ = "No session loaded";
        return false;
    }
    nlohmann::json j = session.info();
    fs::path p = session.path() / "metadata.json";
    if (!atomic_write_(p, j.dump(2))) return false;
    update_manifest_entry_(session, "metadata.json");
    fs::path mp = session.path() / "manifest.json";
    if (!atomic_write_(mp, session.manifest().dump(2))) return false;
    if (opt.append_audit) {
        nlohmann::json payload = {
            {"subject_id", session.info().subject_id},
            {"exercise",   session.info().exercise},
            {"total_weight_kg", session.info().total_weight_kg},
            {"rpe", session.info().rpe},
            {"notes_len", (int)session.info().notes.size()},
        };
        append_event_(session.path(), "info", "annotation.meta_saved",
                      opt.note.empty() ? "Metadata edited" : opt.note,
                      payload);
    }
    spdlog::info("AnnotationStudio: saved metadata to {}", p.string());
    return true;
}

bool Persistence::save_all(SessionData& session, const SaveOptions& opt) {
    bool ok = true;
    if (session.reps_dirty())  ok &= save_reps(session, opt);
    if (session.meta_dirty())  ok &= save_metadata(session, opt);
    if (ok) session.clear_dirty();
    return ok;
}

} // namespace vbt

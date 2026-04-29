#include "core/EventLog.h"
#include <filesystem>
#include <fstream>
#include <sstream>
#include <iomanip>
#include <chrono>
#include <cstdint>
#include <cstring>
#include <spdlog/spdlog.h>

namespace fs = std::filesystem;

namespace vbt {

namespace {
// Tiny self-contained SHA-256. Sufficient for manifest checksums of small
// JSONL files; not a constant-time implementation.
class Sha256 {
public:
    Sha256() { reset(); }
    void reset() {
        s_[0]=0x6a09e667; s_[1]=0xbb67ae85; s_[2]=0x3c6ef372; s_[3]=0xa54ff53a;
        s_[4]=0x510e527f; s_[5]=0x9b05688c; s_[6]=0x1f83d9ab; s_[7]=0x5be0cd19;
        len_=0; buflen_=0;
    }
    void update(const uint8_t* d, size_t n) {
        for (size_t i=0;i<n;i++) {
            buf_[buflen_++] = d[i];
            if (buflen_==64) { compress(buf_); buflen_=0; }
            len_++;
        }
    }
    std::string hex_digest() {
        uint64_t bits = len_ * 8;
        uint8_t pad[64] = {0};
        pad[0] = 0x80;
        if (buflen_ < 56) {
            update(pad, 56 - buflen_);
        } else {
            update(pad, 64 - buflen_);
            uint8_t z[56] = {0}; update(z, 56);
        }
        uint8_t blen[8];
        for (int i=0;i<8;i++) blen[7-i] = (uint8_t)(bits >> (8*i));
        update(blen, 8);
        char hex[65];
        for (int i=0;i<8;i++)
            std::snprintf(hex + i*8, 9, "%08x", s_[i]);
        return std::string(hex, 64);
    }
private:
    static uint32_t rot(uint32_t x, int n){ return (x>>n)|(x<<(32-n)); }
    void compress(const uint8_t* b) {
        static const uint32_t K[64] = {
            0x428a2f98,0x71374491,0xb5c0fbcf,0xe9b5dba5,0x3956c25b,0x59f111f1,0x923f82a4,0xab1c5ed5,
            0xd807aa98,0x12835b01,0x243185be,0x550c7dc3,0x72be5d74,0x80deb1fe,0x9bdc06a7,0xc19bf174,
            0xe49b69c1,0xefbe4786,0x0fc19dc6,0x240ca1cc,0x2de92c6f,0x4a7484aa,0x5cb0a9dc,0x76f988da,
            0x983e5152,0xa831c66d,0xb00327c8,0xbf597fc7,0xc6e00bf3,0xd5a79147,0x06ca6351,0x14292967,
            0x27b70a85,0x2e1b2138,0x4d2c6dfc,0x53380d13,0x650a7354,0x766a0abb,0x81c2c92e,0x92722c85,
            0xa2bfe8a1,0xa81a664b,0xc24b8b70,0xc76c51a3,0xd192e819,0xd6990624,0xf40e3585,0x106aa070,
            0x19a4c116,0x1e376c08,0x2748774c,0x34b0bcb5,0x391c0cb3,0x4ed8aa4a,0x5b9cca4f,0x682e6ff3,
            0x748f82ee,0x78a5636f,0x84c87814,0x8cc70208,0x90befffa,0xa4506ceb,0xbef9a3f7,0xc67178f2
        };
        uint32_t w[64];
        for (int i=0;i<16;i++)
            w[i] = (uint32_t(b[i*4])<<24) | (uint32_t(b[i*4+1])<<16) |
                   (uint32_t(b[i*4+2])<<8)  |  uint32_t(b[i*4+3]);
        for (int i=16;i<64;i++) {
            uint32_t s0 = rot(w[i-15],7) ^ rot(w[i-15],18) ^ (w[i-15]>>3);
            uint32_t s1 = rot(w[i-2],17) ^ rot(w[i-2],19) ^ (w[i-2]>>10);
            w[i] = w[i-16] + s0 + w[i-7] + s1;
        }
        uint32_t a=s_[0],b2=s_[1],c=s_[2],d=s_[3],e=s_[4],f=s_[5],g=s_[6],h=s_[7];
        for (int i=0;i<64;i++) {
            uint32_t S1 = rot(e,6)^rot(e,11)^rot(e,25);
            uint32_t ch = (e&f)^((~e)&g);
            uint32_t t1 = h + S1 + ch + K[i] + w[i];
            uint32_t S0 = rot(a,2)^rot(a,13)^rot(a,22);
            uint32_t mj = (a&b2)^(a&c)^(b2&c);
            uint32_t t2 = S0 + mj;
            h=g; g=f; f=e; e=d+t1; d=c; c=b2; b2=a; a=t1+t2;
        }
        s_[0]+=a; s_[1]+=b2; s_[2]+=c; s_[3]+=d;
        s_[4]+=e; s_[5]+=f; s_[6]+=g; s_[7]+=h;
    }
    uint32_t s_[8];
    uint64_t len_;
    uint8_t  buf_[64];
    size_t   buflen_;
};

std::string iso_now() {
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    auto us = std::chrono::duration_cast<std::chrono::microseconds>(now.time_since_epoch()).count() % 1000000;
    std::ostringstream oss;
    oss << std::put_time(std::gmtime(&t), "%Y-%m-%dT%H:%M:%S")
        << '.' << std::setw(6) << std::setfill('0') << us << "Z";
    return oss.str();
}
double host_now_s() {
    using namespace std::chrono;
    return duration<double>(system_clock::now().time_since_epoch()).count();
}
} // namespace

EventLog::~EventLog() { close(); }

bool EventLog::open(const std::string& path) {
    std::lock_guard<std::mutex> lock(mu_);
    fs::create_directories(fs::path(path).parent_path());
    file_.open(path, std::ios::out | std::ios::app);
    if (!file_.is_open()) {
        spdlog::error("EventLog: failed to open {} (check permissions and parent dir)", path);
        return false;
    }
    path_ = path;
    return true;
}

void EventLog::close() {
    std::lock_guard<std::mutex> lock(mu_);
    if (file_.is_open()) file_.close();
}

void EventLog::log(const std::string& source, const std::string& level,
                   const std::string& code, const std::string& message,
                   const nlohmann::json& payload, double unified_time_s)
{
    nlohmann::json j;
    j["wallclock"] = iso_now();
    j["host_s"]    = host_now_s();
    if (unified_time_s >= 0) j["unified_s"] = unified_time_s;
    j["source"]    = source;
    j["level"]     = level;
    j["code"]      = code;
    j["msg"]       = message;
    if (!payload.empty()) j["payload"] = payload;

    std::string line = j.dump() + "\n";
    {
        std::lock_guard<std::mutex> lock(mu_);
        if (file_.is_open()) {
            file_ << line;
            file_.flush();
        }
    }
}

std::string EventLog::compute_sha256() const {
    std::lock_guard<std::mutex> lock(mu_);
    std::ifstream in(path_, std::ios::binary);
    if (!in) return "";
    Sha256 h;
    char buf[8192];
    while (in) {
        in.read(buf, sizeof(buf));
        std::streamsize n = in.gcount();
        if (n > 0) h.update(reinterpret_cast<const uint8_t*>(buf), (size_t)n);
    }
    return h.hex_digest();
}

} // namespace vbt

#include "offline/AuditImage.h"

#include <algorithm>
#include <cmath>
#include <cstdio>

#include <opencv2/imgcodecs.hpp>
#include <opencv2/imgproc.hpp>

namespace fs = std::filesystem;

namespace vbt::offline {
namespace {

// Same colours the review window uses, so the image and the screen read as one thing.
const cv::Scalar kBg   (24, 20, 18);
const cv::Scalar kInk  (238, 238, 235);
const cv::Scalar kGrid (58, 52, 48);
const cv::Scalar kCon   (79, 158, 46);     // BGR: concentric, sent up
const cv::Scalar kEcc   (91, 73, 209);     // BGR: eccentric, brought down
const cv::Scalar kLine  (184, 112, 31);    // BGR: the three lines
const cv::Scalar kGrey  (122, 116, 114);
const cv::Scalar kAmber (52, 168, 230);

struct Axes {
    int x0, y0, w, h;          // pixel box
    double t0, t1, v0, v1;     // data range
    int px(double t) const {
        const double f = (t1 > t0) ? (t - t0) / (t1 - t0) : 0.0;
        return x0 + (int)std::lround(f * w);
    }
    int py(double v) const {
        const double f = (v1 > v0) ? (v - v0) / (v1 - v0) : 0.5;
        return y0 + h - (int)std::lround(f * h);
    }
};

void frame_axes(cv::Mat& m, const Axes& a, const std::string& ylabel) {
    cv::rectangle(m, cv::Rect(a.x0, a.y0, a.w, a.h), kGrid, 1, cv::LINE_AA);
    for (int i = 1; i < 4; ++i) {
        const int y = a.y0 + a.h * i / 4;
        cv::line(m, {a.x0, y}, {a.x0 + a.w, y}, kGrid, 1, cv::LINE_AA);
    }
    cv::putText(m, ylabel, {a.x0 + 6, a.y0 + 18}, cv::FONT_HERSHEY_SIMPLEX, 0.42,
                kGrey, 1, cv::LINE_AA);
    char buf[48];
    std::snprintf(buf, sizeof buf, "%.2f", a.v1);
    cv::putText(m, buf, {a.x0 - 52, a.y0 + 12}, cv::FONT_HERSHEY_SIMPLEX, 0.36, kGrey, 1, cv::LINE_AA);
    std::snprintf(buf, sizeof buf, "%.2f", a.v0);
    cv::putText(m, buf, {a.x0 - 52, a.y0 + a.h}, cv::FONT_HERSHEY_SIMPLEX, 0.36, kGrey, 1, cv::LINE_AA);
}

void plot(cv::Mat& m, const Axes& a, const std::vector<double>& t,
          const std::vector<double>& v, const cv::Scalar& c, int thick) {
    cv::Point prev(-1, -1);
    for (size_t i = 0; i < v.size(); ++i) {
        const cv::Point p(a.px(t[i]), a.py(v[i]));
        if (prev.x >= 0 && (p.x != prev.x || p.y != prev.y))
            cv::line(m, prev, p, c, thick, cv::LINE_AA);
        prev = p;
    }
}

void band(cv::Mat& m, const Axes& a, double t_a, double t_b, const cv::Scalar& c, double alpha) {
    const int xa = a.px(t_a), xb = a.px(t_b);
    if (xb <= xa) return;
    cv::Rect roi(xa, a.y0 + 1, std::max(1, xb - xa), a.h - 1);
    roi &= cv::Rect(0, 0, m.cols, m.rows);
    if (roi.width <= 0 || roi.height <= 0) return;
    cv::Mat over(roi.size(), m.type(), c);
    cv::addWeighted(over, alpha, m(roi), 1.0 - alpha, 0.0, m(roi));
}

void minmax(const std::vector<double>& v, double& lo, double& hi) {
    lo = 1e30; hi = -1e30;
    for (double x : v) { if (x < lo) lo = x; if (x > hi) hi = x; }
    if (!(hi > lo)) { lo -= 1; hi += 1; }
    const double pad = 0.06 * (hi - lo);
    lo -= pad; hi += pad;
}

} // namespace

bool write_audit_image(const PipelineResult& r, const fs::path& out_png, std::string& err) {
    const auto& an = r.annotation;
    const size_t n = r.track.size();
    if (n < 2) { err = "nothing to draw"; return false; }

    constexpr int W = 2200, H = 1250, L = 90, R_ = 30;
    cv::Mat m(H, W, CV_8UC3, kBg);

    std::vector<double> t(n);
    for (size_t i = 0; i < n; ++i) t[i] = (double)i / 90.0;
    const double t0 = 0.0, t1 = t.back();

    double plo, phi, vlo, vhi, alo, ahi;
    minmax(r.track.pos, plo, phi);
    minmax(r.track.vel, vlo, vhi);
    minmax(r.track.acc, alo, ahi);

    Axes ap{L, 90,  W - L - R_, 560, t0, t1, plo, phi};
    Axes av{L, 690, W - L - R_, 250, t0, t1, vlo, vhi};
    Axes aa{L, 970, W - L - R_, 230, t0, t1, alo, ahi};

    // ---- title -----------------------------------------------------------------------
    {
        char buf[512];
        int kept = 0; for (const auto& x : an.reps) if (!x.rejected) ++kept;
        std::snprintf(buf, sizeof buf,
                      "%s   [%s]   %d reps, %d kept   |   camera tilt %.2f deg   |   "
                      "%ld frames the marker was not seen on   |   %s",
                      r.session_id.c_str(), r.exercise.c_str(), (int)an.reps.size(), kept,
                      r.frame.tilt_deg, r.lost_frames,
                      an.down_first ? "down then up" : "up then down");
        cv::putText(m, buf, {L, 46}, cv::FONT_HERSHEY_SIMPLEX, 0.62, kInk, 1, cv::LINE_AA);
        cv::putText(m, "a rep is one round trip across the MIDDLE line; it begins where it "
                       "set off and ends where the bar stopped being brought back",
                    {L, 70}, cv::FONT_HERSHEY_SIMPLEX, 0.42, kGrey, 1, cv::LINE_AA);
    }

    // ---- frames the marker was not seen on, on every panel ----------------------------
    for (const Axes* a : {&ap, &av, &aa}) {
        size_t i = 0;
        while (i < n) {
            if (r.track.measured[i]) { ++i; continue; }
            size_t j = i;
            while (j < n && !r.track.measured[j]) ++j;
            band(m, *a, t[i], t[j - 1], kAmber, 0.30);
            i = j;
        }
    }

    // ---- height ----------------------------------------------------------------------
    frame_axes(m, ap, "height (m)");
    for (const auto& rep : an.reps) {
        const cv::Scalar cc = rep.rejected ? kGrey : kCon;
        const cv::Scalar ce = rep.rejected ? kGrey : kEcc;
        if (rep.concentric_end_frame > rep.concentric_start_frame)
            band(m, ap, t[rep.concentric_start_frame], t[rep.concentric_end_frame], cc,
                 rep.rejected ? 0.12 : 0.22);
        if (rep.eccentric_end_frame > rep.eccentric_start_frame)
            band(m, ap, t[rep.eccentric_start_frame], t[rep.eccentric_end_frame], ce,
                 rep.rejected ? 0.12 : 0.22);
    }
    if (an.lines.valid) {
        for (auto [lv, thick] : {std::pair<double,int>{an.lines.low, 1},
                                 {an.lines.high, 1}, {an.lines.mid, 2}}) {
            const int y = ap.py(lv);
            if (y > ap.y0 && y < ap.y0 + ap.h)
                cv::line(m, {ap.x0, y}, {ap.x0 + ap.w, y}, kLine, thick, cv::LINE_AA);
        }
    }
    plot(m, ap, t, r.track.pos, kInk, 2);
    for (const auto& rep : an.reps) {
        const int64_t a = std::min(rep.concentric_start_frame, rep.eccentric_start_frame);
        const int64_t b = std::max(rep.concentric_end_frame,   rep.eccentric_end_frame);
        if (a < 0 || (size_t)b >= n) continue;
        const cv::Scalar c = rep.rejected ? kGrey : kInk;
        cv::circle(m, {ap.px(t[a]), ap.py(r.track.pos[a])}, 4, c, -1, cv::LINE_AA);
        cv::rectangle(m, cv::Rect(ap.px(t[b]) - 3, ap.py(r.track.pos[b]) - 3, 7, 7), c, -1, cv::LINE_AA);
        char id[16]; std::snprintf(id, sizeof id, "%d", rep.rep_id);
        cv::putText(m, id, {ap.px(t[a]) - 4, ap.py(r.track.pos[a]) - 10},
                    cv::FONT_HERSHEY_SIMPLEX, 0.36, c, 1, cv::LINE_AA);
    }

    // ---- speed, with the boundaries ---------------------------------------------------
    frame_axes(m, av, "speed (m/s)");
    { const int y = av.py(0.0); cv::line(m, {av.x0, y}, {av.x0 + av.w, y}, kGrid, 1, cv::LINE_AA); }
    plot(m, av, t, r.track.vel, kInk, 1);
    for (const auto& b : an.boundaries) {
        if (b.frame < 0 || (size_t)b.frame >= n) continue;
        const cv::Point p(av.px(t[b.frame]), av.py(r.track.vel[b.frame]));
        cv::circle(m, p, 4, kLine, b.arrived ? -1 : 1, cv::LINE_AA);
    }

    // ---- the push, with the smoother's own uncertainty --------------------------------
    frame_axes(m, aa, "push (m/s^2)");
    for (size_t i = 0; i + 1 < n; ++i) {
        const int x = aa.px(t[i]);
        const int yl = aa.py(-2.0 * r.track.acc_sd[i]);
        const int yh = aa.py(+2.0 * r.track.acc_sd[i]);
        cv::line(m, {x, yh}, {x, yl}, kGrid, 1);
    }
    { const int y = aa.py(0.0); cv::line(m, {aa.x0, y}, {aa.x0 + aa.w, y}, kGrid, 1, cv::LINE_AA); }
    plot(m, aa, t, r.track.acc, kInk, 1);

    // ---- time axis --------------------------------------------------------------------
    for (int s = 0; s <= (int)t1; s += std::max(1, (int)(t1 / 20))) {
        const int x = aa.px((double)s);
        cv::line(m, {x, aa.y0 + aa.h}, {x, aa.y0 + aa.h + 5}, kGrid, 1);
        char b[16]; std::snprintf(b, sizeof b, "%d", s);
        cv::putText(m, b, {x - 6, aa.y0 + aa.h + 22}, cv::FONT_HERSHEY_SIMPLEX, 0.36,
                    kGrey, 1, cv::LINE_AA);
    }
    cv::putText(m, "time (s)", {aa.x0 + aa.w / 2 - 28, aa.y0 + aa.h + 44},
                cv::FONT_HERSHEY_SIMPLEX, 0.40, kGrey, 1, cv::LINE_AA);

    // ---- legend -----------------------------------------------------------------------
    {
        int x = L, y = H - 16;
        auto key = [&](const cv::Scalar& c, const char* label, bool filled) {
            cv::circle(m, {x + 5, y - 4}, 4, c, filled ? -1 : 1, cv::LINE_AA);
            cv::putText(m, label, {x + 16, y}, cv::FONT_HERSHEY_SIMPLEX, 0.38, kGrey, 1, cv::LINE_AA);
            x += 26 + (int)std::string(label).size() * 7;
        };
        key(kCon, "concentric", true); key(kEcc, "eccentric", true);
        key(kAmber, "marker not seen", true);
        key(kLine, "boundary: bar arrived", true);
        key(kLine, "boundary: bar was let go", false);
        key(kGrey, "refused by the reviewer", true);
    }

    std::error_code ec;
    fs::create_directories(out_png.parent_path(), ec);
    if (!cv::imwrite(out_png.string(), m)) { err = "cannot write " + out_png.string(); return false; }
    return true;
}

} // namespace vbt::offline

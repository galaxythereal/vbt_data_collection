#include "offline/OfflineAnnotator.h"

#include <algorithm>
#include <cmath>

namespace vbt::offline {
namespace {

/// Same convention as numpy's median: for an even count, the mean of the two middle
/// values. The reference implementation uses it, and the lines must match exactly.
double median(std::vector<double> v) {
    if (v.empty()) return 0.0;
    std::sort(v.begin(), v.end());
    const size_t n = v.size();
    return (n % 2) ? v[n / 2] : 0.5 * (v[n / 2 - 1] + v[n / 2]);
}

/// Every frame where the track passes through a level, and which way. RULE 2.
struct Crossing { int64_t frame; int dir; };

std::vector<Crossing> crossings(const std::vector<double>& p, double level) {
    std::vector<Crossing> out;
    for (size_t i = 1; i < p.size(); ++i) {
        if (p[i - 1] < level && level <= p[i])      out.push_back({(int64_t)i, +1});
        else if (p[i - 1] > level && level >= p[i]) out.push_back({(int64_t)i, -1});
    }
    return out;
}

/// A run of frames where the bar is clearly travelling one way. RULE 6.
struct Run { int64_t a, b; int dir; };

std::vector<Run> stretches(const Track& t, double k) {
    const size_t n = t.size();
    std::vector<int> s(n, 0);
    for (size_t i = 0; i < n; ++i) {
        if (t.vel[i] >  k * t.vel_sd[i]) s[i] =  1;
        else if (t.vel[i] < -k * t.vel_sd[i]) s[i] = -1;
    }
    std::vector<Run> out;
    size_t i = 0;
    while (i < n) {
        if (s[i] == 0) { ++i; continue; }
        size_t j = i;
        while (j < n && s[j] == s[i]) ++j;
        out.push_back({(int64_t)i, (int64_t)(j - 1), s[i]});
        i = j;
    }
    return out;
}

/// RULE 6. The run that carries the bar across the middle line on its way out.
bool outbound_run(const std::vector<Run>& runs, int64_t opening, int out_dir, Run& found) {
    bool have = false;
    for (const auto& r : runs) {
        if (r.dir != out_dir) continue;
        if (r.a <= opening && opening <= r.b) { found = r; return true; }
        if (r.b < opening) { found = r; have = true; }
    }
    return have;
}

/// RULE 6. From the closing crossing on, the boundary where the bar has come back
/// nearest to the height it set off from. The look stops as soon as it has come back,
/// and as soon as it stops coming back.
bool rep_end(const std::vector<double>& p, const std::vector<Boundary>& bnd, int out_dir,
             int64_t closing, int64_t ceiling, int64_t start, int64_t& out) {
    std::vector<int64_t> cand;
    for (const auto& b : bnd)
        if (b.frame >= closing && b.frame < ceiling) cand.push_back(b.frame);
    if (cand.empty()) {
        for (const auto& b : bnd)
            if (b.frame >= closing) { cand.push_back(b.frame); break; }
    }
    if (cand.empty()) return false;

    std::vector<int64_t> look;
    for (int64_t f : cand) {
        if (!look.empty() && (p[f] - p[look.back()]) * out_dir > 0) break;  // turned back again
        look.push_back(f);
        if ((p[f] - p[start]) * out_dir <= 0) break;                        // it has come back
    }
    out = *std::min_element(look.begin(), look.end(), [&](int64_t x, int64_t y) {
        return std::fabs(p[x] - p[start]) < std::fabs(p[y] - p[start]);
    });
    return true;
}

/// RULE 6. A rep is a round trip, so it set off from the height it comes back to. Of the
/// beginning of the outbound run and any boundary inside that run, that is the one.
int64_t rep_start(const std::vector<double>& p, const std::vector<Boundary>& bnd,
                  int64_t opening, bool have_run, const Run& run, int64_t end) {
    if (!have_run) return opening;
    std::vector<int64_t> cand{run.a};
    const int64_t hi = std::min<int64_t>(opening, run.b);
    for (const auto& b : bnd)
        if (b.frame > run.a && b.frame <= hi) cand.push_back(b.frame);
    return *std::min_element(cand.begin(), cand.end(), [&](int64_t x, int64_t y) {
        return std::fabs(p[x] - p[end]) < std::fabs(p[y] - p[end]);
    });
}

/// RULE 6. The far end of the round trip.
int64_t turnaround(const std::vector<double>& p, int out_dir, int64_t start, int64_t end) {
    int64_t best = start;
    for (int64_t i = start; i <= end; ++i)
        if (out_dir > 0 ? (p[i] > p[best]) : (p[i] < p[best])) best = i;
    return best;
}

} // namespace

Track OfflineAnnotator::track_from(const std::vector<Smoothed3>& s) {
    Track t;
    const size_t n = s.size();
    t.pos.reserve(n); t.vel.reserve(n); t.acc.reserve(n);
    t.vel_sd.reserve(n); t.acc_sd.reserve(n); t.measured.reserve(n);
    for (const auto& f : s) {
        // vertical = -y. The camera's +y is DOWN (corr(y_m, pixel_v) = +0.9998 on 84/84).
        t.pos.push_back(-f.pos[1]);
        t.vel.push_back(-f.vel[1]);
        t.acc.push_back(-f.acc[1]);
        t.vel_sd.push_back(f.vel_sd[1]);
        t.acc_sd.push_back(f.acc_sd[1]);
        t.measured.push_back(f.measured ? 1 : 0);
    }
    return t;
}

Lines OfflineAnnotator::lines_from(const Track& t, const std::vector<rt::RtRep>& online) {
    const int64_t n = (int64_t)t.size();
    auto collect = [&](bool skip_ends, std::vector<double>& bots, std::vector<double>& tops) {
        bots.clear(); tops.clear();
        for (size_t i = 0; i < online.size(); ++i) {
            if (skip_ends && (i == 0 || i + 1 == online.size())) continue;
            if (!online[i].confirmed) continue;
            const int64_t ce = online[i].concentric_end_frame;
            const int64_t ee = online[i].eccentric_end_frame;
            if (ce >= 0 && ce < n) tops.push_back(t.pos[ce]);
            if (ee >= 0 && ee < n) bots.push_back(t.pos[ee]);
        }
    };
    std::vector<double> bots, tops;
    collect(true, bots, tops);
    if (bots.size() < 3 || tops.size() < 3) collect(false, bots, tops);
    Lines L;
    if (bots.empty() || tops.empty()) return L;
    L.low   = median(bots);
    L.high  = median(tops);
    L.mid   = 0.5 * (L.low + L.high);
    L.valid = true;
    return L;
}

std::vector<FrameState> OfflineAnnotator::states(const Track& t) const {
    const size_t n = t.size();
    std::vector<FrameState> st(n, FrameState::Coasting);
    for (size_t i = 0; i < n; ++i) {
        const bool travelling = std::fabs(t.vel[i]) > cfg_.k * t.vel_sd[i];
        if (!travelling) { st[i] = FrameState::Resting; continue; }
        if (std::fabs(t.acc[i]) <= cfg_.k * t.acc_sd[i]) continue;   // push not definite
        const bool agree = (t.vel[i] > 0) == (t.acc[i] > 0);
        st[i] = agree ? FrameState::Driven : FrameState::HeldBack;
    }
    return st;
}

std::vector<Boundary> OfflineAnnotator::boundaries(const std::vector<FrameState>& st,
                                                   const Track&) const {
    std::vector<Boundary> out;
    bool driven = false;   // the bar was sent on its way inside the trip under way
    bool held   = false;   // and is now being brought back
    for (size_t j = 0; j < st.size(); ++j) {
        switch (st[j]) {
            case FrameState::Resting:
                if (held) out.push_back({(int64_t)j - 1, true});
                driven = held = false;
                break;
            case FrameState::Driven:
                if (held) out.push_back({(int64_t)j - 1, false});
                driven = true; held = false;
                break;
            case FrameState::HeldBack:
                if (driven) held = true;
                break;
            case FrameState::Coasting:
                break;   // says nothing either way; leaves the trip as it was
        }
    }
    if (held && !st.empty()) out.push_back({(int64_t)st.size() - 1, true});
    return out;
}

Annotation OfflineAnnotator::run(const Track& t,
                                 const std::vector<rt::RtRep>& online,
                                 bool down_first) const {
    Annotation an;
    an.down_first = down_first;
    an.lines      = lines_from(t, online);
    an.states     = states(t);
    an.boundaries = boundaries(an.states, t);
    if (!an.lines.valid || t.size() < 2) return an;

    const int first = down_first ? -1 : +1;   // RULE 2: which way the rep crosses first
    const auto X   = crossings(t.pos, an.lines.mid);

    // RULE 2. A rep is a crossing the first way followed by a crossing back.
    // RULE 4. Crossings of one line alternate, so two of the same phase cannot follow
    //         each other -- it is not possible to express here.
    // RULE 5. An unmatched opening crossing at the end of the session is not a rep and is
    //         not recorded as anything.
    std::vector<std::pair<int64_t, int64_t>> pairs;
    bool open = false; int64_t open_at = -1;
    for (const auto& c : X) {
        if (!open) { if (c.dir == first) { open = true; open_at = c.frame; } }
        else if (c.dir == -first) { pairs.emplace_back(open_at, c.frame); open = false; }
    }

    const auto runs = stretches(t, cfg_.k);
    const int64_t n = (int64_t)t.size();

    for (size_t k = 0; k < pairs.size(); ++k) {
        const int64_t a = pairs[k].first, b = pairs[k].second;
        const int64_t ceiling = (k + 1 < pairs.size()) ? pairs[k + 1].first : n;

        // RULE 6. The end needs a height to come back to and the start needs a height to
        // have set off from; they are the same height, so the outbound run's own
        // beginning opens the question and the answer closes it.
        // A rep the counting found is a rep: where the track offers no boundary, the rep
        // is recorded between its own crossings rather than dropped.
        Run run{}; const bool have_run = outbound_run(runs, a, first, run);
        int64_t f_a = have_run ? run.a : a;
        int64_t r_b = b;
        if (!rep_end(t.pos, an.boundaries, first, b, ceiling, f_a, r_b)) r_b = b;
        f_a = rep_start(t.pos, an.boundaries, a, have_run, run, r_b);
        int64_t again = r_b;
        if (rep_end(t.pos, an.boundaries, first, b, ceiling, f_a, again)) r_b = again;
        const int64_t turn = turnaround(t.pos, first, f_a, r_b);

        OfflineRep r;
        r.rep_id = (int)k + 1;
        if (down_first) {
            r.eccentric_start_frame  = f_a; r.eccentric_end_frame  = turn;
            r.concentric_start_frame = turn; r.concentric_end_frame = r_b;
        } else {
            r.concentric_start_frame = f_a; r.concentric_end_frame = turn;
            r.eccentric_start_frame  = turn; r.eccentric_end_frame  = r_b;
        }
        r.rom_m = std::fabs(t.pos[r.concentric_end_frame] - t.pos[r.concentric_start_frame]);
        double pk = 0.0;
        for (int64_t i = r.concentric_start_frame; i <= r.concentric_end_frame; ++i)
            pk = std::max(pk, std::fabs(t.vel[i]));
        r.peak_velocity = pk;
        int gaps = 0;
        for (int64_t i = f_a; i <= r_b; ++i) if (!t.measured[i]) ++gaps;
        r.gap_frames    = gaps;
        r.started_below = t.pos[f_a] < an.lines.low;
        r.started_above = t.pos[f_a] > an.lines.high;
        an.reps.push_back(r);
    }
    return an;
}

} // namespace vbt::offline

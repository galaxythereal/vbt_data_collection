/**
 * @file velocity_robustness.cpp
 * @brief Does the smoother distort the velocity it is supposed to measure?
 *
 * Velocity is the quantity the product exists to report, so the camera reference for it
 * has to be defensible. The smoother has exactly one number that was not measured from
 * the data: the model stiffness (jerk_psd), set to 1000 by innovation consistency. If
 * peak velocity moved when that number moved, the reference would be an artefact of a
 * choice rather than a measurement.
 *
 * This runs the same sessions through the same smoother at 200, 500, 1000, 2000 and 5000
 * -- a 25-fold range -- keeping the repetition boundaries fixed, and reports how far the
 * peak concentric velocity of each released repetition travels. Repetitions a reviewer
 * refused are excluded; repetitions with missing marker frames are reported separately,
 * because where there is no measurement the model necessarily supplies the motion.
 *
 *   velocity_robustness <session_dir>...
 */
#include <cstdio>
#include <algorithm>
#include <cmath>
#include <vector>
#include "offline/OfflinePipeline.h"
using namespace vbt::offline;

int main(int argc, char** argv) {
    if (argc < 2) { std::fprintf(stderr, "usage: vel_sensitivity <session_dir>...\n"); return 2; }
    const double PSD[] = {200, 500, 1000, 2000, 5000};
    std::printf("%-14s %4s", "session", "rep");
    for (double p : PSD) std::printf("%10.0f", p);
    std::printf("   %9s %9s\n", "spread", "% of peak");

    double worst_rel = 0; long n = 0; double sum_rel = 0;
    double gworst = 0; long gn = 0; double gsum = 0;
    for (int i = 1; i < argc; ++i) {
        SessionPaths paths; paths.dir = argv[i]; paths.id = paths.dir.filename().string();
        OfflinePipeline pipe;
        // the annotation from the shipped settings decides WHICH frames are a rep;
        // only the velocity inside them is recomputed
        auto base = pipe.run(paths);
        if (!base.ok) continue;
        OfflinePipeline::load_review(paths, base.annotation);

        std::vector<std::vector<double>> pk(base.annotation.reps.size());
        for (double p : PSD) {
            RtsSmoother::Config c; c.jerk_psd = p;
            RtsSmoother sm(c);
            auto sm_out = sm.run(base.samples);
            for (size_t k = 0; k < base.annotation.reps.size(); ++k) {
                const auto& r = base.annotation.reps[k];
                double m = 0;
                for (int64_t f = r.concentric_start_frame; f <= r.concentric_end_frame; ++f)
                    if (f >= 0 && (size_t)f < sm_out.size())
                        m = std::max(m, std::fabs(-sm_out[f].vel[1]));
                pk[k].push_back(m);
            }
        }
        for (size_t k = 0; k < pk.size(); ++k) {
            double lo = *std::min_element(pk[k].begin(), pk[k].end());
            double hi = *std::max_element(pk[k].begin(), pk[k].end());
            double mid = pk[k][2];                       // the shipped setting, 1000
            double rel = mid > 0 ? 100.0 * (hi - lo) / mid : 0;
            const auto& rp = base.annotation.reps[k];
            if (rp.rejected) continue;                 // not in the released set
            if (rp.gap_frames > 0) { gsum += rel; ++gn; gworst = std::max(gworst, rel); continue; }
            sum_rel += rel; ++n; worst_rel = std::max(worst_rel, rel);
            if (false) {
                std::printf("%-14s %4zu", paths.id.c_str() + 8, k + 1);
                for (double v : pk[k]) std::printf("%10.4f", v);
                std::printf("   %9.4f %8.2f%%  gap_frames=%d\n", hi - lo, rel, base.annotation.reps[k].gap_frames);
            }
        }
    }
    std::printf("\nHow much peak concentric velocity moves across a 25x range of model\n"
                "stiffness (jerk_psd 200 to 5000), over the RELEASED repetitions only:\n\n");
    std::printf("  marker seen on every frame   %5ld reps   mean %.2f%%   worst %.2f%%\n",
                n, n ? sum_rel / n : 0.0, worst_rel);
    std::printf("  marker missing on some       %5ld reps   mean %.2f%%   worst %.2f%%\n",
                gn, gn ? gsum / gn : 0.0, gworst);
    return 0;
}

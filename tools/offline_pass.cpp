/**
 * @file offline_pass.cpp
 * @brief Run the app's post-session pass from the command line.
 *
 * This is the SAME code the button in the app runs -- OfflinePipeline, nothing
 * reimplemented. It exists for two reasons: to process the sessions that were recorded
 * before the app had the button, and to be checked against scripts/reference/annotate_v2.py,
 * which is kept as an independent implementation of the same rules.
 *
 *   offline_pass [--root DIR] [--quiet] [--export] <session_dir>...
 *
 * Reads and writes datasets/<session>/. camera/ and imu/ are never written.
 */
#include <cstdio>
#include <cstring>
#include <filesystem>
#include <string>
#include <vector>

#include "offline/AuditImage.h"
#include "offline/OfflinePipeline.h"

namespace fs = std::filesystem;
using namespace vbt::offline;

int main(int argc, char** argv) {
    fs::path root = "datasets";
    bool quiet = false;
    bool do_export = false;
    bool do_audit  = false;
    std::vector<fs::path> sessions;

    for (int i = 1; i < argc; ++i) {
        const std::string a = argv[i];
        if (a == "--root" && i + 1 < argc)  root = argv[++i];
        else if (a == "--quiet")            quiet = true;
        else if (a == "--export")           do_export = true;
        else if (a == "--audit")            do_audit = true;
        else if (a.rfind("--", 0) == 0) {
            std::fprintf(stderr, "unknown option %s\n", a.c_str()); return 2;
        } else sessions.push_back(a);
    }
    if (sessions.empty()) {
        // default: every session in the dataset
        if (!fs::exists(root)) {
            std::fprintf(stderr,
                "usage: offline_pass [--root DIR] [--quiet] [--export] [--audit] <session_dir>...\n"
                "  no dataset at %s -- run from the repository root\n", root.string().c_str());
            return 2;
        }
        for (const auto& e : fs::directory_iterator(root))
            if (e.is_directory() && e.path().filename().string().rfind("session_", 0) == 0)
                sessions.push_back(e.path());
        std::sort(sessions.begin(), sessions.end());
    }

    OfflinePipeline pipe;
    int done = 0, failed = 0; long total_reps = 0;
    for (const auto& s : sessions) {
        SessionPaths paths;
        paths.id  = s.filename().string();
        paths.dir = s;

        const auto r = pipe.run(paths);
        if (!r.ok) {
            std::fprintf(stderr, "  %-28s FAILED: %s\n", paths.id.c_str(), r.message.c_str());
            ++failed; continue;
        }
        std::string err;
        if (!pipe.write(r, paths, err)) {
            std::fprintf(stderr, "  %-28s WRITE FAILED: %s\n", paths.id.c_str(), err.c_str());
            ++failed; continue;
        }
        if (do_export) {
            // a reviewer's earlier refusals are honoured; nothing else is filtered
            auto& mut = const_cast<PipelineResult&>(r);
            mut.reviewed = OfflinePipeline::load_review(paths, mut.annotation);
            std::string xerr;
            if (!OfflinePipeline::export_ground_truth(root, r, xerr))
                std::fprintf(stderr, "  %-28s EXPORT FAILED: %s\n", paths.id.c_str(), xerr.c_str());
        }
        if (do_audit) {
            std::string aerr;
            if (!write_audit_image(r, paths.dir / "audit_post_session.png", aerr))
                std::fprintf(stderr, "  %-28s AUDIT FAILED: %s\n", paths.id.c_str(), aerr.c_str());
        }
        total_reps += (long)r.annotation.reps.size();
        ++done;
        if (!quiet)
            std::printf("  %-28s %-13s %3zu reps   tilt %5.2f deg   lost %4ld   nis %.2f\n",
                        paths.id.c_str(), r.exercise.c_str(), r.annotation.reps.size(),
                        r.frame.tilt_deg, r.lost_frames, r.nis[1]);
    }
    std::printf("\n%ld reps over %d sessions -> %s%s\n",
                total_reps, done, root.string().c_str(),
                failed ? ("   (" + std::to_string(failed) + " failed)").c_str() : "");
    return failed ? 1 : 0;
}

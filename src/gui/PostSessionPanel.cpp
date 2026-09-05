#include "gui/PostSessionPanel.h"

#include <algorithm>
#include <cmath>
#include <cstdio>
#include <filesystem>
#include <limits>

#include <imgui.h>
#include <implot.h>

#include "app/Application.h"
#include "offline/AuditImage.h"
#include "rt_annotator/RtAnnotationIO.h"

namespace fs = std::filesystem;
using namespace vbt::offline;

namespace vbt {
namespace {

// Nominal only, for the moments before a session is loaded. Once one is, the rate
// measured from that session's own trigger pulses is used instead -- the camera runs
// at 89.8654 Hz, and assuming 90.000 puts the end of a minute-long set 90 ms out.
constexpr double kNominalFps = 90.0;

// The two phases keep the colours the whole project uses for them.
const ImVec4 kCon   {0.18f, 0.62f, 0.31f, 1.00f};   // concentric: sent up
const ImVec4 kEcc   {0.82f, 0.29f, 0.36f, 1.00f};   // eccentric: brought down
const ImVec4 kLine  {0.12f, 0.44f, 0.72f, 1.00f};   // the three lines
const ImVec4 kRefused{0.45f, 0.45f, 0.48f, 1.00f};

ImU32 shade(const ImVec4& c, float a) { return ImGui::GetColorU32(ImVec4(c.x, c.y, c.z, a)); }

/// The frame the video is showing, drawn on whichever plot is open. Every panel gets it,
/// so the eye can move between height, speed and push without losing the place.
void draw_cursor(double t_s) {
    double x[2] = {t_s, t_s};
    double y[2] = {ImPlot::GetPlotLimits().Y.Min, ImPlot::GetPlotLimits().Y.Max};
    ImPlot::SetNextLineStyle(ImVec4(0.98f, 0.78f, 0.25f, 0.95f), 1.5f);
    ImPlot::PlotLine("##cursor", x, y, 2);
}

} // namespace

double PostSessionPanel::fps() const {
    if (have_result_ && result_ && result_->sync.frame_period > 0)
        return 1.0 / result_->sync.frame_period;
    return kNominalFps;
}

PostSessionPanel::PostSessionPanel(Application& app) : app_(app) {}
PostSessionPanel::~PostSessionPanel() = default;

SessionPaths PostSessionPanel::paths_for(const std::string& id) const {
    SessionPaths p;
    p.id  = id;
    p.dir = fs::path(app_.config().dataset_root) / id;    // datasets/<session>
    return p;
}

void PostSessionPanel::refresh_sessions() {
    sessions_.clear();
    const fs::path root = app_.config().dataset_root;
    std::error_code ec;
    if (!fs::exists(root, ec)) return;
    for (const auto& e : fs::directory_iterator(root, ec)) {
        if (!e.is_directory()) continue;
        const std::string id = e.path().filename().string();
        if (id.rfind("session_", 0) != 0) continue;
        Entry en; en.id = id;
        const auto p = paths_for(id);
        en.annotated = fs::exists(p.offline(), ec);
        en.reviewed  = fs::exists(p.reviewed(), ec);
        sessions_.push_back(en);
    }
    std::sort(sessions_.begin(), sessions_.end(),
              [](const Entry& a, const Entry& b) { return a.id > b.id; });   // newest first
}

void PostSessionPanel::open(const std::string& session_id) {
    is_open_ = true;
    refresh_sessions();
    if (!session_id.empty()) select(session_id);
    else if (selected_.empty() && !sessions_.empty()) select(sessions_.front().id);
}

void PostSessionPanel::on_session_finished(const std::string& session_id) {
    // IN THE GYM the operator needs to know NOW whether the set was captured, while the
    // lifter is still standing there and the set could be repeated. The pass takes about
    // 40 ms, so there is nothing to gain by making them ask for it.
    refresh_sessions();
    selected_ = session_id;
    have_result_ = false; review_dirty_ = false; result_.reset();
    t_.clear(); pos_.clear(); vel_.clear(); acc_.clear(); raw_pos_.clear();
    playing_ = false;
    video_.close(); cursor_frame_ = 0;
    run_pass();
    if (have_result_) generate_audit();
    show_live_ = false;
    is_open_ = true;
}

void PostSessionPanel::select(const std::string& id) {
    if (selected_ == id && have_result_) return;
    selected_    = id;
    have_result_ = false;
    review_dirty_= false;
    result_.reset();
    t_.clear(); pos_.clear(); vel_.clear(); acc_.clear(); raw_pos_.clear();
    playing_ = false;
    video_.close(); cursor_frame_ = 0;
    status_.clear(); status_bad_ = false;
    load_selected();
}

void PostSessionPanel::load_selected() {
    // Reading what is already on disk is not the same as running the pass: the pass is
    // what produces the track the window draws, so a session that has never been through
    // it simply shows the button.
    const auto p = paths_for(selected_);
    std::error_code ec;
    if (!fs::exists(p.offline(), ec)) return;
    run_pass();          // ~40 ms; re-deriving is cheaper than half-loading
}

void PostSessionPanel::load_live_annotation() {
    live_.clear();
    const auto p = paths_for(selected_);
    std::string err;
    // Written at save() time by the acquisition loop, beside the measurement rather than
    // inside it.
    rt::rt_read_file(p.live().string(), live_, err);
}

void PostSessionPanel::run_pass() {
    if (selected_.empty()) return;
    const auto p = paths_for(selected_);

    OfflinePipeline pipe;
    auto r = std::make_unique<PipelineResult>(pipe.run(p));
    if (!r->ok) {
        status_ = "Could not annotate this session: " + r->message;
        status_bad_ = true; have_result_ = false; result_.reset();
        return;
    }
    std::string err;
    if (!pipe.write(*r, p, err)) {
        status_ = "Annotated, but could not write it: " + err;
        status_bad_ = true;
    } else {
        status_ = "Annotated " + std::to_string(r->annotation.reps.size()) + " reps.";
        status_bad_ = false;
    }
    // a reviewer's earlier decisions on this session are not lost by re-running
    bool stale = false;
    r->reviewed = OfflinePipeline::load_review(p, r->annotation, &stale);
    if (stale) {
        // The judgement on file was made against different boundaries. Say so
        // rather than carrying it over as though it still applied.
        status_ = "This session was reviewed against an older annotation. "
                  "Check the reps and save again.";
        status_bad_ = true;
    }

    // drawing buffers
    const size_t n = r->track.size();
    t_.resize(n); pos_.resize(n); vel_.resize(n); acc_.resize(n);
    raw_pos_.assign(n, std::numeric_limits<double>::quiet_NaN());
    for (size_t i = 0; i < n; ++i) {
        t_[i]   = (double)i / fps();
        pos_[i] = r->track.pos[i];
        vel_[i] = r->track.vel[i];
        acc_[i] = r->track.acc[i];
        if (i < r->raw_pos.size()) raw_pos_[i] = r->raw_pos[i];
    }
    load_live_annotation();
    video_.open(p.dir);
    cursor_frame_ = 0;
    x_min_ = 0.0;
    x_max_ = t_.empty() ? 1.0 : t_.back();
    result_      = std::move(r);
    have_result_ = true;
    review_dirty_= false;
    refresh_sessions();
}

void PostSessionPanel::generate_audit() {
    if (!have_result_) return;
    const auto out = paths_for(selected_).dir / "audit_post_session.png";
    std::string err;
    if (!offline::write_audit_image(*result_, out, err)) {
        status_ = "Could not write the audit: " + err; status_bad_ = true; return;
    }
    status_ = "Audit written to " + out.filename().string();
    status_bad_ = false;
    refresh_sessions();
}

void PostSessionPanel::save_review() {
    if (!have_result_) return;
    const auto p = paths_for(selected_);
    std::string err;
    if (!OfflinePipeline::write_review(p, result_->annotation, err)) {
        status_ = "Could not save the review: " + err; status_bad_ = true; return;
    }
    if (!OfflinePipeline::export_ground_truth(app_.config().dataset_root, *result_, err)) {
        status_ = "Review saved, but the ground truth was not updated: " + err;
        status_bad_ = true; return;
    }
    result_->reviewed = true;
    int kept = 0;
    for (const auto& r : result_->annotation.reps) if (!r.rejected) ++kept;
    status_ = "Saved. " + std::to_string(kept) + " of " +
              std::to_string(result_->annotation.reps.size()) + " reps kept as ground truth.";
    status_bad_  = false;
    review_dirty_= false;
    refresh_sessions();
}

// ---------------------------------------------------------------------------- toolbar

void PostSessionPanel::draw_toolbar() {
    const bool has_sel = !selected_.empty();

    ImGui::BeginDisabled(!has_sel);
    ImGui::PushStyleColor(ImGuiCol_Button, ImVec4(0.12f, 0.40f, 0.66f, 1.0f));
    if (ImGui::Button("Run post-session annotation", ImVec2(250, 30))) run_pass();
    ImGui::PopStyleColor();
    ImGui::EndDisabled();

    ImGui::SameLine();
    ImGui::BeginDisabled(!have_result_ || !review_dirty_);
    if (ImGui::Button("Save review", ImVec2(130, 30))) save_review();
    ImGui::EndDisabled();

    ImGui::SameLine();
    ImGui::BeginDisabled(!have_result_);
    if (ImGui::Button("Save & next set", ImVec2(150, 30))) { save_review(); close(); }
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered())
        ImGui::SetTooltip("Accept what is on screen, write the ground truth, and go back\n"
                          "to the main screen ready to record again");

    ImGui::SameLine();
    ImGui::BeginDisabled(!have_result_);
    if (ImGui::Button("Generate audit", ImVec2(150, 30))) generate_audit();
    ImGui::EndDisabled();
    if (ImGui::IsItemHovered())
        ImGui::SetTooltip("Draw this session's audit to audit_post_session.png, at a fixed\n"
                          "size from the data alone, so it does not depend on the window");

    ImGui::SameLine();
    if (ImGui::Button("Close", ImVec2(90, 30))) close();

    if (have_result_) {
        const auto& an = result_->annotation;
        int kept = 0; for (const auto& r : an.reps) if (!r.rejected) ++kept;
        ImGui::SameLine(0, 24);
        ImGui::Text("%s   |   %d reps, %d kept   |   camera tilt %.2f deg   |   %ld frames the marker was not seen on",
                    result_->exercise.c_str(), (int)an.reps.size(), kept,
                    result_->frame.tilt_deg, result_->lost_frames);
    }
    if (!status_.empty()) {
        ImGui::PushStyleColor(ImGuiCol_Text, status_bad_ ? ImVec4(0.85f,0.35f,0.35f,1)
                                                         : ImVec4(0.55f,0.62f,0.70f,1));
        ImGui::TextUnformatted(status_.c_str());
        ImGui::PopStyleColor();
    }
}

// ----------------------------------------------------------------------------- verdict

void PostSessionPanel::draw_verdict() {
    if (!have_result_) return;
    const auto& an = result_->annotation;
    const int found = (int)an.reps.size();
    int live_confirmed = 0;
    for (const auto& r : live_) if (r.confirmed) ++live_confirmed;

    // What could make this set unusable, each stated as a number rather than a colour.
    int with_gaps = 0, short_reps = 0;
    double med = 0.0;
    {
        std::vector<double> rom;
        for (const auto& r : an.reps) { rom.push_back(r.rom_m); if (r.gap_frames) ++with_gaps; }
        if (!rom.empty()) {
            std::vector<double> t = rom; std::sort(t.begin(), t.end());
            med = t[t.size() / 2];
            for (double v : rom) if (med > 0 && v < 0.5 * med) ++short_reps;
        }
    }
    const bool worth_a_look = (live_confirmed && std::abs(found - live_confirmed) > 1)
                            || with_gaps || short_reps || result_->lost_frames > 0;

    ImGui::PushStyleColor(ImGuiCol_ChildBg, worth_a_look ? ImVec4(0.20f, 0.13f, 0.06f, 1.0f)
                                                         : ImVec4(0.08f, 0.16f, 0.11f, 1.0f));
    ImGui::BeginChild("##verdict", ImVec2(0, 34), ImGuiChildFlags_None);
    ImGui::SetCursorPos(ImVec2(10, 7));
    if (worth_a_look) {
        ImGui::TextColored(ImVec4(0.95f, 0.75f, 0.35f, 1), "CHECK THIS SET");
    } else {
        ImGui::TextColored(ImVec4(0.55f, 0.85f, 0.60f, 1), "SET LOOKS CLEAN");
    }
    ImGui::SameLine(0, 16);
    ImGui::Text("%d reps", found);
    if (live_confirmed) {
        ImGui::SameLine(0, 14);
        ImGui::TextDisabled("| live count %d", live_confirmed);
    }
    if (result_->lost_frames) {
        ImGui::SameLine(0, 14);
        ImGui::TextColored(ImVec4(0.90f, 0.65f, 0.25f, 1),
                           "| marker not seen on %ld frames", result_->lost_frames);
    }
    if (with_gaps) {
        ImGui::SameLine(0, 14);
        ImGui::TextColored(ImVec4(0.90f, 0.65f, 0.25f, 1), "| %d reps cross a gap", with_gaps);
    }
    if (short_reps) {
        ImGui::SameLine(0, 14);
        ImGui::TextColored(ImVec4(0.90f, 0.65f, 0.25f, 1),
                           "| %d reps under half the usual travel", short_reps);
    }
    ImGui::SameLine(0, 14);
    ImGui::TextDisabled("| usual travel %.0f mm", med * 1000);
    ImGui::EndChild();
    ImGui::PopStyleColor();
}

// ------------------------------------------------------------------------ session list

void PostSessionPanel::draw_session_list() {
    ImGui::TextDisabled("SESSIONS");
    ImGui::SetNextItemWidth(-1);
    ImGui::InputTextWithHint("##search", "search by name", search_, sizeof search_);
    ImGui::Separator();
    const std::string needle = search_;
    ImGui::BeginChild("##sessions", ImVec2(230, ImGui::GetContentRegionAvail().y - 22),
                      ImGuiChildFlags_None);
    int shown = 0;
    for (const auto& e : sessions_) {
        if (!needle.empty() && e.id.find(needle) == std::string::npos) continue;
        ++shown;
        const bool sel = (e.id == selected_);
        // the state of a session is in its mark, so the list is scannable
        const char* mark = e.reviewed ? "[R]" : e.annotated ? "[A]" : "[ ]";
        char label[160];
        std::snprintf(label, sizeof label, "%s %s##%s", mark, e.id.c_str() + 8, e.id.c_str());
        if (ImGui::Selectable(label, sel)) select(e.id);
    }
    if (shown == 0) ImGui::TextDisabled("  no session matches");
    ImGui::EndChild();
    ImGui::TextDisabled("[ ] not yet   [A] annotated   [R] reviewed");
}

// ------------------------------------------------------------------------------ audit

void PostSessionPanel::draw_audit() {
    if (!have_result_) {
        ImGui::Dummy(ImVec2(0, 30));
        if (sessions_.empty()) {
            // An empty list is almost always the working directory, not a missing dataset.
            ImGui::TextColored(ImVec4(0.85f, 0.45f, 0.35f, 1), "  No sessions found.");
            ImGui::TextDisabled("  Looked in: %s",
                                fs::absolute(fs::path(app_.config().dataset_root)).c_str());
            ImGui::TextDisabled("  Start the app from the repository root, or set "
                                "dataset_root in vbt_config.json.");
        } else if (selected_.empty()) {
            ImGui::TextDisabled("  Pick a session on the left.");
        } else {
            ImGui::TextDisabled("  %s has not been through the post-session pass yet.",
                                selected_.c_str());
            ImGui::TextDisabled("  Press \"Run post-session annotation\".");
        }
        return;
    }
    const auto& an = result_->annotation;
    const auto& L  = an.lines;
    const size_t n = pos_.size();
    if (n < 2) return;
    // WHICH ANNOTATION IS BEING LOOKED AT. The live one is what the annotator produced
    // while the set was being recorded; the post-session one is what this window's button
    // produced. Both are drawn on the same track so they can be compared directly.
    if (ImGui::BeginTabBar("##which")) {
        if (ImGui::BeginTabItem("Post-session annotation (rotated, smoothed)")) {
            show_live_ = false; ImGui::EndTabItem();
        }
        ImGui::BeginDisabled(live_.empty());
        if (ImGui::BeginTabItem(live_.empty() ? "Live annotation (none recorded)"
                                              : "Live annotation (signal as recorded)")) {
            show_live_ = true; ImGui::EndTabItem();
        }
        ImGui::EndDisabled();
        ImGui::EndTabBar();
    }
    if (live_.empty()) show_live_ = false;

    const float h = ImGui::GetContentRegionAvail().y;

    hovered_rep_ = -1;

    // ---- height, the three lines, and the reps -------------------------------------
    if (ImPlot::BeginPlot("##height", ImVec2(-1, h * 0.52f))) {
        ImPlot::SetupAxes("", "height (m)");
        ImPlot::SetupAxisLinks(ImAxis_X1, &x_min_, &x_max_);

        // WHICH SIGNAL. The live annotator only ever saw the vertical as recorded, so the
        // live tab draws that; the post-session annotation was made on the rotated,
        // smoothed track, so its tab draws that one. Drawing both over the same curve
        // would misrepresent what each pass actually worked on.
        const std::vector<double>& curve = (show_live_ && !raw_pos_.empty()) ? raw_pos_ : pos_;
        // The track goes down first: everything below shades against ITS axis limits, and
        // before an item is plotted those limits are not the ones the data will use.
        ImPlot::SetNextLineStyle(ImVec4(0.92f, 0.92f, 0.94f, 1.0f), 1.6f);
        ImPlot::PlotLine(show_live_ ? "bar (as recorded)" : "bar (rotated, smoothed)",
                         t_.data(), curve.data(), (int)n);

        if (show_live_) {
            // the live annotator's own reps, drawn the same way
            for (const auto& r : live_) {
                struct Half { int64_t a, b; ImVec4 c; };
                const Half halves[2] = {
                    {r.concentric_start_frame, r.concentric_end_frame, kCon},
                    {r.eccentric_start_frame,  r.eccentric_end_frame,  kEcc}};
                for (const auto& hf : halves) {
                    if (hf.a < 0 || hf.b <= hf.a || (size_t)hf.b >= n) continue;
                    ImPlot::PushPlotClipRect();
                    const ImVec2 p0 = ImPlot::PlotToPixels((double)hf.a / fps(),
                                                           ImPlot::GetPlotLimits().Y.Max);
                    const ImVec2 p1 = ImPlot::PlotToPixels((double)hf.b / fps(),
                                                           ImPlot::GetPlotLimits().Y.Min);
                    ImPlot::GetPlotDrawList()->AddRectFilled(
                        p0, p1, shade(hf.c, r.confirmed ? 0.22f : 0.10f));
                    ImPlot::PopPlotClipRect();
                }
            }
        }
        // rep bands. A refused rep goes grey, so a refusal reads as a decision rather
        // than as an absence.
        for (size_t k = 0; !show_live_ && k < an.reps.size(); ++k) {
            const auto& r = an.reps[k];
            struct Half { int64_t a, b; ImVec4 c; };
            const Half halves[2] = {
                {r.concentric_start_frame, r.concentric_end_frame, r.rejected ? kRefused : kCon},
                {r.eccentric_start_frame,  r.eccentric_end_frame,  r.rejected ? kRefused : kEcc}};
            for (const auto& hf : halves) {
                if (hf.a < 0 || hf.b <= hf.a || (size_t)hf.b >= n) continue;
                double xs[2] = {(double)hf.a / fps(), (double)hf.b / fps()};
                ImPlot::PushPlotClipRect();
                const ImVec2 p0 = ImPlot::PlotToPixels(xs[0], ImPlot::GetPlotLimits().Y.Max);
                const ImVec2 p1 = ImPlot::PlotToPixels(xs[1], ImPlot::GetPlotLimits().Y.Min);
                ImPlot::GetPlotDrawList()->AddRectFilled(p0, p1, shade(hf.c, r.rejected ? 0.12f : 0.22f));
                ImPlot::PopPlotClipRect();
            }
        }

        // the three lines. The middle one is what the counting depends on, so it is solid.
        if (L.valid) {
            ImPlot::SetNextLineStyle(kLine, 1.2f);
            double y[2] = {L.low, L.low}, x[2] = {0, t_.back()};
            ImPlot::PlotLine("bottom", x, y, 2);
            y[0] = y[1] = L.high; ImPlot::PlotLine("top", x, y, 2);
            ImPlot::SetNextLineStyle(kLine, 2.4f);
            y[0] = y[1] = L.mid;  ImPlot::PlotLine("MIDDLE", x, y, 2);
        }

        // start and end of every rep
        for (size_t i = 0; !show_live_ && i < an.reps.size(); ++i) {
            const auto& r = an.reps[i];
            const int64_t a = std::min(r.concentric_start_frame, r.eccentric_start_frame);
            const int64_t b = std::max(r.concentric_end_frame,   r.eccentric_end_frame);
            if (a < 0 || (size_t)b >= n) continue;
            double xs[2] = {(double)a / fps(), (double)b / fps()};
            double ys[2] = {curve[a], curve[b]};
            ImPlot::SetNextMarkerStyle(ImPlotMarker_Circle, 4,
                                       r.rejected ? kRefused : ImVec4(1,1,1,1), 0);
            ImPlot::PlotScatter("##s", &xs[0], &ys[0], 1);
            ImPlot::SetNextMarkerStyle(ImPlotMarker_Square, 4,
                                       r.rejected ? kRefused : ImVec4(1,1,1,1), 0);
            ImPlot::PlotScatter("##e", &xs[1], &ys[1], 1);
        }

        draw_cursor((double)cursor_frame_ / fps());
        if (ImPlot::IsPlotHovered()) {
            const double mx = ImPlot::GetPlotMousePos().x;
            const int f = (int)std::llround(mx * fps());
            if (follow_mouse_ && f >= 0 && f < (int)n) cursor_frame_ = f;
            // a click always parks the cursor there, so it can be studied
            if (ImGui::IsMouseClicked(ImGuiMouseButton_Right) && f >= 0 && f < (int)n) {
                cursor_frame_ = f; follow_mouse_ = false;
            }
        }

        // click a rep to refuse it, click again to take it back. Only the post-session
        // annotation can be refused: the live one is a record of what happened.
        if (!show_live_ && ImPlot::IsPlotHovered()) {
            const double mx = ImPlot::GetPlotMousePos().x;
            const int64_t f = (int64_t)std::llround(mx * fps());
            for (size_t k = 0; k < an.reps.size(); ++k) {
                const auto& r = an.reps[k];
                const int64_t a = std::min(r.concentric_start_frame, r.eccentric_start_frame);
                const int64_t b = std::max(r.concentric_end_frame,   r.eccentric_end_frame);
                if (f >= a && f <= b) { hovered_rep_ = (int)k; break; }
            }
            if (hovered_rep_ >= 0) {
                ImGui::BeginTooltip();
                const auto& r = an.reps[hovered_rep_];
                ImGui::Text("rep %d", r.rep_id);
                ImGui::Text("travel %.0f mm   fastest %.2f m/s", r.rom_m * 1000, r.peak_velocity);
                if (r.gap_frames) ImGui::Text("marker not seen on %d frames", r.gap_frames);
                ImGui::TextDisabled(r.rejected ? "click to keep it" : "click to refuse it");
                ImGui::EndTooltip();
                if (ImGui::IsMouseClicked(ImGuiMouseButton_Left)) {
                    result_->annotation.reps[hovered_rep_].rejected =
                        !result_->annotation.reps[hovered_rep_].rejected;
                    review_dirty_ = true;
                }
            }
        }
        ImPlot::EndPlot();
    }

    // ---- speed, and what the bar was doing on every frame ---------------------------
    if (ImPlot::BeginPlot("##speed", ImVec2(-1, h * 0.24f))) {
        ImPlot::SetupAxes("", "speed (m/s)");
        ImPlot::SetupAxisLinks(ImAxis_X1, &x_min_, &x_max_);
        ImPlot::SetNextLineStyle(ImVec4(0.85f, 0.85f, 0.88f, 1.0f), 1.2f);
        ImPlot::PlotLine("speed", t_.data(), vel_.data(), (int)n);
        // the boundaries: filled = the bar arrived, hollow = it was let go while moving
        for (const auto& b : an.boundaries) {
            if (b.frame < 0 || (size_t)b.frame >= n) continue;
            double x = (double)b.frame / fps(), y = vel_[b.frame];
            ImPlot::SetNextMarkerStyle(ImPlotMarker_Down, 4,
                                       b.arrived ? ImVec4(0.35f,0.62f,0.92f,1) : ImVec4(0,0,0,0),
                                       1, ImVec4(0.35f,0.62f,0.92f,1));
            ImPlot::PlotScatter("##b", &x, &y, 1);
        }
        draw_cursor((double)cursor_frame_ / fps());
        ImPlot::EndPlot();
    }

    // ---- the push, with the smoother's own uncertainty about it ---------------------
    if (ImPlot::BeginPlot("##push", ImVec2(-1, h * 0.20f))) {
        ImPlot::SetupAxes("time (s)", "push (m/s^2)");
        ImPlot::SetupAxisLinks(ImAxis_X1, &x_min_, &x_max_);
        ImPlot::SetNextLineStyle(ImVec4(0.75f, 0.75f, 0.80f, 1.0f), 1.0f);
        ImPlot::PlotLine("push", t_.data(), acc_.data(), (int)n);
        draw_cursor((double)cursor_frame_ / fps());
        ImPlot::EndPlot();
    }
}

// ------------------------------------------------------------------------------ video

void PostSessionPanel::draw_video() {
    if (!have_result_) return;
    const int n = (int)pos_.size();
    if (n < 2) return;
    auto clampf = [&](int f) { return std::max(0, std::min(n - 1, f)); };

    // ---- play head ------------------------------------------------------------------
    if (playing_) {
        play_accum_ += (double)ImGui::GetIO().DeltaTime * fps() * (double)play_speed_;
        const int step = (int)play_accum_;
        if (step != 0) {
            play_accum_ -= step;
            cursor_frame_ += step;
            if (cursor_frame_ >= n - 1) { cursor_frame_ = n - 1; playing_ = false; }
        }
    }

    ImGui::TextDisabled("VIDEO");
    ImGui::SameLine(0, 10);
    int rep_here = -1;
    for (size_t k = 0; k < result_->annotation.reps.size(); ++k) {
        const auto& r = result_->annotation.reps[k];
        const int64_t ra = std::min(r.concentric_start_frame, r.eccentric_start_frame);
        const int64_t rb = std::max(r.concentric_end_frame,   r.eccentric_end_frame);
        if (cursor_frame_ >= ra && cursor_frame_ <= rb) { rep_here = (int)k; break; }
    }
    const bool delivered = cursor_frame_ >= 0
                        && (size_t)cursor_frame_ < result_->video_row.size()
                        && result_->video_row[cursor_frame_] >= 0;
    ImGui::Text("frame %d / %d   %.2f s", cursor_frame_, n - 1, (double)cursor_frame_ / fps());
    if (!delivered) {
        ImGui::SameLine(0, 10);
        ImGui::TextColored(ImVec4(0.90f, 0.65f, 0.25f, 1), "camera dropped this frame");
    }
    ImGui::SameLine(0, 12);
    if (rep_here >= 0) {
        const auto& r = result_->annotation.reps[rep_here];
        ImGui::TextColored(r.rejected ? kRefused : ImVec4(0.45f, 0.72f, 0.95f, 1),
                           "rep %d%s", r.rep_id, r.rejected ? "  REFUSED" : "");
    } else {
        ImGui::TextDisabled("between reps");
    }
    ImGui::Separator();

    // ---- controls first, so they can never be pushed off the bottom by the picture ---
    const float ctl_h = ImGui::GetFrameHeightWithSpacing() * 3.0f + 6.0f;
    const float avail_h = ImGui::GetContentRegionAvail().y;
    const float img_h   = std::max(60.0f, avail_h - ctl_h);
    const float avail_w = ImGui::GetContentRegionAvail().x;

    if (!video_.is_open()) {
        ImGui::Dummy(ImVec2(0, 8));
        ImGui::TextDisabled("  no camera/ir_video.mp4 in this session");
        ImGui::Dummy(ImVec2(0, img_h - 40));
    } else {
        // The video holds only the frames the camera DELIVERED, while the track counts
        // every frame the camera should have delivered. They part company at the first
        // drop, so the row has to be looked up rather than assumed equal.
        int row = -1;
        if (cursor_frame_ >= 0 && (size_t)cursor_frame_ < result_->video_row.size())
            row = result_->video_row[cursor_frame_];
        if (row < 0) {
            // this frame was never delivered; show the last one that was
            for (int i = cursor_frame_; i >= 0 && row < 0; --i)
                if ((size_t)i < result_->video_row.size()) row = result_->video_row[i];
        }
        video_.seek(row < 0 ? 0 : row);
        const unsigned int tex = video_.texture();
        if (tex && video_.width() > 0 && video_.height() > 0) {
            // fit inside the pane, keeping the aspect ratio, so the controls always show
            const float ar = (float)video_.width() / (float)video_.height();
            float w = avail_w, h = w / ar;
            if (h > img_h) { h = img_h; w = h * ar; }
            ImGui::SetCursorPosX(ImGui::GetCursorPosX() + (avail_w - w) * 0.5f);
            ImGui::Image((ImTextureID)(intptr_t)tex, ImVec2(w, h));
        } else {
            ImGui::TextDisabled("  could not decode this frame");
            ImGui::Dummy(ImVec2(0, img_h - 20));
        }
    }

    // ---- scrub ----------------------------------------------------------------------
    ImGui::SetNextItemWidth(-1);
    if (ImGui::SliderInt("##scrub", &cursor_frame_, 0, n - 1)) { playing_ = false; follow_mouse_ = false; }

    // ---- transport ------------------------------------------------------------------
    auto step = [&](int d) { cursor_frame_ = clampf(cursor_frame_ + d); playing_ = false; follow_mouse_ = false; };
    const float bw = 44.0f;
    if (ImGui::Button("|<", ImVec2(34, 0)))       { cursor_frame_ = 0; playing_ = false; }
    ImGui::SameLine(); if (ImGui::Button("-1s", ImVec2(bw, 0)))  step(-(int)fps());
    ImGui::SameLine(); if (ImGui::Button("-1f", ImVec2(bw, 0)))  step(-1);
    ImGui::SameLine();
    if (ImGui::Button(playing_ ? "Pause" : "Play", ImVec2(64, 0))) {
        playing_ = !playing_; follow_mouse_ = false;
        if (playing_ && cursor_frame_ >= n - 1) cursor_frame_ = 0;
    }
    ImGui::SameLine(); if (ImGui::Button("+1f", ImVec2(bw, 0)))  step(+1);
    ImGui::SameLine(); if (ImGui::Button("+1s", ImVec2(bw, 0)))  step(+(int)fps());
    ImGui::SameLine(); if (ImGui::Button(">|", ImVec2(34, 0)))   { cursor_frame_ = n - 1; playing_ = false; }
    ImGui::SameLine(0, 10);
    ImGui::SetNextItemWidth(90);
    ImGui::SliderFloat("##speed", &play_speed_, 0.1f, 2.0f, "%.1fx");

    // arrow keys step too, so the hand can stay off the mouse
    if (ImGui::IsWindowFocused(ImGuiFocusedFlags_ChildWindows)) {
        if (ImGui::IsKeyPressed(ImGuiKey_LeftArrow,  true)) step(ImGui::GetIO().KeyShift ? -(int)fps() : -1);
        if (ImGui::IsKeyPressed(ImGuiKey_RightArrow, true)) step(ImGui::GetIO().KeyShift ? +(int)fps() : +1);
    }

    // ---- jump to a rep, and judge it ------------------------------------------------
    if (ImGui::Button("< prev rep", ImVec2(90, 0))) {
        for (int k = (int)result_->annotation.reps.size() - 1; k >= 0; --k) {
            const auto& r = result_->annotation.reps[k];
            const int64_t ra = std::min(r.concentric_start_frame, r.eccentric_start_frame);
            if (ra < cursor_frame_ - 2) { cursor_frame_ = (int)ra; playing_ = false; break; }
        }
    }
    ImGui::SameLine();
    if (ImGui::Button("next rep >", ImVec2(90, 0))) {
        for (const auto& r : result_->annotation.reps) {
            const int64_t ra = std::min(r.concentric_start_frame, r.eccentric_start_frame);
            if (ra > cursor_frame_ + 2) { cursor_frame_ = (int)ra; playing_ = false; break; }
        }
    }
    ImGui::SameLine(); ImGui::Checkbox("follow mouse", &follow_mouse_);
    if (rep_here >= 0) {
        auto& r = result_->annotation.reps[rep_here];
        ImGui::SameLine(0, 10);
        if (ImGui::Button(r.rejected ? "keep this rep" : "refuse this rep", ImVec2(130, 0))) {
            r.rejected = !r.rejected; review_dirty_ = true;
        }
    }
}

// -------------------------------------------------------------------------- rep table

void PostSessionPanel::draw_rep_table() {
    if (!have_result_) return;
    auto& reps = result_->annotation.reps;
    if (ImGui::BeginTable("##reps", 7,
                          ImGuiTableFlags_RowBg | ImGuiTableFlags_Borders |
                          ImGuiTableFlags_ScrollY | ImGuiTableFlags_SizingStretchProp)) {
        ImGui::TableSetupScrollFreeze(0, 1);
        ImGui::TableSetupColumn("keep", ImGuiTableColumnFlags_WidthFixed, 40);
        ImGui::TableSetupColumn("rep",  ImGuiTableColumnFlags_WidthFixed, 36);
        ImGui::TableSetupColumn("start (s)");
        ImGui::TableSetupColumn("end (s)");
        ImGui::TableSetupColumn("travel (mm)");
        ImGui::TableSetupColumn("fastest (m/s)");
        ImGui::TableSetupColumn("unseen frames");
        ImGui::TableHeadersRow();
        for (size_t k = 0; k < reps.size(); ++k) {
            auto& r = reps[k];
            const int64_t a = std::min(r.concentric_start_frame, r.eccentric_start_frame);
            const int64_t b = std::max(r.concentric_end_frame,   r.eccentric_end_frame);
            ImGui::TableNextRow();
            if ((int)k == hovered_rep_)
                ImGui::TableSetBgColor(ImGuiTableBgTarget_RowBg0, shade(kLine, 0.20f));
            ImGui::TableSetColumnIndex(0);
            bool keep = !r.rejected;
            ImGui::PushID((int)k);
            if (ImGui::Checkbox("##k", &keep)) { r.rejected = !keep; review_dirty_ = true; }
            ImGui::PopID();
            ImGui::TableSetColumnIndex(1); ImGui::Text("%d", r.rep_id);
            ImGui::TableSetColumnIndex(2); ImGui::Text("%.2f", (double)a / fps());
            ImGui::TableSetColumnIndex(3); ImGui::Text("%.2f", (double)b / fps());
            ImGui::TableSetColumnIndex(4); ImGui::Text("%.0f", r.rom_m * 1000);
            ImGui::TableSetColumnIndex(5); ImGui::Text("%.2f", r.peak_velocity);
            ImGui::TableSetColumnIndex(6);
            if (r.gap_frames) ImGui::TextColored(ImVec4(0.90f,0.65f,0.25f,1), "%d", r.gap_frames);
            else              ImGui::TextDisabled("0");
        }
        ImGui::EndTable();
    }
}

// ------------------------------------------------------------------------------ render

void PostSessionPanel::render() {
    if (!is_open_) return;
    const ImGuiViewport* vp = ImGui::GetMainViewport();
    ImGui::SetNextWindowPos(vp->WorkPos);
    ImGui::SetNextWindowSize(vp->WorkSize);
    ImGui::Begin("Post-session annotation", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse | ImGuiWindowFlags_NoTitleBar |
                 ImGuiWindowFlags_NoBringToFrontOnFocus);

    ImGui::Text("Post-session annotation");
    ImGui::SameLine(0, 16);
    ImGui::TextDisabled("click a rep to refuse it \xe2\x80\x94 the algorithm's own output is never edited");
    ImGui::Separator();
    draw_toolbar();
    draw_verdict();
    ImGui::Separator();

    ImGui::BeginChild("##left", ImVec2(240, 0), ImGuiChildFlags_None);
    draw_session_list();
    ImGui::EndChild();

    ImGui::SameLine();
    const float table_w = 560.0f;
    const float mid_w = std::max(320.0f, ImGui::GetContentRegionAvail().x - table_w);
    ImGui::BeginChild("##mid", ImVec2(mid_w, 0));
    draw_audit();
    ImGui::EndChild();

    ImGui::SameLine();
    ImGui::BeginChild("##right", ImVec2(0, 0));
    // the video needs room for three rows of controls under the picture
    const float vid_h = ImGui::GetContentRegionAvail().y * 0.62f;
    ImGui::BeginChild("##video", ImVec2(0, vid_h));
    draw_video();
    ImGui::EndChild();
    ImGui::Separator();
    ImGui::TextDisabled("REPS");
    draw_rep_table();
    ImGui::EndChild();

    ImGui::End();
}

} // namespace vbt

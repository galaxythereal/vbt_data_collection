#pragma once

/**
 * @file PostSessionPanel.h
 * @brief The post-session annotation window: run the offline pass, look at what it found,
 *        refuse the reps that are wrong, save.
 *
 * WHAT IT IS FOR. The counting is right on 1405 of 1409 reps, and a handful more are
 * damaged at acquisition in ways no algorithm can fix -- a person walked across the
 * marker, or the marker dropped out mid-rep. Those need a person to look and refuse them.
 * This is where that happens.
 *
 * WHAT IT DOES NOT DO. It never edits the algorithm's own output. A refusal is written to
 * annotation/reviewed.csv beside it, so the record shows what was refused rather than
 * quietly missing it. The reviewer can accept or refuse a rep; there is no way to drag a
 * boundary, because a hand-placed boundary would not be reproducible.
 *
 * Sessions recorded before the app had this button are opened exactly the same way: the
 * pass reads datasets/raw/<session>/ and derives everything, so there is no difference
 * between a session processed today and one processed at acquisition time.
 */

#include <memory>
#include <string>
#include <vector>

#include "offline/OfflinePipeline.h"
#include "offline/SessionVideo.h"
#include "rt_annotator/RtTypes.h"

namespace vbt {

class Application;

class PostSessionPanel {
public:
    explicit PostSessionPanel(Application& app);
    ~PostSessionPanel();

    /// Open the window. With a session id, select and load that session.
    void open(const std::string& session_id = {});
    void close() { is_open_ = false; }
    bool is_open() const { return is_open_; }

    /// A set has just finished recording. Opens on that session, annotates it and writes
    /// its audit, so the operator sees whether the set was captured before the lifter has
    /// left the bar.
    void on_session_finished(const std::string& session_id);

    /// True while the operator still has an unsaved judgement on a set. The main screen
    /// uses it to keep the button lit.
    bool needs_review() const { return review_dirty_; }

    void render();

private:
    struct Entry {
        std::string id;
        bool annotated = false;   ///< has annotation/offline.csv
        bool reviewed  = false;   ///< has annotation/reviewed.csv
    };

    void refresh_sessions();
    void select(const std::string& id);
    void load_selected();                 ///< read what is already on disk, run nothing
    void run_pass();                      ///< the button
    void save_review();
    offline::SessionPaths paths_for(const std::string& id) const;

    void load_live_annotation();          ///< what the annotator produced during the set

    /// Frames per second for the session on screen, measured from its own trigger
    /// pulses. Falls back to the nominal rate when nothing is loaded.
    double fps() const;

    void generate_audit();
    void draw_verdict();          ///< the one line that says whether the set is usable

    void draw_toolbar();
    void draw_video();
    void draw_session_list();
    void draw_audit();
    void draw_rep_table();

    Application& app_;
    bool         is_open_ = false;

    std::vector<Entry> sessions_;
    std::string        selected_;
    char               search_[64] = {0};   ///< filter the list by name
    std::string        status_;
    bool               status_bad_ = false;

    /// What the causal annotator produced DURING the set, read back from disk. Shown
    /// beside the post-session pass so the two can be compared on the same track.
    std::vector<rt::RtRep> live_;
    bool                   show_live_ = false;

    /// The last pass, kept whole so the window can draw the working, not just the answer.
    std::unique_ptr<offline::PipelineResult> result_;
    bool  have_result_   = false;
    bool  review_dirty_  = false;
    int   hovered_rep_   = -1;

    // drawing buffers, rebuilt when a session is loaded
    std::vector<double> t_, pos_, vel_, acc_;
    /// The vertical as recorded, for the LIVE tab. The live annotator never saw the
    /// rotated or smoothed signal, so it must not be drawn over one.
    std::vector<double> raw_pos_;

    /// The three plots pan and zoom together, so the eye stays on one rep.
    double x_min_ = 0.0, x_max_ = 1.0;

    /// The session's own IR video, seekable by the frame the annotation counts in.
    /// A person walking across the marker is one glance in the frame and invisible in
    /// the track, so the reviewer needs to see it.
    offline::SessionVideo video_;
    int   cursor_frame_ = 0;      ///< which frame the video and the plots point at
    bool  follow_mouse_ = false;  ///< move the cursor with the mouse over the plot
    bool  playing_      = false;
    float play_speed_   = 1.0f;   ///< 1.0 = real time (90 fps)
    double play_accum_  = 0.0;
};

} // namespace vbt

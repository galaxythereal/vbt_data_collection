/**
 * @file MainWindow.cpp
 * @brief ImGui main window with professional tiled layout.
 */
#include "gui/MainWindow.h"
#include "gui/Fonts.h"
#include "gui/SensorPanel.h"
#include "gui/SessionPanel.h"
#include "gui/PlotPanel.h"
#include "gui/CalibrationPanel.h"
#include "gui/CameraPanel.h"
#include "gui/PreflightPanel.h"
#include "gui/CalibrationWizard.h"
#include "gui/ReplayMode.h"
#include "annotation/AnnotationStudio.h"
#include "app/Application.h"
#include "app/Config.h"
#include "app/Version.h"
#include "core/Session.h"
#include "core/CalibrationManager.h"
#include "utils/Notifications.h"
#include "utils/AudioCue.h"
#include "utils/DiagnosticExport.h"
#include <imgui.h>
#include <algorithm>
#include <chrono>
#include <cstdio>
#include <filesystem>

namespace vbt {
bool save_config(const std::string& path, const AppConfig& config);

namespace {
// Singleton calibration manager so the wizard and panel share state.
CalibrationManager& shared_calib_mgr() {
    static CalibrationManager m;
    return m;
}

// Filled rounded-corner status pill — much more glanceable than "● text".
// Use for binary states (Connected / Synced / Recording / Disconnected).
void status_pill(const char* label, ImVec4 bg, ImVec4 fg = ImVec4(1,1,1,1)) {
    ImGui::PushStyleColor(ImGuiCol_Button,        bg);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, bg);
    ImGui::PushStyleColor(ImGuiCol_ButtonActive,  bg);
    ImGui::PushStyleColor(ImGuiCol_Text,          fg);
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 12.0f);
    ImGui::PushStyleVar(ImGuiStyleVar_FramePadding, ImVec2(10, 4));
    ImGui::Button(label);
    ImGui::PopStyleVar(2);
    ImGui::PopStyleColor(4);
}
constexpr ImVec4 kPillGreen{0.18f, 0.62f, 0.32f, 1.0f};
constexpr ImVec4 kPillRed  {0.72f, 0.20f, 0.22f, 1.0f};
constexpr ImVec4 kPillAmber{0.82f, 0.55f, 0.05f, 1.0f};
constexpr ImVec4 kPillBlue {0.15f, 0.50f, 0.85f, 1.0f};
constexpr ImVec4 kPillGray {0.32f, 0.34f, 0.40f, 1.0f};
}

MainWindow::MainWindow(Application& app) : app_(app) {
    sensor_panel_     = std::make_unique<SensorPanel>(app);
    session_panel_    = std::make_unique<SessionPanel>(app);
    plot_panel_       = std::make_unique<PlotPanel>(app);
    calib_panel_      = std::make_unique<CalibrationPanel>(app);
    camera_panel_     = std::make_unique<CameraPanel>(app);
    preflight_        = std::make_unique<PreflightPanel>(app);
    calib_wizard_     = std::make_unique<CalibrationWizard>(app, shared_calib_mgr());
    replay_           = std::make_unique<ReplayMode>(app);
    studio_           = std::make_unique<AnnotationStudio>(app);
}

MainWindow::~MainWindow() = default;

void MainWindow::process_hotkeys() {
    ImGuiIO& io = ImGui::GetIO();
    // Don't trigger when typing into a text field
    if (io.WantTextInput) return;

    auto& sess = app_.session();

    // F1 — toggle calibration wizard
    if (ImGui::IsKeyPressed(ImGuiKey_F1, false)) {
        if (calib_wizard_->is_open()) { /* no-op, user can close via X */ }
        else calib_wizard_->open();
    }
    // F2 — toggle replay (legacy quick-look)
    if (ImGui::IsKeyPressed(ImGuiKey_F2, false)) {
        replay_->open();
    }
    // F3 — open Annotation Studio (post-recording workspace)
    if (ImGui::IsKeyPressed(ImGuiKey_F3, false)) {
        studio_->open();
    }
    // F5 — diagnostic export
    if (ImGui::IsKeyPressed(ImGuiKey_F5, false)) {
        export_diagnostic_bundle(app_, "./diagnostics");
    }
    // Space — start/stop recording (only when state allows)
    if (ImGui::IsKeyPressed(ImGuiKey_Space, false)) {
        if (sess.get_state() == SessionState::CONFIGURED ||
            sess.get_state() == SessionState::READY) {
            if (!sess.has_pre_session_calibration()) {
                Notifications::get().warn("Capture pre-set calibration before recording.");
            } else if (show_preflight_) {
                // already gated
            } else {
                // open the pre-flight modal first
                show_preflight_ = true;
            }
        } else if (sess.get_state() == SessionState::RECORDING) {
            sess.stop_recording();
            AudioCue::play(Cue::StopRecord);
        }
    }
    // Ctrl+S — save session if stopped
    if (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_S, false)) {
        if (sess.get_state() == SessionState::STOPPED) {
            if (!sess.has_post_session_calibration()) {
                Notifications::get().warn("Capture post-set calibration before saving.");
            } else {
                sess.save();
            }
        }
    }
    // Ctrl+Q — quit
    if (io.KeyCtrl && ImGui::IsKeyPressed(ImGuiKey_Q, false)) {
        app_.shutdown();
    }
}

void MainWindow::check_orphaned_partials_once() {
    if (checked_partials_) return;
    checked_partials_ = true;
    auto orphans = Session::find_orphaned_partials(app_.config().dataset_root);
    if (!orphans.empty()) {
        Notifications::get().warn(
            std::to_string(orphans.size()) +
            " unfinalised set recording(s) found. Tools -> Recovery to inspect.");
        for (const auto& p : orphans) ImGui::OpenPopup("##recovery");
    }
}

void MainWindow::render() {
    static bool style_applied = false;
    if (!style_applied) { apply_style(); style_applied = true; }

    process_hotkeys();
    check_orphaned_partials_once();

    const bool annotation_studio_open = studio_ && studio_->is_open();
    if (!annotation_studio_open) {
        render_menu_bar();
    }

    ImGuiIO& io = ImGui::GetIO();
    float W = io.DisplaySize.x;
    float H = io.DisplaySize.y;
    float menu_h    = annotation_studio_open ? 0.0f : ImGui::GetFrameHeight();
    float header_h  = 70.0f;   // compact header band
    float status_h  = ImGui::GetFrameHeightWithSpacing();

    // Hero header band — branding, session context, primary CTAs, live status
    ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0.07f, 0.08f, 0.11f, 1.0f));
    ImGui::PushStyleVar(ImGuiStyleVar_WindowPadding, ImVec2(16, 8));
    ImGui::SetNextWindowPos(ImVec2(0, menu_h));
    ImGui::SetNextWindowSize(ImVec2(W, header_h));
    ImGui::Begin("##HeroHeader", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
                 ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse |
                 ImGuiWindowFlags_NoScrollbar | ImGuiWindowFlags_NoBringToFrontOnFocus);
    render_top_toolbar();
    ImGui::End();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor();

    float body_y = menu_h + header_h;
    float body_h = H - menu_h - header_h - status_h;

    // Responsive column widths: scale with screen size, minimum acceptable on
    // small displays. Center always gets the most room.
    float left_w   = std::clamp(W * 0.18f, 250.0f, 320.0f);
    float right_w  = std::clamp(W * 0.20f, 280.0f, 360.0f);
    float center_w = W - left_w - right_w;

    // LEFT
    ImGui::SetNextWindowPos(ImVec2(0, body_y));
    ImGui::SetNextWindowSize(ImVec2(left_w, body_h));
    ImGui::Begin("##LeftColumn", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
                 ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse);
    render_sensor_section();
    ImGui::Spacing(); ImGui::Spacing();
    render_sync_section();
    ImGui::End();

    // CENTER (plots + reps timeline/table)
    float plot_h = body_h * 0.72f;
    float annot_h = body_h - plot_h;

    ImGui::SetNextWindowPos(ImVec2(left_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(center_w, plot_h));
    ImGui::Begin("Live Visualization", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    bool imu_up = app_.session().imu().is_running();
    bool cam_up = app_.session().camera().is_running();
    if (cam_up) {
        // Tabs: IR Tracker (live camera + marker overlay) | Plots
        if (ImGui::BeginTabBar("##center_tabs", ImGuiTabBarFlags_FittingPolicyResizeDown)) {
            if (ImGui::BeginTabItem("IR Tracker")) {
                camera_panel_->render_content();
                ImGui::EndTabItem();
            }
            if (ImGui::BeginTabItem("Plots")) {
                plot_panel_->render_content();
                ImGui::EndTabItem();
            }
            ImGui::EndTabBar();
        }
    } else if (!imu_up || !cam_up) {
        // Guided welcome — centered panel walking the user through setup
        float aw = ImGui::GetContentRegionAvail().x;
        float ah = ImGui::GetContentRegionAvail().y;
        ImGui::Dummy(ImVec2(0, ah * 0.18f));
        const char* title = "Hardware setup";
        ImVec2 ts = ImGui::CalcTextSize(title);
        ImGui::SetCursorPosX((aw - ts.x*1.5f) * 0.5f);
        ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.85f, 1.0f, 1));
        ImGui::SetWindowFontScale(1.5f);
        ImGui::TextUnformatted(title);
        ImGui::SetWindowFontScale(1.0f);
        ImGui::PopStyleColor();
        ImGui::Dummy(ImVec2(0, 18));

        auto step = [&](int n, const char* label, bool done){
            float ix = (aw - 380) * 0.5f;
            ImGui::SetCursorPosX(ix);
            char num[8]; snprintf(num, sizeof(num), "%d", n);
            status_pill(num, done ? kPillGreen : kPillGray);
            ImGui::SameLine();
            ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 4);
            ImGui::PushStyleColor(ImGuiCol_Text,
                done ? ImVec4(0.92f,0.93f,0.95f,1) : ImVec4(0.62f,0.62f,0.65f,1));
            ImGui::TextUnformatted(label);
            ImGui::PopStyleColor();
        };

        // Hardware FSYNC tagging is the actual sync mechanism — tap test is a
        // legacy sanity check that's now optional (Tools menu).
        bool hw_sync_live = (app_.session().imu().get_stats().fsync_rate_hz > 5.0);

        step(1, imu_up ? "IMU connected" : "Connect IMU  (top-left button)", imu_up);
        ImGui::Dummy(ImVec2(0, 6));
        step(2, cam_up ? "Camera connected" : "Connect Camera  (top-left button)", cam_up);
        ImGui::Dummy(ImVec2(0, 6));
        step(3, hw_sync_live ? "Hardware sync receiving FSYNC ✓"
                             : "Waiting for camera FSYNC pulses…", hw_sync_live);
        ImGui::Dummy(ImVec2(0, 6));
        step(4, "Configure set details  (right panel)", false);
        ImGui::Dummy(ImVec2(0, 6));
        step(5, "Press START SET  (top toolbar)", false);

        ImGui::Dummy(ImVec2(0, 24));
        const char* hint = "Tip: F1 calibration  ·  F2 replay  ·  F12 operator view  ·  Space start/stop";
        ImVec2 hs = ImGui::CalcTextSize(hint);
        ImGui::SetCursorPosX((aw - hs.x) * 0.5f);
        ImGui::TextDisabled("%s", hint);
    }
    ImGui::End();

    // Center-bottom split: camera view (full IR feed, left) + rep stats (right).
    // Camera lives here rather than the right-column tab so the IR image isn't
    // squished into 300 px width. Rep stats default to the autoscrolling table
    // (v1.0 style) — toggle to timeline-bars view via the checkbox.
    float cam_w = center_w * 0.55f;
    float reps_w = center_w - cam_w;

    ImGui::SetNextWindowPos(ImVec2(left_w, body_y + plot_h));
    ImGui::SetNextWindowSize(ImVec2(cam_w, annot_h));
    ImGui::Begin("Camera View", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    camera_panel_->render_content();
    ImGui::End();

    ImGui::SetNextWindowPos(ImVec2(left_w + cam_w, body_y + plot_h));
    ImGui::SetNextWindowSize(ImVec2(reps_w, annot_h));
    ImGui::Begin("Rep Stats", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    ImGui::TextDisabled("(F1 calib · F2 replay · F3 studio)");
    ImGui::Separator();
    ImGui::TextWrapped(
        "Live rep segmentation was removed with the old realtime algorithm. "
        "Label reps offline in the Annotation Studio (F3).");
    ImGui::End();

    // RIGHT — Set + Metrics tabs (Camera moved to center-bottom)
    ImGui::SetNextWindowPos(ImVec2(left_w + center_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(right_w, body_h));
    ImGui::Begin("##RightColumn", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
                 ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse);
    if (ImGui::BeginTabBar("##right_tabs", ImGuiTabBarFlags_FittingPolicyResizeDown)) {
        if (ImGui::BeginTabItem("Set")) {
            session_panel_->render_content();
            if (session_panel_->consume_preflight_request()) show_preflight_ = true;
            ImGui::EndTabItem();
        }
        if (ImGui::BeginTabItem("Camera Settings")) {
            // Camera tuning sliders live here; the live IR feed is in the
            // center-bottom CameraView panel where it has more room.
            auto& cfg = app_.config().camera;
            ImGui::SliderInt("Exposure (us)", &cfg.exposure_us, 10, 5000);
            ImGui::SliderInt("Gain", &cfg.gain, 16, 248);
            ImGui::SliderInt("Marker Threshold", &cfg.marker_threshold, 50, 254);
            ImGui::SliderFloat("Min Area", &cfg.marker_min_area, 5, 100);
            ImGui::SliderFloat("Max Area", &cfg.marker_max_area, 50, 2000);
            if (ImGui::IsItemDeactivatedAfterEdit())
                app_.session().tracker().configure(cfg);
            ImGui::EndTabItem();
        }
        ImGui::EndTabBar();
    }
    ImGui::End();

    // Wizards / modals
    if (show_calib_window_) {
        ImGui::SetNextWindowSize(ImVec2(500, 550), ImGuiCond_FirstUseEver);
        calib_panel_->render();
    }
    calib_wizard_->render();
    replay_->render();
    studio_->render();
    render_preflight_modal();

    if (show_demo_window_) ImGui::ShowDemoWindow(&show_demo_window_);

    // Sync auto-rearm warning
    if (app_.session().sync().rearm_required()) {
        static bool last_rearm = false;
        if (!last_rearm) {
            Notifications::get().error(
                "Sync drift exceeded threshold — re-run tap-test before continuing.");
            AudioCue::play(Cue::Warning);
            last_rearm = true;
        }
    }

    Notifications::get().render();
    render_status_bar();
}

void MainWindow::render_preflight_modal() {
    if (show_preflight_) ImGui::OpenPopup("Pre-flight Checklist");
    ImGui::SetNextWindowSize(ImVec2(620, 480), ImGuiCond_FirstUseEver);
    if (ImGui::BeginPopupModal("Pre-flight Checklist", &show_preflight_,
                               ImGuiWindowFlags_NoSavedSettings)) {
        bool ok = preflight_->render_and_check_blocking();
        bool calibrated = app_.session().has_pre_session_calibration();

        ImGui::Spacing();
        ImGui::Separator();
        if (!calibrated) {
            ImGui::TextColored(ImVec4(1.0f, 0.78f, 0.25f, 1.0f),
                "Pre-set calibration is required before recording.");
        }
        ImGui::BeginDisabled(!ok || !calibrated);
        if (ImGui::Button("Start Set", ImVec2(180, 32))) {
            app_.session().start_recording();
            AudioCue::play(Cue::StartRecord);
            show_preflight_ = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndDisabled();
        ImGui::SameLine();
        ImGui::BeginDisabled(!calibrated);
        if (ImGui::Button("Override + Start", ImVec2(160, 32))) {
            preflight_->record_override("operator overrode failed pre-flight");
            app_.session().start_recording();
            AudioCue::play(Cue::StartRecord);
            show_preflight_ = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndDisabled();
        ImGui::SameLine();
        if (ImGui::Button("Cancel", ImVec2(120, 32))) {
            show_preflight_ = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndPopup();
    }
}

void MainWindow::render_menu_bar() {
    if (ImGui::BeginMainMenuBar()) {
        if (ImGui::BeginMenu("File")) {
            if (ImGui::MenuItem("Save Config", "Ctrl+S")) {
                save_config("vbt_config.json", app_.config());
            }
            ImGui::Separator();
            if (ImGui::MenuItem("Quit", "Ctrl+Q")) app_.shutdown();
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Tools")) {
            if (ImGui::MenuItem("Pre-flight Checklist...", "Space")) show_preflight_ = true;
            if (ImGui::MenuItem("Calibration Wizard...",   "F1"))  calib_wizard_->open();
            if (ImGui::MenuItem("Replay Saved Set...", "F2"))  replay_->open();
            if (ImGui::MenuItem("Annotation Studio...", "F3"))     studio_->open();
            ImGui::Separator();
            ImGui::MenuItem("Legacy Calibration Panel", nullptr, &show_calib_window_);
            ImGui::Separator();
            if (ImGui::MenuItem("Export Diagnostic Bundle", "F5")) {
                export_diagnostic_bundle(app_, "./diagnostics");
            }
            ImGui::Separator();
            ImGui::MenuItem("ImGui Demo", nullptr, &show_demo_window_);
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Settings")) {
            bool audio = AudioCue::enabled();
            if (ImGui::MenuItem("Enable audio cues", nullptr, &audio)) {
                AudioCue::set_enabled(audio);
                app_.config().enable_audio_cues = audio;
            }
            ImGui::MenuItem("BIDS-style directory layout", nullptr, &app_.config().bids_layout);
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Help")) {
            ImGui::TextDisabled("VBT Data Collection v%s", kAppVersion);
            ImGui::TextDisabled("git %s (%s)", kGitSha, kGitBranch);
            ImGui::TextDisabled("built %s", kBuildTimestamp);
            ImGui::EndMenu();
        }

        // Recording indicator (right)
        auto& session = app_.session();
        float right_start = ImGui::GetWindowWidth() - 400;
        ImGui::SameLine(right_start);

        if (session.get_state() == SessionState::RECORDING) {
            float blink = (float)fmod(ImGui::GetTime() * 2.0, 2.0);
            float alpha = blink > 1.0f ? 2.0f - blink : blink;
            ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1, 0.2f, 0.2f, alpha));
            ImGui::Text("●");
            ImGui::PopStyleColor();
            ImGui::SameLine();
            ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(1, 0.3f, 0.3f, 1));
            ImGui::Text("REC");
            ImGui::PopStyleColor();

            auto stats = session.get_recording_stats();
            ImGui::SameLine();
            ImGui::Text("| %.1fs | IMU:%llu | Cam:%llu",
                        stats.duration_s, (unsigned long long)stats.imu_samples,
                        (unsigned long long)stats.camera_frames);
        } else {
            ImGui::TextDisabled("○ %s", session.get_state_string().c_str());
        }
        ImGui::EndMainMenuBar();
    }
}

void MainWindow::render_top_toolbar() {
    auto& session = app_.session();
    auto& imu     = session.imu();
    auto& cam     = session.camera();
    auto& sync    = session.sync();
    auto imu_st   = imu.get_stats();
    auto cam_st   = cam.get_stats();
    auto trk_st   = session.tracker().get_stats();
    auto sync_r   = sync.get_sync_result();

    // ─────── LEFT: brand title + set context ───────
    if (g_font_title) ImGui::PushFont(g_font_title);
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.85f, 1.0f, 1.0f));
    ImGui::TextUnformatted("VBT");
    ImGui::PopStyleColor();
    if (g_font_title) ImGui::PopFont();
    ImGui::SameLine();
    ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 8);
    ImGui::TextDisabled("Data Collection  v%s", kAppVersion);

    // Set context line (subject / exercise / rep)
    ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 4);
    const auto& sinfo = session.get_info();
    char ctx[160];
    std::string subj = sinfo.subject_id.empty() ? "(no subject)" : sinfo.subject_id;
    std::string ex   = sinfo.exercise.empty()   ? "(no exercise)"  : sinfo.exercise;
    snprintf(ctx, sizeof(ctx), "subject %s   ·   %s",
             subj.c_str(), ex.c_str());
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.70f, 0.72f, 0.78f, 1.0f));
    ImGui::TextUnformatted(ctx);
    ImGui::PopStyleColor();

    // ─────── RIGHT (top row): big record button ───────
    SessionState st = session.get_state();
    bool can_record = (st == SessionState::CONFIGURED || st == SessionState::READY);
    bool is_recording = (st == SessionState::RECORDING);

    ImVec4 rec_col = is_recording ? kPillRed
                   : can_record   ? kPillGreen
                                  : kPillGray;
    float rec_w = 190, rec_h = 42;
    ImGui::SetCursorPos(ImVec2(ImGui::GetWindowWidth() - rec_w - 16, 8));
    ImGui::PushStyleColor(ImGuiCol_Button,        rec_col);
    ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(rec_col.x*1.18f, rec_col.y*1.18f, rec_col.z*1.18f, 1));
    ImGui::PushStyleColor(ImGuiCol_ButtonActive,  ImVec4(rec_col.x*0.82f, rec_col.y*0.82f, rec_col.z*0.82f, 1));
    ImGui::PushStyleVar(ImGuiStyleVar_FrameRounding, 10.0f);
    if (g_font_metric) ImGui::PushFont(g_font_metric);
    const char* rec_label = is_recording ? "■  STOP"
                          : can_record   ? "●  START SET"
                                         : "●  Hardware not ready";
    ImGui::BeginDisabled(!can_record && !is_recording);
    if (ImGui::Button(rec_label, ImVec2(rec_w, rec_h))) {
        if (is_recording) {
            session.stop_recording();
            AudioCue::play(Cue::StopRecord);
        } else {
            show_preflight_ = true;
        }
    }
    ImGui::EndDisabled();
    if (g_font_metric) ImGui::PopFont();
    ImGui::PopStyleVar();
    ImGui::PopStyleColor(3);

    // ─────── MIDDLE row of bottom: connect/tap buttons ───────
    // Continue inline below the title
    ImGui::SetCursorPos(ImVec2(110, 42));

    // Quick connect — single button when both offline, individual when one is up
    auto try_connect_imu = [&]() {
        if (imu.open(app_.config().imu)) {
            imu.start();
            Notifications::get().success("IMU connected on " + app_.config().imu.port);
            return true;
        }
        Notifications::get().error("Failed to open IMU on " + app_.config().imu.port);
        return false;
    };
    auto try_connect_cam = [&]() {
        if (cam.open(app_.config().camera)) {
            session.tracker().configure(app_.config().camera);
            cam.start();
            // Install LIVE preview callback so the IR Tracker tab shows frames
            // before recording starts. start_recording() replaces this with a
            // fuller callback that also logs data.
            cam.set_callback([&session = session](const CameraFrame& f) {
                session.tracker().process(f.ir_left, f.ir_right, f.depth,
                                          f.ir_intrinsics, f.depth_intrinsics);
            });
            Notifications::get().success("RealSense camera connected — live IR preview on");
            return true;
        }
        Notifications::get().error("RealSense D455 not found on USB. Reseat the USB-C cable.");
        return false;
    };

    if (!imu.is_running() && !cam.is_running()) {
        ImGui::PushStyleColor(ImGuiCol_Button, kPillBlue);
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.60f, 0.95f, 1));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive,  ImVec4(0.10f, 0.40f, 0.75f, 1));
        if (ImGui::Button("Connect All Hardware", ImVec2(180, 28))) {
            try_connect_imu();
            try_connect_cam();
        }
        ImGui::PopStyleColor(3);
        ImGui::SameLine();
    } else {
        if (!imu.is_running()) {
            ImGui::PushStyleColor(ImGuiCol_Button, kPillBlue);
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.60f, 0.95f, 1));
            ImGui::PushStyleColor(ImGuiCol_ButtonActive,  ImVec4(0.10f, 0.40f, 0.75f, 1));
            if (ImGui::Button("Connect IMU", ImVec2(120, 28))) try_connect_imu();
            ImGui::PopStyleColor(3);
            ImGui::SameLine();
        }
        if (!cam.is_running()) {
            ImGui::PushStyleColor(ImGuiCol_Button, kPillBlue);
            ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.25f, 0.60f, 0.95f, 1));
            ImGui::PushStyleColor(ImGuiCol_ButtonActive,  ImVec4(0.10f, 0.40f, 0.75f, 1));
            if (ImGui::Button("Connect Camera", ImVec2(130, 28))) try_connect_cam();
            ImGui::PopStyleColor(3);
            ImGui::SameLine();
        }
    }
    if (sync.rearm_required()) {
        ImGui::PushStyleColor(ImGuiCol_Button, kPillRed);
        ImGui::PushStyleColor(ImGuiCol_ButtonHovered, ImVec4(0.85f, 0.25f, 0.25f, 1));
        ImGui::PushStyleColor(ImGuiCol_ButtonActive,  ImVec4(0.65f, 0.18f, 0.18f, 1));
        if (ImGui::Button("⚠  Re-run Tap Test", ImVec2(150, 28))) {
            sync.start_tap_test();
            Notifications::get().info("Tap test running — tap the bar sharply");
        }
        ImGui::PopStyleColor(3);
        ImGui::SameLine();
    }

    // ────── Right side: live status pills ──────
    float right_x = ImGui::GetWindowWidth() - 720;
    if (right_x < ImGui::GetCursorPosX() + 20) right_x = ImGui::GetCursorPosX() + 20;
    ImGui::SetCursorPosX(right_x);
    ImGui::SetCursorPosY(ImGui::GetCursorPosY() + 2);  // vertical center

    char buf[64];
    if (imu.is_running()) {
        snprintf(buf, sizeof(buf), "IMU  %.0f Hz", imu_st.measured_rate_hz);
        status_pill(buf, kPillGreen);
    } else {
        status_pill("IMU  offline", kPillRed);
    }
    ImGui::SameLine();

    if (cam.is_running()) {
        snprintf(buf, sizeof(buf), "Cam  %.0f fps", cam_st.measured_fps);
        status_pill(buf, kPillGreen);
    } else {
        status_pill("Cam  offline", kPillRed);
    }
    ImGui::SameLine();

    // Sync pill prefers the live FSYNC-rate from the IMU (hardware truth)
    // over the offline tap-test offset. If FSYNC ticks at the camera rate, we
    // are physically synced regardless of whether the user has run a tap test.
    if (sync.rearm_required()) {
        status_pill("Sync  REARM", kPillRed);
    } else if (imu.is_running() && cam.is_running() && imu_st.fsync_rate_hz > 5.0) {
        snprintf(buf, sizeof(buf), "Sync  %.0f Hz HW", imu_st.fsync_rate_hz);
        status_pill(buf, kPillGreen);
    } else if (sync_r.valid) {
        snprintf(buf, sizeof(buf), "Sync  %.1f ppm", sync.get_current_drift_ppm());
        status_pill(buf, kPillGreen);
    } else if (imu.is_running() && cam.is_running()) {
        status_pill("Sync  waiting", kPillAmber);  // both up but no edges yet
    } else {
        status_pill("Sync  offline", kPillGray);
    }
    ImGui::SameLine();

    if (cam.is_running()) {
        float dr = trk_st.detection_rate;
        ImVec4 c = dr > 0.95f ? kPillGreen : dr > 0.80f ? kPillAmber : kPillRed;
        snprintf(buf, sizeof(buf), "Marker  %.0f%%", dr * 100.0f);
        status_pill(buf, c);
    }
}

// Helper: two-column metric row — left label, right value (right-aligned, bold)
static void metric(const char* label, const char* value, ImVec4 value_col = ImVec4(0.92f,0.93f,0.95f,1)) {
    ImGui::TextDisabled("%s", label);
    ImGui::SameLine();
    float rx = ImGui::GetContentRegionMax().x;
    ImVec2 sz = ImGui::CalcTextSize(value);
    ImGui::SameLine(rx - sz.x - 2);
    ImGui::PushStyleColor(ImGuiCol_Text, value_col);
    ImGui::TextUnformatted(value);
    ImGui::PopStyleColor();
}

void MainWindow::render_sensor_section() {
    auto& session = app_.session();

    // ─── IMU card ───────────────────────────────────────────────
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.13f, 0.14f, 0.18f, 1.0f));
    ImGui::BeginChild("##imu_card", ImVec2(0, 0), ImGuiChildFlags_AutoResizeY | ImGuiChildFlags_Border);
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.85f, 1.0f, 1.0f));
    ImGui::Text("IMU  ICM42688-P");
    ImGui::PopStyleColor();
    ImGui::Separator();
    auto& imu = session.imu();
    auto imu_stats = imu.get_stats();
    char buf[64];

    if (imu.is_running()) {
        snprintf(buf, sizeof(buf), "%.1f Hz", imu_stats.measured_rate_hz);
        metric("rate", buf, ImVec4(0.4f, 1.0f, 0.5f, 1));

        snprintf(buf, sizeof(buf), "%.0f us", imu_stats.jitter_us_mean);
        metric("jitter", buf);

        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)imu_stats.valid_packets);
        metric("samples", buf);

        snprintf(buf, sizeof(buf), "%llu / %llu", (unsigned long long)imu_stats.crc_errors,
                                                  (unsigned long long)imu_stats.dropouts);
        ImVec4 ec = (imu_stats.crc_errors + imu_stats.dropouts > 0) ?
                     ImVec4(1.0f, 0.6f, 0.3f, 1) : ImVec4(0.92f,0.93f,0.95f,1);
        metric("crc / drop", buf, ec);

        auto sample = imu.get_latest_sample();
        float mag = sqrtf(sample.accel_x_g*sample.accel_x_g +
                          sample.accel_y_g*sample.accel_y_g +
                          sample.accel_z_g*sample.accel_z_g);
        snprintf(buf, sizeof(buf), "%.3f g", mag);
        metric("|a|", buf);

        float gmag = sqrtf(sample.gyro_x_dps*sample.gyro_x_dps +
                           sample.gyro_y_dps*sample.gyro_y_dps +
                           sample.gyro_z_dps*sample.gyro_z_dps);
        snprintf(buf, sizeof(buf), "%.1f dps", gmag);
        metric("|w|", buf);

        snprintf(buf, sizeof(buf), "%.1f C", sample.temperature_c);
        metric("temp", buf);

        ImGui::Spacing();
        if (ImGui::Button("Calibrate Gyro Bias", ImVec2(-1, 0))) {
            imu.start_gyro_bias_calibration(5000);
        }
        if (imu.is_calibrating()) {
            ImGui::TextColored(ImVec4(1, 1, 0, 1), "Calibrating...");
        }
    } else {
        ImGui::TextDisabled("not connected");
        ImGui::TextDisabled("port: %s", app_.config().imu.port.c_str());
        ImGui::TextDisabled("(use top toolbar to connect)");
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();

    ImGui::Spacing();

    // ─── Camera card ────────────────────────────────────────────
    ImGui::PushStyleColor(ImGuiCol_ChildBg, ImVec4(0.13f, 0.14f, 0.18f, 1.0f));
    ImGui::BeginChild("##cam_card", ImVec2(0, 0), ImGuiChildFlags_AutoResizeY | ImGuiChildFlags_Border);
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.55f, 0.85f, 1.0f, 1.0f));
    ImGui::Text("Camera  D455");
    ImGui::PopStyleColor();
    ImGui::Separator();
    auto& cam = session.camera();
    auto cam_stats = cam.get_stats();
    if (cam.is_running()) {
        snprintf(buf, sizeof(buf), "%.0f fps", cam_stats.measured_fps);
        metric("rate", buf, ImVec4(0.4f, 1.0f, 0.5f, 1));

        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)cam_stats.total_frames);
        metric("frames", buf);

        snprintf(buf, sizeof(buf), "%llu", (unsigned long long)cam_stats.dropped_frames);
        ImVec4 dc = cam_stats.dropped_frames > 0 ?
                     ImVec4(1.0f, 0.6f, 0.3f, 1) : ImVec4(0.92f,0.93f,0.95f,1);
        metric("dropped", buf, dc);

        auto tstat = session.tracker().get_stats();
        float dr = tstat.detection_rate;
        ImVec4 tc = dr > 0.95f ? ImVec4(0.4f, 1.0f, 0.5f, 1) :
                    dr > 0.80f ? ImVec4(1.0f, 0.85f, 0.3f, 1) :
                                 ImVec4(1.0f, 0.4f, 0.3f, 1);
        snprintf(buf, sizeof(buf), "%.0f%%", dr * 100.0f);
        metric("marker det", buf, tc);

        snprintf(buf, sizeof(buf), "%.1f", tstat.avg_snr);
        metric("marker SNR", buf);

        ImGui::Spacing();
        ImGui::TextDisabled("SN: %s", cam.get_serial().c_str());
    } else {
        ImGui::TextDisabled("not connected");
        ImGui::TextDisabled("D455 over USB 3.x required");
        ImGui::TextDisabled("(use top toolbar to connect)");
    }
    ImGui::EndChild();
    ImGui::PopStyleColor();
}

void MainWindow::render_sync_section() {
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("Synchronization");
    ImGui::PopStyleColor();

    // Hardware FSYNC tagging is the only sync path now — tap-test logic
    // removed per user request. Live FSYNC rate from IMU's TEMP-LSB tag is
    // the canonical sync indicator.
    auto& session = app_.session();
    const auto& imu_st = session.imu().get_stats();
    bool hw_sync_live = imu_st.fsync_rate_hz > 5.0;

    if (hw_sync_live) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● HW Synced (FSYNC)");
        ImGui::Text("FSYNC rate: %.1f Hz", imu_st.fsync_rate_hz);
        ImGui::Text("FSYNC hits: %llu", (unsigned long long)imu_st.fsync_hit_count);
    } else if (session.imu().is_running() && session.camera().is_running()) {
        ImGui::TextColored(ImVec4(1, 1, 0, 1), "○ Waiting for FSYNC pulses");
        ImGui::TextDisabled("Both ESPs powered? Cat5e to camera SYNC pin?");
    } else {
        ImGui::TextColored(ImVec4(0.6f, 0.6f, 0.6f, 1), "● Hardware offline");
    }
}

void MainWindow::render_status_bar() {
    ImGuiIO& io = ImGui::GetIO();
    ImGuiWindowFlags flags = ImGuiWindowFlags_NoDecoration | ImGuiWindowFlags_NoInputs
                           | ImGuiWindowFlags_NoNav | ImGuiWindowFlags_NoMove
                           | ImGuiWindowFlags_NoBringToFrontOnFocus;
    float bar_h = ImGui::GetFrameHeightWithSpacing();
    ImGui::SetNextWindowPos(ImVec2(0, io.DisplaySize.y - bar_h));
    ImGui::SetNextWindowSize(ImVec2(io.DisplaySize.x, bar_h));
    ImGui::SetNextWindowBgAlpha(0.92f);
    ImGui::PushStyleColor(ImGuiCol_WindowBg, ImVec4(0.12f, 0.12f, 0.16f, 1.0f));
    if (ImGui::Begin("##StatusBar", nullptr, flags)) {
        auto& session = app_.session();
        auto stats = session.get_recording_stats();

        ImGui::TextDisabled("VBT v%s · git %s", kAppVersion, kGitSha);
        ImGui::SameLine(220);
        ImGui::Text("Duration: %.1fs", stats.duration_s);
        ImGui::SameLine(360);
        ImGui::Text("IMU: %llu",  (unsigned long long)stats.imu_samples);
        ImGui::SameLine(470);
        ImGui::Text("Camera: %llu", (unsigned long long)stats.camera_frames);
        ImGui::SameLine(610);
        ImGui::Text("Track: %.0f%%", stats.tracking_rate * 100.0f);
        ImGui::SameLine(io.DisplaySize.x - 130);
        ImGui::Text("%.0f FPS", io.Framerate);
    }
    ImGui::End();
    ImGui::PopStyleColor();
}

void MainWindow::apply_style() {
    // We have proper TTF fonts loaded in Application::init_imgui — no
    // FontGlobalScale (which makes the bitmap font blurry).
    ImGuiStyle& s = ImGui::GetStyle();

    s.WindowPadding     = ImVec2(14, 12);
    s.FramePadding      = ImVec2(10, 6);
    s.ItemSpacing       = ImVec2(10, 8);
    s.ItemInnerSpacing  = ImVec2(8, 5);
    s.IndentSpacing     = 20;
    s.ScrollbarSize     = 14;
    s.GrabMinSize       = 12;

    s.WindowBorderSize  = 1.0f;
    s.FrameBorderSize   = 0.0f;
    s.PopupBorderSize   = 1.0f;
    s.TabBorderSize     = 0.0f;

    s.WindowRounding    = 6.0f;
    s.FrameRounding     = 6.0f;
    s.PopupRounding     = 6.0f;
    s.ScrollbarRounding = 8.0f;
    s.GrabRounding      = 6.0f;
    s.TabRounding       = 6.0f;
    s.ChildRounding     = 6.0f;

    // Tokyo-Night–inspired slate base + cyan/amber accents.
    // Window backgrounds are slightly lighter than the screen background so cards
    // stand out, with darker child bg for nested cards.
    ImVec4* c = s.Colors;
    const ImVec4 BG_DEEP   = ImVec4(0.060f, 0.070f, 0.094f, 1.00f);  // page bg
    const ImVec4 BG_PANEL  = ImVec4(0.094f, 0.106f, 0.137f, 1.00f);  // panel surface
    const ImVec4 BG_CARD   = ImVec4(0.122f, 0.137f, 0.176f, 1.00f);  // card surface
    const ImVec4 BG_INPUT  = ImVec4(0.078f, 0.090f, 0.118f, 1.00f);  // input/frame
    const ImVec4 ACCENT    = ImVec4(0.376f, 0.808f, 0.945f, 1.00f);  // cyan #60CEF1
    const ImVec4 ACCENT_HI = ImVec4(0.580f, 0.890f, 0.980f, 1.00f);
    const ImVec4 BORDER    = ImVec4(0.180f, 0.200f, 0.250f, 1.00f);
    const ImVec4 TEXT      = ImVec4(0.945f, 0.953f, 0.969f, 1.00f);
    const ImVec4 TEXT_DIM  = ImVec4(0.580f, 0.620f, 0.690f, 1.00f);

    c[ImGuiCol_Text]                  = TEXT;
    c[ImGuiCol_TextDisabled]          = TEXT_DIM;
    c[ImGuiCol_WindowBg]              = BG_PANEL;
    c[ImGuiCol_ChildBg]               = BG_CARD;
    c[ImGuiCol_PopupBg]               = BG_PANEL;
    c[ImGuiCol_Border]                = BORDER;
    c[ImGuiCol_FrameBg]               = BG_INPUT;
    c[ImGuiCol_FrameBgHovered]        = ImVec4(0.110f, 0.130f, 0.170f, 1.00f);
    c[ImGuiCol_FrameBgActive]         = ImVec4(0.140f, 0.165f, 0.215f, 1.00f);
    c[ImGuiCol_TitleBg]               = BG_DEEP;
    c[ImGuiCol_TitleBgActive]         = BG_PANEL;
    c[ImGuiCol_MenuBarBg]             = BG_DEEP;
    c[ImGuiCol_ScrollbarBg]           = BG_DEEP;
    c[ImGuiCol_ScrollbarGrab]         = ImVec4(0.220f, 0.250f, 0.310f, 1.0f);
    c[ImGuiCol_ScrollbarGrabHovered]  = ImVec4(0.290f, 0.330f, 0.410f, 1.0f);
    c[ImGuiCol_ScrollbarGrabActive]   = ImVec4(0.360f, 0.420f, 0.520f, 1.0f);
    c[ImGuiCol_CheckMark]             = ACCENT;
    c[ImGuiCol_SliderGrab]            = ACCENT;
    c[ImGuiCol_SliderGrabActive]      = ACCENT_HI;
    c[ImGuiCol_Button]                = ImVec4(0.165f, 0.200f, 0.255f, 1.0f);
    c[ImGuiCol_ButtonHovered]         = ImVec4(0.220f, 0.270f, 0.345f, 1.0f);
    c[ImGuiCol_ButtonActive]          = ImVec4(0.275f, 0.345f, 0.435f, 1.0f);
    c[ImGuiCol_Header]                = ImVec4(0.165f, 0.200f, 0.255f, 1.0f);
    c[ImGuiCol_HeaderHovered]         = ImVec4(0.220f, 0.270f, 0.345f, 1.0f);
    c[ImGuiCol_HeaderActive]          = ImVec4(0.275f, 0.345f, 0.435f, 1.0f);
    c[ImGuiCol_Separator]             = BORDER;
    c[ImGuiCol_SeparatorHovered]      = ACCENT;
    c[ImGuiCol_SeparatorActive]       = ACCENT_HI;
    c[ImGuiCol_Tab]                   = BG_INPUT;
    c[ImGuiCol_TabHovered]            = ImVec4(0.180f, 0.230f, 0.310f, 1.0f);
    c[ImGuiCol_TabActive]             = ImVec4(0.200f, 0.260f, 0.340f, 1.0f);
    c[ImGuiCol_TableHeaderBg]         = ImVec4(0.140f, 0.165f, 0.215f, 1.0f);
    c[ImGuiCol_TableBorderStrong]     = BORDER;
    c[ImGuiCol_TableBorderLight]      = ImVec4(0.150f, 0.170f, 0.215f, 1.0f);
    c[ImGuiCol_TableRowBg]            = ImVec4(0.000f, 0.000f, 0.000f, 0.000f);
    c[ImGuiCol_TableRowBgAlt]         = ImVec4(0.105f, 0.120f, 0.155f, 0.500f);
    c[ImGuiCol_PlotLines]             = ACCENT;
    c[ImGuiCol_PlotHistogram]         = ACCENT;
    c[ImGuiCol_NavHighlight]          = ACCENT;
}

} // namespace vbt

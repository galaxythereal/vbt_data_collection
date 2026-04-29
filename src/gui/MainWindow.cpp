/**
 * @file MainWindow.cpp
 * @brief ImGui main window with professional tiled layout.
 */
#include "gui/MainWindow.h"
#include "gui/SensorPanel.h"
#include "gui/SessionPanel.h"
#include "gui/PlotPanel.h"
#include "gui/CalibrationPanel.h"
#include "gui/AnnotationPanel.h"
#include "gui/ValidationPanel.h"
#include "gui/CameraPanel.h"
#include "gui/PreflightPanel.h"
#include "gui/OperatorView.h"
#include "gui/RepTimelinePanel.h"
#include "gui/CalibrationWizard.h"
#include "gui/ReplayMode.h"
#include "app/Application.h"
#include "app/Config.h"
#include "app/Version.h"
#include "core/Session.h"
#include "core/CalibrationManager.h"
#include "utils/Notifications.h"
#include "utils/AudioCue.h"
#include "utils/DiagnosticExport.h"
#include <imgui.h>
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
}

MainWindow::MainWindow(Application& app) : app_(app) {
    sensor_panel_     = std::make_unique<SensorPanel>(app);
    session_panel_    = std::make_unique<SessionPanel>(app);
    plot_panel_       = std::make_unique<PlotPanel>(app);
    calib_panel_      = std::make_unique<CalibrationPanel>(app);
    annotation_panel_ = std::make_unique<AnnotationPanel>(app);
    validation_panel_ = std::make_unique<ValidationPanel>(app);
    camera_panel_     = std::make_unique<CameraPanel>(app);
    preflight_        = std::make_unique<PreflightPanel>(app);
    operator_view_    = std::make_unique<OperatorView>(app);
    rep_timeline_     = std::make_unique<RepTimelinePanel>(app);
    calib_wizard_     = std::make_unique<CalibrationWizard>(app, shared_calib_mgr());
    replay_           = std::make_unique<ReplayMode>(app);
}

MainWindow::~MainWindow() = default;

void MainWindow::process_hotkeys() {
    ImGuiIO& io = ImGui::GetIO();
    // Don't trigger when typing into a text field
    if (io.WantTextInput) return;

    auto& sess = app_.session();

    // F12 — toggle operator view
    if (ImGui::IsKeyPressed(ImGuiKey_F12, false)) {
        show_operator_view_ = !show_operator_view_;
    }
    // F1 — toggle calibration wizard
    if (ImGui::IsKeyPressed(ImGuiKey_F1, false)) {
        if (calib_wizard_->is_open()) { /* no-op, user can close via X */ }
        else calib_wizard_->open();
    }
    // F2 — toggle replay
    if (ImGui::IsKeyPressed(ImGuiKey_F2, false)) {
        replay_->open();
    }
    // F5 — diagnostic export
    if (ImGui::IsKeyPressed(ImGuiKey_F5, false)) {
        export_diagnostic_bundle(app_, "./diagnostics");
    }
    // Space — start/stop recording (only when state allows)
    if (ImGui::IsKeyPressed(ImGuiKey_Space, false)) {
        if (sess.get_state() == SessionState::CONFIGURED ||
            sess.get_state() == SessionState::READY) {
            if (show_preflight_) {
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
        if (sess.get_state() == SessionState::STOPPED) sess.save();
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
            " unfinalised session(s) found. Tools → Recovery to inspect.");
        for (const auto& p : orphans) ImGui::OpenPopup("##recovery");
    }
}

void MainWindow::render() {
    static bool style_applied = false;
    if (!style_applied) { apply_style(); style_applied = true; }

    process_hotkeys();
    check_orphaned_partials_once();

    // Operator view replaces normal UI when active
    if (show_operator_view_) {
        operator_view_->render();
        Notifications::get().render();
        return;
    }

    render_menu_bar();

    ImGuiIO& io = ImGui::GetIO();
    float W = io.DisplaySize.x;
    float H = io.DisplaySize.y;
    float menu_h = ImGui::GetFrameHeight();
    float status_h = ImGui::GetFrameHeightWithSpacing();
    float body_y = menu_h;
    float body_h = H - menu_h - status_h;

    float left_w   = 290.0f;
    float right_w  = 310.0f;
    float center_w = W - left_w - right_w;
    if (center_w < 400.0f) {
        left_w = 240.0f; right_w = 260.0f;
        center_w = W - left_w - right_w;
    }

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
    float plot_h = body_h * 0.58f;
    float annot_h = body_h - plot_h;

    ImGui::SetNextWindowPos(ImVec2(left_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(center_w, plot_h));
    ImGui::Begin("Live Visualization", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    plot_panel_->render_content();
    ImGui::End();

    ImGui::SetNextWindowPos(ImVec2(left_w, body_y + plot_h));
    ImGui::SetNextWindowSize(ImVec2(center_w, annot_h));
    ImGui::Begin("Rep Annotations", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    ImGui::Checkbox("Timeline view", &use_rep_timeline_);
    ImGui::SameLine();
    ImGui::TextDisabled("(F12: operator view  F1: calib  F2: replay)");
    ImGui::Separator();
    if (use_rep_timeline_) rep_timeline_->render_content();
    else                    annotation_panel_->render_content();
    ImGui::End();

    // RIGHT
    float session_h = body_h * 0.45f;
    float valid_h   = body_h * 0.25f;
    float camera_h  = body_h - session_h - valid_h;

    ImGui::SetNextWindowPos(ImVec2(left_w + center_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(right_w, session_h));
    ImGui::Begin("Session Control", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    session_panel_->render_content();
    if (session_panel_->consume_preflight_request()) {
        show_preflight_ = true;
    }
    ImGui::End();

    ImGui::SetNextWindowPos(ImVec2(left_w + center_w, body_y + session_h));
    ImGui::SetNextWindowSize(ImVec2(right_w, valid_h));
    ImGui::Begin("Validation Metrics", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    validation_panel_->render_content();
    ImGui::End();

    ImGui::SetNextWindowPos(ImVec2(left_w + center_w, body_y + session_h + valid_h));
    ImGui::SetNextWindowSize(ImVec2(right_w, camera_h));
    ImGui::Begin("Camera Settings", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    camera_panel_->render_content();
    ImGui::End();

    // Wizards / modals
    if (show_calib_window_) {
        ImGui::SetNextWindowSize(ImVec2(500, 550), ImGuiCond_FirstUseEver);
        calib_panel_->render();
    }
    calib_wizard_->render();
    replay_->render();
    render_preflight_modal();

    if (show_demo_window_) ImGui::ShowDemoWindow(&show_demo_window_);

    // Audio cue: rep counter incremented?
    int rep_now = (int)app_.session().segmenter().get_reps().size();
    if (rep_now > prev_rep_count_) {
        AudioCue::play(Cue::LiftOff);
        prev_rep_count_ = rep_now;
    } else if (rep_now < prev_rep_count_) {
        prev_rep_count_ = rep_now;  // a rep was deleted manually
    }

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

        ImGui::Spacing();
        ImGui::Separator();
        ImGui::BeginDisabled(!ok);
        if (ImGui::Button("Start Recording", ImVec2(180, 32))) {
            app_.session().start_recording();
            AudioCue::play(Cue::StartRecord);
            show_preflight_ = false;
            ImGui::CloseCurrentPopup();
        }
        ImGui::EndDisabled();
        ImGui::SameLine();
        if (ImGui::Button("Override + Start", ImVec2(160, 32))) {
            preflight_->record_override("operator overrode failed pre-flight");
            app_.session().start_recording();
            AudioCue::play(Cue::StartRecord);
            show_preflight_ = false;
            ImGui::CloseCurrentPopup();
        }
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
            if (ImGui::MenuItem("Replay Saved Session...", "F2"))  replay_->open();
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
        if (ImGui::BeginMenu("View")) {
            if (ImGui::MenuItem("Operator View (big numbers)", "F12",
                                show_operator_view_)) {
                show_operator_view_ = !show_operator_view_;
            }
            ImGui::MenuItem("Rep Timeline (vs. table)", nullptr, &use_rep_timeline_);
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
            ImGui::Text("| %.1fs | IMU:%llu | Cam:%llu | Reps:%d",
                        stats.duration_s, (unsigned long long)stats.imu_samples,
                        (unsigned long long)stats.camera_frames, stats.rep_count);
        } else {
            ImGui::TextDisabled("○ %s", session.get_state_string().c_str());
        }
        ImGui::EndMainMenuBar();
    }
}

void MainWindow::render_sensor_section() {
    auto& session = app_.session();

    // IMU
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("IMU (ICM42688-P)");
    ImGui::PopStyleColor();

    auto& imu = session.imu();
    auto imu_stats = imu.get_stats();

    if (imu.is_running()) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Connected");
        ImGui::SameLine();
        ImGui::TextDisabled("%.0f Hz", imu_stats.measured_rate_hz);

        ImGui::Text("Valid: %llu | CRC: %llu | Drop: %llu",
                    (unsigned long long)imu_stats.valid_packets,
                    (unsigned long long)imu_stats.crc_errors,
                    (unsigned long long)imu_stats.dropouts);
        ImGui::Text("Sat A:%llu  G:%llu  Noise(g):%.4f  Noise(dps):%.3f",
                    (unsigned long long)imu_stats.accel_saturation_count,
                    (unsigned long long)imu_stats.gyro_saturation_count,
                    imu_stats.accel_noise_floor_g,
                    imu_stats.gyro_noise_floor_dps);

        auto sample = imu.get_latest_sample();
        float mag = sqrtf(sample.accel_x_g*sample.accel_x_g +
                          sample.accel_y_g*sample.accel_y_g +
                          sample.accel_z_g*sample.accel_z_g);
        ImGui::Text("Accel: %.3f %.3f %.3f (%.3fg)",
                    sample.accel_x_g, sample.accel_y_g, sample.accel_z_g, mag);
        ImGui::Text("Gyro:  %.1f %.1f %.1f dps",
                    sample.gyro_x_dps, sample.gyro_y_dps, sample.gyro_z_dps);
        ImGui::Text("Temp:  %.1f °C | Jitter: %.0f µs",
                    sample.temperature_c, imu_stats.jitter_us_mean);

        if (ImGui::Button("Calibrate Gyro", ImVec2(-1, 0))) {
            imu.start_gyro_bias_calibration(5000);
        }
        if (imu.is_calibrating()) {
            ImGui::TextColored(ImVec4(1, 1, 0, 1), "Calibrating...");
        }
    } else {
        ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "● Disconnected");
        static char port_buf[64];
        strncpy(port_buf, app_.config().imu.port.c_str(), sizeof(port_buf)-1);
        if (ImGui::InputText("Port", port_buf, sizeof(port_buf))) {
            app_.config().imu.port = port_buf;
        }
        if (ImGui::Button("Connect IMU", ImVec2(-1, 0))) {
            if (imu.open(app_.config().imu)) {
                imu.start();
                Notifications::get().success("IMU connected on " + app_.config().imu.port);
            } else {
                Notifications::get().error(
                    "Failed to open IMU on " + app_.config().imu.port +
                    ". Check the cable, port name, and permissions "
                    "(udev rules or `dialout` group on Linux).");
            }
        }
    }

    ImGui::Spacing();

    // Camera
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("Camera (D455)");
    ImGui::PopStyleColor();

    auto& cam = session.camera();
    auto cam_stats = cam.get_stats();

    if (cam.is_running()) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Connected");
        ImGui::SameLine();
        ImGui::TextDisabled("SN:%s", cam.get_serial().c_str());
        ImGui::Text("Frames: %llu (dropped: %llu)",
                    (unsigned long long)cam_stats.total_frames,
                    (unsigned long long)cam_stats.dropped_frames);

        auto tstat = session.tracker().get_stats();
        float dr = tstat.detection_rate;
        ImVec4 tc = dr > 0.95f ? ImVec4(0.2f,1,0.3f,1) :
                    dr > 0.80f ? ImVec4(1,0.8f,0.2f,1) :
                                 ImVec4(1,0.3f,0.2f,1);
        ImGui::TextColored(tc, "Marker: %.0f%%", dr * 100.0f);
        ImGui::SameLine();
        ImGui::Text("SNR:%.1f", tstat.avg_snr);
    } else {
        ImGui::TextColored(ImVec4(1, 0.3f, 0.3f, 1), "● Disconnected");
        if (ImGui::Button("Connect Camera", ImVec2(-1, 0))) {
            if (cam.open(app_.config().camera)) {
                session.tracker().configure(app_.config().camera);
                cam.start();
                Notifications::get().success("RealSense camera connected");
            } else {
                Notifications::get().error(
                    "RealSense not found. Plug in the D455, run "
                    "`rs-enumerate-devices` to verify, then click again.");
            }
        }
    }
}

void MainWindow::render_sync_section() {
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("Synchronization");
    ImGui::PopStyleColor();

    auto& session = app_.session();
    auto sync_result = session.sync().get_sync_result();
    bool rearm = session.sync().rearm_required();
    if (rearm) {
        ImGui::TextColored(ImVec4(1,0.30f,0.30f,1), "● REARM REQUIRED");
    } else if (sync_result.valid) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Synced");
    } else {
        ImGui::TextColored(ImVec4(1, 1, 0, 1), "○ Not calibrated");
    }
    if (sync_result.valid) {
        ImGui::Text("Offset: %.1f µs", sync_result.offset_us);
        ImGui::Text("Drift: %.1f ppm", session.sync().get_current_drift_ppm());
    }

    if (ImGui::Button("Run Tap Test", ImVec2(-1, 0))) {
        session.sync().start_tap_test();
        Notifications::get().info("Tap test running — tap the bar sharply");
    }
    if (session.sync().is_tap_test_active()) {
        ImGui::TextColored(ImVec4(1, 1, 0, 1), "TAP the barbell now!");
        if (ImGui::Button("Finish", ImVec2(-1, 0))) {
            auto r = session.sync().finish_tap_test();
            if (r.valid) {
                Notifications::get().success("Tap test complete");
                session.sync().clear_rearm();
            } else {
                Notifications::get().warn("Tap test inconclusive — try again");
            }
        }
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
        ImGui::SameLine(710);
        ImGui::Text("Reps: %d", stats.rep_count);
        ImGui::SameLine(io.DisplaySize.x - 130);
        ImGui::Text("%.0f FPS", io.Framerate);
    }
    ImGui::End();
    ImGui::PopStyleColor();
}

void MainWindow::apply_style() {
    ImGuiStyle& s = ImGui::GetStyle();

    s.WindowPadding     = ImVec2(10, 10);
    s.FramePadding      = ImVec2(8, 4);
    s.ItemSpacing       = ImVec2(8, 5);
    s.ItemInnerSpacing  = ImVec2(6, 4);
    s.IndentSpacing     = 18;
    s.ScrollbarSize     = 12;
    s.GrabMinSize       = 10;

    s.WindowBorderSize  = 1.0f;
    s.FrameBorderSize   = 0.0f;
    s.PopupBorderSize   = 1.0f;
    s.TabBorderSize     = 0.0f;

    s.WindowRounding    = 4.0f;
    s.FrameRounding     = 4.0f;
    s.PopupRounding     = 4.0f;
    s.ScrollbarRounding = 6.0f;
    s.GrabRounding      = 4.0f;
    s.TabRounding       = 4.0f;

    ImVec4* c = s.Colors;
    c[ImGuiCol_Text]                  = ImVec4(0.92f, 0.93f, 0.95f, 1.00f);
    c[ImGuiCol_TextDisabled]          = ImVec4(0.50f, 0.52f, 0.55f, 1.00f);
    c[ImGuiCol_WindowBg]              = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_ChildBg]               = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_PopupBg]               = ImVec4(0.13f, 0.13f, 0.17f, 1.00f);
    c[ImGuiCol_Border]                = ImVec4(0.22f, 0.23f, 0.28f, 1.00f);
    c[ImGuiCol_FrameBg]               = ImVec4(0.16f, 0.16f, 0.20f, 1.00f);
    c[ImGuiCol_FrameBgHovered]        = ImVec4(0.22f, 0.22f, 0.28f, 1.00f);
    c[ImGuiCol_FrameBgActive]         = ImVec4(0.28f, 0.28f, 0.36f, 1.00f);
    c[ImGuiCol_TitleBg]               = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_TitleBgActive]         = ImVec4(0.14f, 0.14f, 0.18f, 1.00f);
    c[ImGuiCol_MenuBarBg]             = ImVec4(0.12f, 0.12f, 0.15f, 1.00f);
    c[ImGuiCol_ScrollbarBg]           = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_ScrollbarGrab]         = ImVec4(0.30f, 0.30f, 0.38f, 1.00f);
    c[ImGuiCol_ScrollbarGrabHovered]  = ImVec4(0.40f, 0.40f, 0.50f, 1.00f);
    c[ImGuiCol_ScrollbarGrabActive]   = ImVec4(0.50f, 0.50f, 0.60f, 1.00f);
    c[ImGuiCol_CheckMark]             = ImVec4(0.40f, 0.75f, 1.00f, 1.00f);
    c[ImGuiCol_SliderGrab]            = ImVec4(0.35f, 0.65f, 1.00f, 1.00f);
    c[ImGuiCol_SliderGrabActive]      = ImVec4(0.50f, 0.80f, 1.00f, 1.00f);
    c[ImGuiCol_Button]                = ImVec4(0.20f, 0.22f, 0.28f, 1.00f);
    c[ImGuiCol_ButtonHovered]         = ImVec4(0.28f, 0.32f, 0.42f, 1.00f);
    c[ImGuiCol_ButtonActive]          = ImVec4(0.35f, 0.40f, 0.55f, 1.00f);
    c[ImGuiCol_Header]                = ImVec4(0.18f, 0.20f, 0.26f, 1.00f);
    c[ImGuiCol_HeaderHovered]         = ImVec4(0.26f, 0.30f, 0.40f, 1.00f);
    c[ImGuiCol_HeaderActive]          = ImVec4(0.30f, 0.36f, 0.48f, 1.00f);
    c[ImGuiCol_Separator]             = ImVec4(0.25f, 0.27f, 0.33f, 1.00f);
    c[ImGuiCol_SeparatorHovered]      = ImVec4(0.40f, 0.60f, 1.00f, 0.80f);
    c[ImGuiCol_SeparatorActive]       = ImVec4(0.40f, 0.65f, 1.00f, 1.00f);
    c[ImGuiCol_Tab]                   = ImVec4(0.16f, 0.16f, 0.20f, 1.00f);
    c[ImGuiCol_TabHovered]            = ImVec4(0.28f, 0.40f, 0.65f, 0.80f);
    c[ImGuiCol_TableHeaderBg]         = ImVec4(0.16f, 0.18f, 0.22f, 1.00f);
    c[ImGuiCol_TableBorderStrong]     = ImVec4(0.24f, 0.26f, 0.32f, 1.00f);
    c[ImGuiCol_TableBorderLight]      = ImVec4(0.20f, 0.22f, 0.28f, 1.00f);
    c[ImGuiCol_TableRowBg]            = ImVec4(0.00f, 0.00f, 0.00f, 0.00f);
    c[ImGuiCol_TableRowBgAlt]         = ImVec4(0.14f, 0.14f, 0.18f, 0.60f);
    c[ImGuiCol_PlotLines]             = ImVec4(0.40f, 0.75f, 1.00f, 1.00f);
    c[ImGuiCol_PlotHistogram]         = ImVec4(0.40f, 0.75f, 1.00f, 1.00f);
}

} // namespace vbt

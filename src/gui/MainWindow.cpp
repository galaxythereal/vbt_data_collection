/**
 * @file MainWindow.cpp
 * @brief ImGui main window with professional tiled layout.
 *
 * Layout (no docking — manual positioning):
 * ┌──────────────────────────────────────────────────────────────┐
 * │                        Menu Bar                              │
 * ├──────────────┬────────────────────────────┬──────────────────┤
 * │  LEFT COLUMN │     CENTER COLUMN          │  RIGHT COLUMN    │
 * │  (~280px)    │     (remainder)            │  (~300px)        │
 * │              │                            │                  │
 * │ Sensor       │  Live Plots (top 55%)      │ Session Control  │
 * │ Status       │                            │                  │
 * │              ├────────────────────────────┤ Validation       │
 * │ Sync &       │  Rep Annotations (bot 45%) │ Metrics          │
 * │ Calibration  │                            │                  │
 * ├──────────────┴────────────────────────────┴──────────────────┤
 * │                     Status Bar                               │
 * └──────────────────────────────────────────────────────────────┘
 */
#include "gui/MainWindow.h"
#include "gui/SensorPanel.h"
#include "gui/SessionPanel.h"
#include "gui/PlotPanel.h"
#include "gui/CalibrationPanel.h"
#include "gui/AnnotationPanel.h"
#include "gui/ValidationPanel.h"
#include "gui/CameraPanel.h"
#include "app/Application.h"
#include "app/Config.h"
#include <imgui.h>
#include <cstdio>

namespace vbt {
bool save_config(const std::string& path, const AppConfig& config);

MainWindow::MainWindow(Application& app) : app_(app) {
    sensor_panel_ = std::make_unique<SensorPanel>(app);
    session_panel_ = std::make_unique<SessionPanel>(app);
    plot_panel_ = std::make_unique<PlotPanel>(app);
    calib_panel_ = std::make_unique<CalibrationPanel>(app);
    annotation_panel_ = std::make_unique<AnnotationPanel>(app);
    validation_panel_ = std::make_unique<ValidationPanel>(app);
    camera_panel_ = std::make_unique<CameraPanel>(app);
}

MainWindow::~MainWindow() = default;

void MainWindow::render() {
    // Apply custom style on first frame
    static bool style_applied = false;
    if (!style_applied) {
        apply_style();
        style_applied = true;
    }

    render_menu_bar();

    // Compute layout dimensions
    ImGuiIO& io = ImGui::GetIO();
    float W = io.DisplaySize.x;
    float H = io.DisplaySize.y;
    float menu_h = ImGui::GetFrameHeight();            // ~22px
    float status_h = ImGui::GetFrameHeightWithSpacing(); // ~26px
    float body_y = menu_h;
    float body_h = H - menu_h - status_h;

    float left_w   = 290.0f;
    float right_w  = 310.0f;
    float center_w = W - left_w - right_w;
    if (center_w < 400.0f) {
        // Narrow screen: reduce side panels
        left_w = 240.0f; right_w = 260.0f;
        center_w = W - left_w - right_w;
    }

    // ==========================================================================
    // LEFT COLUMN: Sensor Status + Calibration
    // ==========================================================================
    ImGui::SetNextWindowPos(ImVec2(0, body_y));
    ImGui::SetNextWindowSize(ImVec2(left_w, body_h));
    ImGui::Begin("##LeftColumn", nullptr,
                 ImGuiWindowFlags_NoTitleBar | ImGuiWindowFlags_NoResize |
                 ImGuiWindowFlags_NoMove | ImGuiWindowFlags_NoCollapse);

    render_sensor_section();
    ImGui::Spacing();
    ImGui::Spacing();
    render_sync_section();

    ImGui::End();

    // ==========================================================================
    // CENTER COLUMN: Plots + Annotations
    // ==========================================================================
    float plot_h = body_h * 0.58f;
    float annot_h = body_h - plot_h;

    // Live Plots (top)
    ImGui::SetNextWindowPos(ImVec2(left_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(center_w, plot_h));
    ImGui::Begin("Live Visualization", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    plot_panel_->render_content();
    ImGui::End();

    // Annotations (bottom)
    ImGui::SetNextWindowPos(ImVec2(left_w, body_y + plot_h));
    ImGui::SetNextWindowSize(ImVec2(center_w, annot_h));
    ImGui::Begin("Rep Annotations", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    annotation_panel_->render_content();
    ImGui::End();

    // ==========================================================================
    // RIGHT COLUMN: Session Control + Validation + Camera
    // ==========================================================================
    float session_h = body_h * 0.45f;
    float valid_h   = body_h * 0.25f;
    float camera_h  = body_h - session_h - valid_h;

    ImGui::SetNextWindowPos(ImVec2(left_w + center_w, body_y));
    ImGui::SetNextWindowSize(ImVec2(right_w, session_h));
    ImGui::Begin("Session Control", nullptr,
                 ImGuiWindowFlags_NoResize | ImGuiWindowFlags_NoMove |
                 ImGuiWindowFlags_NoCollapse);
    session_panel_->render_content();
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

    // ==========================================================================
    // CALIBRATION POPUP
    // ==========================================================================
    if (show_calib_window_) {
        ImGui::SetNextWindowSize(ImVec2(500, 550), ImGuiCond_FirstUseEver);
        calib_panel_->render();
    }

    if (show_demo_window_) ImGui::ShowDemoWindow(&show_demo_window_);

    render_status_bar();
}

// ==========================================================================
// Menu bar
// ==========================================================================
void MainWindow::render_menu_bar() {
    if (ImGui::BeginMainMenuBar()) {
        if (ImGui::BeginMenu("File")) {
            if (ImGui::MenuItem("Save Config", "Ctrl+S")) {
                save_config("vbt_config.json", app_.config());
            }
            ImGui::Separator();
            if (ImGui::MenuItem("Quit", "Ctrl+Q")) {
                app_.shutdown();
            }
            ImGui::EndMenu();
        }
        if (ImGui::BeginMenu("Tools")) {
            ImGui::MenuItem("Calibration", nullptr, &show_calib_window_);
            ImGui::Separator();
            ImGui::MenuItem("ImGui Demo", nullptr, &show_demo_window_);
            ImGui::EndMenu();
        }

        // Right-aligned recording indicator
        auto& session = app_.session();
        float right_start = ImGui::GetWindowWidth() - 400;
        ImGui::SameLine(right_start);

        if (session.get_state() == SessionState::RECORDING) {
            // Blinking red dot
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
            ImGui::Text("| %.1fs | IMU:%lu | Cam:%lu | Reps:%d",
                        stats.duration_s, stats.imu_samples,
                        stats.camera_frames, stats.rep_count);
        } else {
            ImGui::TextDisabled("○ %s", session.get_state_string().c_str());
        }

        ImGui::EndMainMenuBar();
    }
}

// ==========================================================================
// Sensor section (embedded in left column)
// ==========================================================================
void MainWindow::render_sensor_section() {
    auto& session = app_.session();

    // ── IMU ──
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("IMU (ICM42688-P)");
    ImGui::PopStyleColor();

    auto& imu = session.imu();
    auto imu_stats = imu.get_stats();

    if (imu.is_running()) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Connected");
        ImGui::SameLine();
        ImGui::TextDisabled("%.0f Hz", imu_stats.measured_rate_hz);

        ImGui::Text("Valid: %lu | CRC: %lu | Drop: %lu",
                    imu_stats.valid_packets, imu_stats.crc_errors, imu_stats.dropouts);

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
            if (imu.open(app_.config().imu)) imu.start();
        }
    }

    ImGui::Spacing();

    // ── Camera ──
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("Camera (D455)");
    ImGui::PopStyleColor();

    auto& cam = session.camera();
    auto cam_stats = cam.get_stats();

    if (cam.is_running()) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Connected");
        ImGui::SameLine();
        ImGui::TextDisabled("SN:%s", cam.get_serial().c_str());
        ImGui::Text("Frames: %lu (dropped: %lu)", cam_stats.total_frames, cam_stats.dropped_frames);

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
            }
        }
    }
}

// ==========================================================================
// Sync section (embedded in left column)
// ==========================================================================
void MainWindow::render_sync_section() {
    ImGui::PushStyleColor(ImGuiCol_Text, ImVec4(0.4f, 0.8f, 1.0f, 1.0f));
    ImGui::SeparatorText("Synchronization");
    ImGui::PopStyleColor();

    auto& session = app_.session();
    auto sync_result = session.sync().get_sync_result();
    if (sync_result.valid) {
        ImGui::TextColored(ImVec4(0.2f, 1, 0.3f, 1), "● Synced");
        ImGui::Text("Offset: %.1f µs", sync_result.offset_us);
        ImGui::Text("Drift: %.1f ppm", session.sync().get_current_drift_ppm());
    } else {
        ImGui::TextColored(ImVec4(1, 1, 0, 1), "○ Not calibrated");
    }
    if (ImGui::Button("Run Tap Test", ImVec2(-1, 0))) {
        session.sync().start_tap_test();
    }
    if (session.sync().is_tap_test_active()) {
        ImGui::TextColored(ImVec4(1, 1, 0, 1), "TAP the barbell now!");
        if (ImGui::Button("Finish", ImVec2(-1, 0))) {
            session.sync().finish_tap_test();
        }
    }
}

// ==========================================================================
// Status bar (bottom)
// ==========================================================================
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

        ImGui::TextDisabled("VBT Data Collection v1.0");
        ImGui::SameLine(200);
        ImGui::Text("Duration: %.1fs", stats.duration_s);
        ImGui::SameLine(350);
        ImGui::Text("IMU: %lu", stats.imu_samples);
        ImGui::SameLine(460);
        ImGui::Text("Camera: %lu", stats.camera_frames);
        ImGui::SameLine(600);
        ImGui::Text("Track: %.0f%%", stats.tracking_rate * 100.0f);
        ImGui::SameLine(700);
        ImGui::Text("Reps: %d", stats.rep_count);
        ImGui::SameLine(io.DisplaySize.x - 120);
        ImGui::Text("%.0f FPS", io.Framerate);
    }
    ImGui::End();
    ImGui::PopStyleColor();
}

// ==========================================================================
// Premium dark theme
// ==========================================================================
void MainWindow::apply_style() {
    ImGuiStyle& s = ImGui::GetStyle();

    // Geometry
    s.WindowPadding     = ImVec2(10, 10);
    s.FramePadding      = ImVec2(8, 4);
    s.ItemSpacing       = ImVec2(8, 5);
    s.ItemInnerSpacing  = ImVec2(6, 4);
    s.IndentSpacing     = 18;
    s.ScrollbarSize     = 12;
    s.GrabMinSize       = 10;

    // Borders
    s.WindowBorderSize  = 1.0f;
    s.FrameBorderSize   = 0.0f;
    s.PopupBorderSize   = 1.0f;
    s.TabBorderSize     = 0.0f;

    // Rounding
    s.WindowRounding    = 4.0f;
    s.FrameRounding     = 4.0f;
    s.PopupRounding     = 4.0f;
    s.ScrollbarRounding = 6.0f;
    s.GrabRounding      = 4.0f;
    s.TabRounding       = 4.0f;

    // Colors: sleek dark blue-gray theme
    ImVec4* c = s.Colors;
    c[ImGuiCol_Text]                  = ImVec4(0.92f, 0.93f, 0.95f, 1.00f);
    c[ImGuiCol_TextDisabled]          = ImVec4(0.50f, 0.52f, 0.55f, 1.00f);
    c[ImGuiCol_WindowBg]              = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_ChildBg]               = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_PopupBg]               = ImVec4(0.13f, 0.13f, 0.17f, 1.00f);
    c[ImGuiCol_Border]                = ImVec4(0.22f, 0.23f, 0.28f, 1.00f);
    c[ImGuiCol_BorderShadow]          = ImVec4(0.00f, 0.00f, 0.00f, 0.00f);
    c[ImGuiCol_FrameBg]               = ImVec4(0.16f, 0.16f, 0.20f, 1.00f);
    c[ImGuiCol_FrameBgHovered]        = ImVec4(0.22f, 0.22f, 0.28f, 1.00f);
    c[ImGuiCol_FrameBgActive]         = ImVec4(0.28f, 0.28f, 0.36f, 1.00f);
    c[ImGuiCol_TitleBg]               = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
    c[ImGuiCol_TitleBgActive]         = ImVec4(0.14f, 0.14f, 0.18f, 1.00f);
    c[ImGuiCol_TitleBgCollapsed]      = ImVec4(0.10f, 0.10f, 0.13f, 1.00f);
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

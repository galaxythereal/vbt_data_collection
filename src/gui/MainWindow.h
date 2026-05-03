#pragma once

/**
 * @file MainWindow.h
 * @brief ImGui main window with professional tiled layout, plus the new
 *        wizards/overlays (operator view, calibration wizard, replay,
 *        pre-flight checklist).
 */

#include <memory>

namespace vbt {

class Application;
class SensorPanel;
class SessionPanel;
class PlotPanel;
class CalibrationPanel;
class AnnotationPanel;
class ValidationPanel;
class CameraPanel;
class PreflightPanel;
class OperatorView;
class RepTimelinePanel;
class CalibrationWizard;
class ReplayMode;

class MainWindow {
public:
    explicit MainWindow(Application& app);
    ~MainWindow();

    void render();

    // Hotkey handlers (called from Application before ImGui::NewFrame)
    void process_hotkeys();

private:
    void render_menu_bar();
    void render_top_toolbar();        // primary CTAs + live hardware status
    void render_sensor_section();
    void render_sync_section();
    void render_status_bar();
    void render_preflight_modal();
    void render_recovery_modal();
    void apply_style();
    void check_orphaned_partials_once();

    Application& app_;

    std::unique_ptr<SensorPanel>      sensor_panel_;
    std::unique_ptr<SessionPanel>     session_panel_;
    std::unique_ptr<PlotPanel>        plot_panel_;
    std::unique_ptr<CalibrationPanel> calib_panel_;
    std::unique_ptr<AnnotationPanel>  annotation_panel_;
    std::unique_ptr<ValidationPanel>  validation_panel_;
    std::unique_ptr<CameraPanel>      camera_panel_;
    std::unique_ptr<PreflightPanel>   preflight_;
    std::unique_ptr<OperatorView>     operator_view_;
    std::unique_ptr<RepTimelinePanel> rep_timeline_;
    std::unique_ptr<CalibrationWizard> calib_wizard_;
    std::unique_ptr<ReplayMode>       replay_;

    bool show_demo_window_   = false;
    bool show_calib_window_  = false;
    bool show_preflight_     = false;
    bool show_operator_view_ = false;
    bool use_rep_timeline_   = false;    // hot-toggle: false = autoscroll table (default)
    bool checked_partials_   = false;
    int  prev_rep_count_     = 0;        // for lift-off audio cue
};

} // namespace vbt

#pragma once

/**
 * @file MainWindow.h
 * @brief ImGui main window with professional tiled layout.
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

class MainWindow {
public:
    explicit MainWindow(Application& app);
    ~MainWindow();

    void render();

private:
    void render_menu_bar();
    void render_sensor_section();
    void render_sync_section();
    void render_status_bar();
    void apply_style();

    Application& app_;

    std::unique_ptr<SensorPanel>      sensor_panel_;
    std::unique_ptr<SessionPanel>     session_panel_;
    std::unique_ptr<PlotPanel>        plot_panel_;
    std::unique_ptr<CalibrationPanel> calib_panel_;
    std::unique_ptr<AnnotationPanel>  annotation_panel_;
    std::unique_ptr<ValidationPanel>  validation_panel_;
    std::unique_ptr<CameraPanel>      camera_panel_;

    bool show_demo_window_ = false;
    bool show_calib_window_ = false;
};

} // namespace vbt

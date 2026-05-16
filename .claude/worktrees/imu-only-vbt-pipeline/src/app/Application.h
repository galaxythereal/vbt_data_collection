#pragma once

/**
 * @file Application.h
 * @brief Main application orchestrator.
 *
 * Owns the GLFW/OpenGL window, ImGui context, and coordinates
 * all subsystems (sensors, session, GUI panels).
 */

#include <string>
#include <memory>
#include "app/Config.h"
#include "core/Session.h"

struct GLFWwindow;

namespace vbt {

class MainWindow;

class Application {
public:
    Application();
    ~Application();

    // Initialize (create window, load config)
    bool init(int argc = 0, char** argv = nullptr);

    // Main loop
    void run();

    // Shutdown
    void shutdown();

    // Access
    AppConfig& config() { return config_; }
    const AppConfig& config() const { return config_; }
    Session& session() { return *session_; }
    const Session& session() const { return *session_; }

private:
    bool init_window();
    bool init_imgui();
    void main_loop_tick();
    void cleanup();

    GLFWwindow* window_ = nullptr;
    int window_width_  = 1920;
    int window_height_ = 1080;

    AppConfig config_;
    std::unique_ptr<Session> session_;
    std::unique_ptr<MainWindow> main_window_;

    bool running_ = false;
};

} // namespace vbt

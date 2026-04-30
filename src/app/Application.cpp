/**
 * @file Application.cpp
 * @brief Main application with GLFW window and ImGui context.
 */
#include "app/Application.h"
#include "app/Config.h"
#include "app/Version.h"
#include "gui/MainWindow.h"
#include "gui/Fonts.h"
#include "utils/AudioCue.h"
#include <spdlog/spdlog.h>
#include <imgui.h>
#include <imgui_impl_glfw.h>
#include <imgui_impl_opengl3.h>
#include <implot.h>
#include <GLFW/glfw3.h>
#include <fstream>
#include <filesystem>

namespace vbt {
// Forward declarations for config helpers (defined in Config.cpp)
bool load_config(const std::string& path, AppConfig& config);
bool save_config(const std::string& path, const AppConfig& config);

// Font globals (defined in gui/Fonts.h, populated during init_imgui)
ImFont* g_font_default = nullptr;
ImFont* g_font_title   = nullptr;
ImFont* g_font_mono    = nullptr;
ImFont* g_font_metric  = nullptr;

Application::Application() = default;
Application::~Application() { shutdown(); }

bool Application::init(int argc, char** argv) {
    // Banner — pin build provenance to the log
    spdlog::info("VBT v{} | git {} ({}) | built {}",
                 kAppVersion, kGitSha, kGitBranch, kBuildTimestamp);

    // Load or create default config
    std::string config_path = "vbt_config.json";
    if (argc > 1) config_path = argv[1];
    load_config(config_path, config_);

    AudioCue::set_enabled(config_.enable_audio_cues);

    session_ = std::make_unique<Session>();

    if (!init_window()) return false;
    if (!init_imgui()) return false;

    main_window_ = std::make_unique<MainWindow>(*this);

    running_ = true;
    spdlog::info("Application initialized");
    return true;
}

bool Application::init_window() {
    if (!glfwInit()) {
        spdlog::error("GLFW init failed");
        return false;
    }
    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
    glfwWindowHint(GLFW_MAXIMIZED, GLFW_TRUE);

    window_ = glfwCreateWindow(window_width_, window_height_,
                                "VBT Data Collection System v1.0", nullptr, nullptr);
    if (!window_) {
        spdlog::error("GLFW window creation failed");
        glfwTerminate();
        return false;
    }
    glfwMakeContextCurrent(window_);
    glfwSwapInterval(1);  // VSync
    return true;
}

bool Application::init_imgui() {
    IMGUI_CHECKVERSION();
    ImGui::CreateContext();
    ImPlot::CreateContext();

    ImGuiIO& io = ImGui::GetIO();
    io.ConfigFlags |= ImGuiConfigFlags_NavEnableKeyboard;

    // ── Load proper TrueType fonts (replaces blurry Proggy default) ──
    // Try a few common Linux paths, fall back to ImGui's bundled font.
    auto try_load = [&](const char* path, float px) -> ImFont* {
        for (const char* prefix : {"", "/usr/share/fonts/TTF/", "/usr/share/fonts/truetype/dejavu/",
                                   "/usr/share/fonts/noto/"}) {
            std::string p = std::string(prefix) + path;
            if (std::filesystem::exists(p)) return io.Fonts->AddFontFromFileTTF(p.c_str(), px);
        }
        return nullptr;
    };
    g_font_default = try_load("DejaVuSans.ttf",       16.0f);
    g_font_title   = try_load("DejaVuSans-Bold.ttf",  24.0f);
    g_font_mono    = try_load("DejaVuSansMono.ttf",   15.0f);
    g_font_metric  = try_load("DejaVuSans-Bold.ttf",  20.0f);
    if (!g_font_default) io.Fonts->AddFontDefault();

    // Dark theme with custom colors
    ImGui::StyleColorsDark();
    ImGuiStyle& style = ImGui::GetStyle();
    style.WindowRounding = 6.0f;
    style.FrameRounding = 4.0f;
    style.GrabRounding = 4.0f;
    style.TabRounding = 4.0f;
    style.ScrollbarRounding = 6.0f;
    style.WindowPadding = ImVec2(10, 10);
    style.FramePadding = ImVec2(8, 4);
    style.ItemSpacing = ImVec2(8, 6);

    // Custom accent colors (scientific blue theme)
    auto& colors = style.Colors;
    colors[ImGuiCol_WindowBg] = ImVec4(0.08f, 0.08f, 0.12f, 1.0f);
    colors[ImGuiCol_Header] = ImVec4(0.15f, 0.20f, 0.35f, 1.0f);
    colors[ImGuiCol_HeaderHovered] = ImVec4(0.20f, 0.28f, 0.48f, 1.0f);
    colors[ImGuiCol_HeaderActive] = ImVec4(0.22f, 0.32f, 0.55f, 1.0f);
    colors[ImGuiCol_Button] = ImVec4(0.15f, 0.22f, 0.40f, 1.0f);
    colors[ImGuiCol_ButtonHovered] = ImVec4(0.20f, 0.30f, 0.55f, 1.0f);
    colors[ImGuiCol_ButtonActive] = ImVec4(0.25f, 0.38f, 0.65f, 1.0f);
    colors[ImGuiCol_FrameBg] = ImVec4(0.10f, 0.12f, 0.18f, 1.0f);
    colors[ImGuiCol_Tab] = ImVec4(0.12f, 0.15f, 0.25f, 1.0f);
    colors[ImGuiCol_TabHovered] = ImVec4(0.20f, 0.28f, 0.48f, 1.0f);
    colors[ImGuiCol_TabActive] = ImVec4(0.18f, 0.25f, 0.42f, 1.0f);
    colors[ImGuiCol_TitleBg] = ImVec4(0.06f, 0.06f, 0.09f, 1.0f);
    colors[ImGuiCol_TitleBgActive] = ImVec4(0.10f, 0.14f, 0.25f, 1.0f);
    colors[ImGuiCol_PlotLines] = ImVec4(0.30f, 0.70f, 1.0f, 1.0f);
    colors[ImGuiCol_PlotHistogram] = ImVec4(0.30f, 0.70f, 1.0f, 1.0f);
    colors[ImGuiCol_CheckMark] = ImVec4(0.30f, 0.80f, 0.50f, 1.0f);

    ImGui_ImplGlfw_InitForOpenGL(window_, true);
    ImGui_ImplOpenGL3_Init("#version 330");

    return true;
}

void Application::run() {
    while (running_ && !glfwWindowShouldClose(window_)) {
        main_loop_tick();
    }
}

void Application::main_loop_tick() {
    glfwPollEvents();

    ImGui_ImplOpenGL3_NewFrame();
    ImGui_ImplGlfw_NewFrame();
    ImGui::NewFrame();

    // Render all panels
    main_window_->render();

    ImGui::Render();
    int w, h;
    glfwGetFramebufferSize(window_, &w, &h);
    glViewport(0, 0, w, h);
    glClearColor(0.06f, 0.06f, 0.09f, 1.0f);
    glClear(GL_COLOR_BUFFER_BIT);
    ImGui_ImplOpenGL3_RenderDrawData(ImGui::GetDrawData());

    glfwSwapBuffers(window_);
}

void Application::shutdown() {
    if (!running_) return;
    running_ = false;

    save_config("vbt_config.json", config_);

    main_window_.reset();

    ImGui_ImplOpenGL3_Shutdown();
    ImGui_ImplGlfw_Shutdown();
    ImPlot::DestroyContext();
    ImGui::DestroyContext();

    if (window_) {
        glfwDestroyWindow(window_);
        window_ = nullptr;
    }
    glfwTerminate();
    spdlog::info("Application shutdown complete");
}

} // namespace vbt

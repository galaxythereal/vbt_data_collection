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
#include <vector>
#include <algorithm>

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

#ifdef __APPLE__
    // macOS requires a forward-compatible core profile context.
    // 3.2 is the most widely supported baseline across Macs.
    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 2);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
    glfwWindowHint(GLFW_OPENGL_FORWARD_COMPAT, GL_TRUE);
    glfwWindowHint(GLFW_COCOA_RETINA_FRAMEBUFFER, GL_TRUE);
#else
    glfwWindowHint(GLFW_CONTEXT_VERSION_MAJOR, 3);
    glfwWindowHint(GLFW_CONTEXT_VERSION_MINOR, 3);
    glfwWindowHint(GLFW_OPENGL_PROFILE, GLFW_OPENGL_CORE_PROFILE);
#endif
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

    // ── Fonts + Retina / HiDPI handling ─────────────────────────────────────
    // ImGui coordinates are in logical window units, but the framebuffer can be
    // higher-res (Retina). If we keep a low-res font atlas, the renderer will
    // upscale it and text looks blurry. The fix is: build fonts at (px * scale)
    // and scale them back down at runtime.
    float xscale = 1.0f, yscale = 1.0f;
    if (window_) glfwGetWindowContentScale(window_, &xscale, &yscale);
    int win_w = 0, win_h = 0, fb_w = 0, fb_h = 0;
    if (window_) {
        glfwGetWindowSize(window_, &win_w, &win_h);
        glfwGetFramebufferSize(window_, &fb_w, &fb_h);
    }
    float fb_scale = (win_w > 0) ? (float)fb_w / (float)win_w : 1.0f;
    float dpi_scale = std::max(1.0f, std::max(std::max(xscale, yscale), fb_scale));
    spdlog::info("ImGui DPI: window_scale=({:.2f},{:.2f}) fb_scale={:.2f} -> dpi_scale={:.2f}",
                 xscale, yscale, fb_scale, dpi_scale);

    ImFontConfig font_cfg;
    font_cfg.OversampleH = 3;
    font_cfg.OversampleV = 2;
    font_cfg.RasterizerMultiply = 1.0f;

    auto try_load_any = [&](const std::vector<std::string>& candidates, float px) -> ImFont* {
        for (const auto& p : candidates) {
            if (!p.empty() && std::filesystem::exists(p)) {
                if (auto* f = io.Fonts->AddFontFromFileTTF(p.c_str(), px, &font_cfg)) {
                    spdlog::info("Loaded font: {} @ {:.1f}px", p, px);
                    return f;
                }
            }
        }
        return nullptr;
    };

#ifdef __APPLE__
    // Prefer SF Pro / SF Mono (present on modern macOS), fall back to common system fonts.
    const std::vector<std::string> mac_sans = {
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
        "/System/Library/Fonts/Helvetica.ttc",
        "/System/Library/Fonts/LucidaGrande.ttc",
    };
    const std::vector<std::string> mac_title = {
        "/System/Library/Fonts/SFNSRounded.ttf",
        "/System/Library/Fonts/SFNS.ttf",
        "/System/Library/Fonts/HelveticaNeue.ttc",
    };
    const std::vector<std::string> mac_mono = {
        "/System/Library/Fonts/SFNSMono.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "/System/Library/Fonts/Monaco.ttf",
        "/System/Library/Fonts/Courier.ttc",
    };

    g_font_default = try_load_any(mac_sans,  16.0f * dpi_scale);
    g_font_title   = try_load_any(mac_title, 24.0f * dpi_scale);
    g_font_mono    = try_load_any(mac_mono,  15.0f * dpi_scale);
    g_font_metric  = try_load_any(mac_title, 20.0f * dpi_scale);
#else
    // Linux defaults (DejaVu) — keep the old behavior.
    auto linux_paths = [&](const char* filename) {
        return std::vector<std::string>{
            std::string(filename),
            std::string("/usr/share/fonts/TTF/") + filename,
            std::string("/usr/share/fonts/truetype/dejavu/") + filename,
            std::string("/usr/share/fonts/noto/") + filename,
        };
    };

    g_font_default = try_load_any(linux_paths("DejaVuSans.ttf"),      16.0f * dpi_scale);
    g_font_title   = try_load_any(linux_paths("DejaVuSans-Bold.ttf"), 24.0f * dpi_scale);
    g_font_mono    = try_load_any(linux_paths("DejaVuSansMono.ttf"),  15.0f * dpi_scale);
    g_font_metric  = try_load_any(linux_paths("DejaVuSans-Bold.ttf"), 20.0f * dpi_scale);
#endif

    if (!g_font_default) {
        spdlog::warn("No system TTF font found — using ImGui default bitmap font (may look blurry on HiDPI)");
        g_font_default = io.Fonts->AddFontDefault();
    }
    if (!g_font_title)  g_font_title  = g_font_default;
    if (!g_font_mono)   g_font_mono   = g_font_default;
    if (!g_font_metric) g_font_metric = g_font_default;

    // Scale fonts back down so they render at the intended logical size,
    // while remaining crisp on HiDPI displays.
    if (dpi_scale > 1.0f) {
        const float inv = 1.0f / dpi_scale;
        if (g_font_default) g_font_default->Scale = inv;
        if (g_font_title)   g_font_title->Scale   = inv;
        if (g_font_mono)    g_font_mono->Scale    = inv;
        if (g_font_metric)  g_font_metric->Scale  = inv;
    }
    io.FontDefault = g_font_default;

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

#ifdef __APPLE__
    // GLSL 150 matches OpenGL 3.2 core profile on macOS.
    ImGui_ImplOpenGL3_Init("#version 150");
#else
    ImGui_ImplOpenGL3_Init("#version 330");
#endif

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

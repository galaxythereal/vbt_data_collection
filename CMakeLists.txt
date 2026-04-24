cmake_minimum_required(VERSION 3.20)
project(vbt_data_collection VERSION 1.0.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ==============================================================================
# Options
# ==============================================================================
option(BUILD_TESTS "Build unit tests" ON)

# ==============================================================================
# Dependencies
# ==============================================================================

# OpenGL
find_package(OpenGL REQUIRED)

# GLFW (for ImGui backend)
find_package(glfw3 3.3 REQUIRED)

# RealSense2
find_package(realsense2 REQUIRED)

# OpenCV (for marker detection)
find_package(OpenCV 4.0 REQUIRED COMPONENTS core imgproc highgui videoio)

# Threads
find_package(Threads REQUIRED)

# Eigen3 (for math)
find_package(Eigen3 REQUIRED)

# ==============================================================================
# Third-party (header-only or bundled)
# ==============================================================================

# nlohmann/json
include(FetchContent)

FetchContent_Declare(json
    GIT_REPOSITORY https://github.com/nlohmann/json.git
    GIT_TAG        v3.11.3
)

FetchContent_Declare(spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG        v1.13.0
)

FetchContent_Declare(imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG        v1.90.4
)

FetchContent_Declare(implot
    GIT_REPOSITORY https://github.com/epezent/implot.git
    GIT_TAG        v0.16
)

FetchContent_MakeAvailable(json spdlog)

# ImGui needs manual setup since it's not CMake-native
FetchContent_GetProperties(imgui)
if(NOT imgui_POPULATED)
    FetchContent_Populate(imgui)
endif()

FetchContent_GetProperties(implot)
if(NOT implot_POPULATED)
    FetchContent_Populate(implot)
endif()

# Build ImGui as a library
add_library(imgui_lib STATIC
    ${imgui_SOURCE_DIR}/imgui.cpp
    ${imgui_SOURCE_DIR}/imgui_demo.cpp
    ${imgui_SOURCE_DIR}/imgui_draw.cpp
    ${imgui_SOURCE_DIR}/imgui_tables.cpp
    ${imgui_SOURCE_DIR}/imgui_widgets.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_glfw.cpp
    ${imgui_SOURCE_DIR}/backends/imgui_impl_opengl3.cpp
)
target_include_directories(imgui_lib PUBLIC
    ${imgui_SOURCE_DIR}
    ${imgui_SOURCE_DIR}/backends
)
target_link_libraries(imgui_lib PUBLIC glfw OpenGL::GL)

# Build ImPlot as a library
add_library(implot_lib STATIC
    ${implot_SOURCE_DIR}/implot.cpp
    ${implot_SOURCE_DIR}/implot_items.cpp
)
target_include_directories(implot_lib PUBLIC ${implot_SOURCE_DIR})
target_link_libraries(implot_lib PUBLIC imgui_lib)

# ==============================================================================
# Main Application
# ==============================================================================
set(APP_SOURCES
    src/main.cpp
    src/app/Application.cpp
    src/app/Config.cpp
    src/core/Session.cpp
    src/core/SyncEngine.cpp
    src/core/DataLogger.cpp
    src/core/CalibrationManager.cpp
    src/sensors/IMUReader.cpp
    src/sensors/CameraReader.cpp
    src/sensors/MarkerTracker.cpp
    src/processing/RepSegmenter.cpp
    src/processing/OrientationFilter.cpp
    src/processing/Validator.cpp
    src/gui/MainWindow.cpp
    src/gui/SensorPanel.cpp
    src/gui/SessionPanel.cpp
    src/gui/PlotPanel.cpp
    src/gui/CalibrationPanel.cpp
    src/gui/AnnotationPanel.cpp
    src/gui/ValidationPanel.cpp
    src/utils/SerialPort.cpp
    src/utils/Timer.cpp
)

add_executable(${PROJECT_NAME} ${APP_SOURCES})

target_include_directories(${PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    imgui_lib
    implot_lib
    nlohmann_json::nlohmann_json
    spdlog::spdlog
    realsense2::realsense2
    ${OpenCV_LIBS}
    Eigen3::Eigen
    Threads::Threads
)

# ==============================================================================
# Tests
# ==============================================================================
if(BUILD_TESTS)
    enable_testing()
    # Tests will be added as we build them
endif()

# ==============================================================================
# Install
# ==============================================================================
install(TARGETS ${PROJECT_NAME} DESTINATION bin)

cmake_minimum_required(VERSION 3.20)
project(vbt_data_collection VERSION 1.1.0 LANGUAGES CXX)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_EXPORT_COMPILE_COMMANDS ON)

# ==============================================================================
# Options
# ==============================================================================
option(BUILD_TESTS "Build unit tests" ON)
option(VBT_WITH_REALSENSE "Build with Intel RealSense (librealsense2) support" ON)

# ==============================================================================
# Build provenance — capture git SHA, dirty flag, branch, build timestamp
# ==============================================================================
find_package(Git QUIET)
if(GIT_FOUND AND EXISTS "${CMAKE_SOURCE_DIR}/.git")
    execute_process(COMMAND ${GIT_EXECUTABLE} rev-parse --short HEAD
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        OUTPUT_VARIABLE VBT_GIT_SHA OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    execute_process(COMMAND ${GIT_EXECUTABLE} rev-parse --abbrev-ref HEAD
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        OUTPUT_VARIABLE VBT_GIT_BRANCH OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    execute_process(COMMAND ${GIT_EXECUTABLE} status --porcelain
        WORKING_DIRECTORY ${CMAKE_SOURCE_DIR}
        OUTPUT_VARIABLE VBT_GIT_STATUS OUTPUT_STRIP_TRAILING_WHITESPACE ERROR_QUIET)
    if(VBT_GIT_STATUS)
        set(VBT_GIT_DIRTY "true")
    else()
        set(VBT_GIT_DIRTY "false")
    endif()
endif()
if(NOT VBT_GIT_SHA)
    set(VBT_GIT_SHA "unknown")
    set(VBT_GIT_BRANCH "unknown")
    set(VBT_GIT_DIRTY "unknown")
endif()
string(TIMESTAMP VBT_BUILD_TIMESTAMP "%Y-%m-%dT%H:%M:%SZ" UTC)
if(NOT CMAKE_BUILD_TYPE)
    set(CMAKE_BUILD_TYPE "Release")
endif()
configure_file(
    ${CMAKE_SOURCE_DIR}/src/app/Version.h.in
    ${CMAKE_BINARY_DIR}/generated/app/Version.h
    @ONLY)
message(STATUS "VBT build: ${PROJECT_VERSION} | git ${VBT_GIT_SHA} (${VBT_GIT_BRANCH}, dirty=${VBT_GIT_DIRTY}) | ${VBT_BUILD_TIMESTAMP}")

# ==============================================================================
# Dependencies
# ==============================================================================

# OpenGL
find_package(OpenGL REQUIRED)

# GLFW (for ImGui backend)
find_package(glfw3 3.3 REQUIRED)

# RealSense2 (optional for offline annotation/analysis builds)
if(VBT_WITH_REALSENSE)
    find_package(realsense2 REQUIRED)
endif()

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
    GIT_SHALLOW    TRUE
    GIT_PROGRESS   TRUE
)

FetchContent_Declare(spdlog
    GIT_REPOSITORY https://github.com/gabime/spdlog.git
    GIT_TAG        v1.13.0
    GIT_SHALLOW    TRUE
    GIT_PROGRESS   TRUE
)

FetchContent_Declare(imgui
    GIT_REPOSITORY https://github.com/ocornut/imgui.git
    GIT_TAG        v1.90.4
    GIT_SHALLOW    TRUE
    GIT_PROGRESS   TRUE
)

FetchContent_Declare(implot
    GIT_REPOSITORY https://github.com/epezent/implot.git
    GIT_TAG        v0.16
    GIT_SHALLOW    TRUE
    GIT_PROGRESS   TRUE
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
if(VBT_WITH_REALSENSE)
    set(VBT_CAMERA_READER_SRC src/sensors/CameraReader.cpp)
else()
    set(VBT_CAMERA_READER_SRC src/sensors/CameraReaderStub.cpp)
endif()

set(APP_SOURCES
    src/main.cpp
    src/app/Application.cpp
    src/app/Config.cpp
    src/core/Session.cpp
    src/core/SyncEngine.cpp
    src/core/DataLogger.cpp
    src/core/CalibrationManager.cpp
    src/core/EventLog.cpp
    src/sensors/IMUReader.cpp
    ${VBT_CAMERA_READER_SRC}
    src/sensors/MarkerTracker.cpp
    src/processing/RepAnnotation.cpp
    src/processing/StillnessGate.cpp
    src/gui/MainWindow.cpp
    src/gui/SensorPanel.cpp
    src/gui/SessionPanel.cpp
    src/gui/SessionInfoForm.cpp
    src/gui/PlotPanel.cpp
    src/gui/CalibrationPanel.cpp
    src/annotation/AnnotationStudio.cpp
    src/annotation/SessionData.cpp
    src/annotation/SessionLibrary.cpp
    src/annotation/GroundTruthIO.cpp
    src/annotation/VideoCache.cpp
    src/annotation/Persistence.cpp
    src/annotation/TimelinePanel.cpp
    src/annotation/VideoPanel.cpp
    src/annotation/RepTablePanel.cpp
    src/annotation/MetadataPanel.cpp
    src/annotation/MarkerQualityPanel.cpp
    src/utils/SerialPort.cpp
    src/utils/Timer.cpp
    src/utils/AudioCue.cpp
    src/utils/Notifications.cpp
    src/utils/DiagnosticExport.cpp
    src/utils/SubjectRegistry.cpp
)

add_executable(${PROJECT_NAME} ${APP_SOURCES})

target_include_directories(${PROJECT_NAME} PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/src
    ${CMAKE_BINARY_DIR}/generated
)

target_compile_definitions(${PROJECT_NAME} PRIVATE
    VBT_WITH_REALSENSE=$<BOOL:${VBT_WITH_REALSENSE}>
)

target_link_libraries(${PROJECT_NAME} PRIVATE
    imgui_lib
    implot_lib
    nlohmann_json::nlohmann_json
    spdlog::spdlog
    ${OpenCV_LIBS}
    Eigen3::Eigen
    Threads::Threads
)

if(VBT_WITH_REALSENSE)
    target_link_libraries(${PROJECT_NAME} PRIVATE realsense2::realsense2)
endif()

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

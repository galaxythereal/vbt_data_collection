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
    src/gui/PostSessionPanel.cpp
    # src/annotation/ -- the old Annotation Studio. It reads
    # <session>/annotations/rep_segments*.json, of which there are ZERO in the corpus, and
    # raw/ is read-only so it cannot write them either. Every control in it acted on an
    # empty list. Left on disk, no longer built or reachable; the post-session annotation
    # window replaces it.
    src/utils/SerialPort.cpp
    src/utils/Timer.cpp
    src/utils/AudioCue.cpp
    src/utils/Notifications.cpp
    src/utils/DiagnosticExport.cpp
    src/utils/SubjectRegistry.cpp
    # real-time annotator (runs inside the acquisition loop; also used by the studio)
    src/rt_annotator/CausalTracker.cpp
    src/rt_annotator/RtAnnotator.cpp
    src/rt_annotator/RtAnnotationIO.cpp
    # post-session annotator (the button in the app; same code the reference scripts check)
    src/offline/RtsSmoother.cpp
    src/offline/OfflineAnnotator.cpp
    src/offline/OfflinePipeline.cpp
    src/offline/SessionVideo.cpp
    src/offline/AuditImage.cpp
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
# Real-time annotator core + offline replay harness
# ==============================================================================
# src/rt_annotator/ is intentionally dependency-free (no ImGui / OpenCV / RealSense /
# Eigen / JSON) so the exact same code runs inside the acquisition loop and inside the
# replay tool. rt_replay streams a recorded session through it frame by frame with no
# lookahead, which is how the live algorithm is validated against all 84 sessions
# without hardware.
set(RT_ANNOTATOR_SOURCES
    src/rt_annotator/CausalTracker.cpp
    src/rt_annotator/RtAnnotator.cpp
    src/rt_annotator/RtAnnotationIO.cpp
)

add_executable(rt_replay
    tools/rt_replay.cpp
    ${RT_ANNOTATOR_SOURCES}
)
target_include_directories(rt_replay PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_features(rt_replay PRIVATE cxx_std_17)

# Whole-session smoother: the same constant-jerk model as the acquisition tracker, run
# backwards as well as forwards. Offline only -- it needs the future.
add_executable(smooth_session
    tools/smooth_session.cpp
    src/offline/RtsSmoother.cpp
)
target_include_directories(smooth_session PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src)
target_compile_features(smooth_session PRIVATE cxx_std_17)

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

# The post-session pass from the command line. Same OfflinePipeline the app's button
# runs -- used to process sessions recorded before the button existed, and to be checked
# against scripts/reference/annotate_v2.py.
add_executable(offline_pass
    tools/offline_pass.cpp
    src/offline/RtsSmoother.cpp
    src/offline/OfflineAnnotator.cpp
    src/offline/OfflinePipeline.cpp
    src/offline/AuditImage.cpp
    ${RT_ANNOTATOR_SOURCES}
)
target_include_directories(offline_pass PRIVATE ${CMAKE_CURRENT_SOURCE_DIR}/src ${OpenCV_INCLUDE_DIRS})
target_link_libraries(offline_pass PRIVATE nlohmann_json::nlohmann_json ${OpenCV_LIBS})
target_compile_features(offline_pass PRIVATE cxx_std_17)

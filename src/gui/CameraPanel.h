#pragma once

#include "app/Application.h"
#include <imgui.h>
#include <opencv2/core.hpp>
#include <GLFW/glfw3.h>

namespace vbt {

class CameraPanel {
public:
    explicit CameraPanel(Application& app) : app_(app) {}

    ~CameraPanel() {
        if (texture_id_ != 0) {
            glDeleteTextures(1, &texture_id_);
        }
    }

    void render() {
        ImGui::Begin("IR Camera Feed");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        
        if (!session.camera().is_running()) {
            ImGui::TextDisabled("Camera not connected");
            return;
        }

        // Get debug image from marker tracker
        cv::Mat frame = session.tracker().get_debug_image();
        
        if (!frame.empty()) {
            update_texture(frame);
            
// WITH THIS:
ImVec2 avail = ImGui::GetContentRegionAvail();
float aspect = (float)frame.cols / (float)frame.rows;
ImVec2 img_size = (avail.x / aspect <= avail.y)
    ? ImVec2(avail.x, avail.x / aspect)
    : ImVec2(avail.y * aspect, avail.y);
ImGui::Image((void*)(intptr_t)texture_id_, img_size);
        } else {
            ImGui::TextDisabled("No frame data");
        }

        ImGui::Spacing();
        ImGui::SeparatorText("Camera Settings");

        auto& cam_config = app_.config().camera;
        bool changed = false;
        
        ImGui::PushItemWidth(ImGui::GetContentRegionAvail().x * 0.6f);
        changed |= ImGui::SliderInt("Exposure (us)", &cam_config.exposure_us, 10, 5000);
        changed |= ImGui::SliderInt("Gain", &cam_config.gain, 16, 248);
        changed |= ImGui::SliderInt("Marker Threshold", &cam_config.marker_threshold, 50, 254);
        changed |= ImGui::SliderFloat("Min Area", &cam_config.marker_min_area, 5, 100);
        changed |= ImGui::SliderFloat("Max Area", &cam_config.marker_max_area, 50, 2000);
        ImGui::PopItemWidth();

        if (changed) {
            session.tracker().configure(cam_config);
        }
    }

private:
    void update_texture(const cv::Mat& image) {
        if (texture_id_ == 0) {
            glGenTextures(1, &texture_id_);
        }
        
        glBindTexture(GL_TEXTURE_2D, texture_id_);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MIN_FILTER, GL_LINEAR);
        glTexParameteri(GL_TEXTURE_2D, GL_TEXTURE_MAG_FILTER, GL_LINEAR);
        
        // Handle different image formats
        GLenum format = GL_RGB;
        if (image.channels() == 1) {
            format = GL_RED;
            // Map red channel to RGB so it shows as grayscale instead of red
            GLint swizzleMask[] = {GL_RED, GL_RED, GL_RED, GL_ONE};
#ifndef GL_TEXTURE_SWIZZLE_RGBA
#define GL_TEXTURE_SWIZZLE_RGBA 0x8E46
#endif
            glTexParameteriv(GL_TEXTURE_2D, GL_TEXTURE_SWIZZLE_RGBA, swizzleMask);
        } else if (image.channels() == 4) {
            format = GL_RGBA;
        } else if (image.channels() == 3) {
            format = GL_BGR; // OpenCV uses BGR
        }

        glPixelStorei(GL_UNPACK_ALIGNMENT, 1);
        glTexImage2D(GL_TEXTURE_2D, 0, GL_RGB, image.cols, image.rows, 0, format, GL_UNSIGNED_BYTE, image.ptr());
    }

    Application& app_;
    GLuint texture_id_ = 0;
};

} // namespace vbt

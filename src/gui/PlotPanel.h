#pragma once
#include "app/Application.h"
#include <imgui.h>
#include <implot.h>
#include <deque>
#include <cmath>
#include <vector>
#include <string>

namespace vbt {

class PlotPanel {
public:
    explicit PlotPanel(Application& app) : app_(app) {}

    void render() {
        ImGui::Begin("Live Visualization");
        render_content();
        ImGui::End();
    }

    void render_content() {
        auto& session = app_.session();
        if (!session.imu().is_running() && !session.camera().is_running()) {
            ImGui::TextDisabled("Connect sensors to see live data");
            return;
        }

        // Update data buffers
        if (session.imu().is_running()) {
            auto sample = session.imu().get_latest_sample();
            if (sample.valid) {
                double t = sample.host_timestamp_s - t_base_;
                if (t_base_ == 0) { t_base_ = sample.host_timestamp_s; t = 0; }
                push_sample(accel_x_, t, sample.accel_x_g);
                push_sample(accel_y_, t, sample.accel_y_g);
                push_sample(accel_z_, t, sample.accel_z_g);
                push_sample(gyro_x_, t, sample.gyro_x_dps);
                push_sample(gyro_y_, t, sample.gyro_y_dps);
                push_sample(gyro_z_, t, sample.gyro_z_dps);

                float mag = sqrtf(sample.accel_x_g*sample.accel_x_g +
                                  sample.accel_y_g*sample.accel_y_g +
                                  sample.accel_z_g*sample.accel_z_g);
                push_sample(accel_mag_, t, mag);
            }
        }

        float avail_h = ImGui::GetContentRegionAvail().y;
        float plot_h = (avail_h - 30) / 3.0f;
        if (plot_h < 60) plot_h = 60;

        // ── Accelerometer ──
        if (ImPlot::BeginPlot("Accelerometer (g)", ImVec2(-1, plot_h))) {
            ImPlot::SetupAxisLimits(ImAxis_X1, -5, 0, ImGuiCond_Always);
            ImPlot::SetupAxisLimits(ImAxis_Y1, -5, 5, ImGuiCond_Once);
            ImPlot::SetupAxis(ImAxis_X1, "Time (s)");
            ImPlot::SetupAxis(ImAxis_Y1, "g");
            plot_buffer("Ax", accel_x_, ImVec4(1.0f, 0.35f, 0.35f, 1));
            plot_buffer("Ay", accel_y_, ImVec4(0.35f, 1.0f, 0.35f, 1));
            plot_buffer("Az", accel_z_, ImVec4(0.4f, 0.6f, 1.0f, 1));
            ImPlot::EndPlot();
        }

        // ── Gyroscope ──
        if (ImPlot::BeginPlot("Gyroscope (dps)", ImVec2(-1, plot_h))) {
            ImPlot::SetupAxisLimits(ImAxis_X1, -5, 0, ImGuiCond_Always);
            ImPlot::SetupAxisLimits(ImAxis_Y1, -1000, 1000, ImGuiCond_Once);
            ImPlot::SetupAxis(ImAxis_X1, "Time (s)");
            ImPlot::SetupAxis(ImAxis_Y1, "dps");
            plot_buffer("Gx", gyro_x_, ImVec4(1.0f, 0.6f, 0.2f, 1));
            plot_buffer("Gy", gyro_y_, ImVec4(0.5f, 1.0f, 0.2f, 1));
            plot_buffer("Gz", gyro_z_, ImVec4(0.3f, 0.6f, 1.0f, 1));
            ImPlot::EndPlot();
        }

        // ── Accel Magnitude (g) ──
        if (ImPlot::BeginPlot("Accel Magnitude (g)", ImVec2(-1, plot_h))) {
            ImPlot::SetupAxisLimits(ImAxis_X1, -5, 0, ImGuiCond_Always);
            ImPlot::SetupAxisLimits(ImAxis_Y1, 0, 6, ImGuiCond_Once);
            ImPlot::SetupAxis(ImAxis_X1, "Time (s)");
            ImPlot::SetupAxis(ImAxis_Y1, "|a| (g)");
            plot_buffer("|Accel|", accel_mag_, ImVec4(0.9f, 0.5f, 1.0f, 1));
            // Draw 1g reference line
            double ref_x[] = {-5, 0};
            double ref_y[] = {1.0, 1.0};
            ImPlot::PushStyleColor(ImPlotCol_Line, ImVec4(0.4f, 0.4f, 0.4f, 0.8f));
            ImPlot::PlotLine("1g", ref_x, ref_y, 2);
            ImPlot::PopStyleColor();
            ImPlot::EndPlot();
        }
    }

private:
    struct TimeSeries {
        std::deque<double> t;
        std::deque<double> v;
    };

    void push_sample(TimeSeries& ts, double t, double v) {
        ts.t.push_back(t); ts.v.push_back(v);
        // Keep 5.2 seconds of history to fully cover the -5 to 0 plot window
        while (!ts.t.empty() && (t - ts.t.front() > 5.2)) {
            ts.t.pop_front();
            ts.v.pop_front();
        }
    }

    void plot_buffer(const char* label, const TimeSeries& ts, ImVec4 color) {
        if (ts.t.empty()) return;
        double now = ts.t.back();
        std::vector<double> t_off(ts.t.begin(), ts.t.end());
        for (auto& t : t_off) t -= now;
        ImPlot::PushStyleColor(ImPlotCol_Line, color);
        ImPlot::PlotLine(label, t_off.data(), &ts.v[0], (int)ts.t.size());
        ImPlot::PopStyleColor();
    }

    Application& app_;
    double t_base_ = 0;

    TimeSeries accel_x_, accel_y_, accel_z_;
    TimeSeries gyro_x_, gyro_y_, gyro_z_;
    TimeSeries accel_mag_;

    // Label storage for bar chart (needs to survive frame)
    std::vector<std::string> rep_labels_;
};

} // namespace vbt

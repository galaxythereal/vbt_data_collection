#pragma once

// This project can be built in an "offline" mode without librealsense2.
// When VBT_WITH_REALSENSE=0, we provide minimal compatible definitions for
// the few RealSense types/functions used in headers and marker deprojection.

#ifndef VBT_WITH_REALSENSE
#define VBT_WITH_REALSENSE 1
#endif

#if VBT_WITH_REALSENSE

#include <librealsense2/rs.hpp>
#include <librealsense2/rsutil.h>

#else

#include <cstddef>

// Mirror the subset of librealsense2 C-API types we use.
// (We intentionally ignore lens distortion in the stub deprojection.)

typedef enum rs2_distortion {
    RS2_DISTORTION_NONE = 0,
    RS2_DISTORTION_MODIFIED_BROWN_CONRADY = 1,
    RS2_DISTORTION_INVERSE_BROWN_CONRADY = 2,
    RS2_DISTORTION_FTHETA = 3,
    RS2_DISTORTION_BROWN_CONRADY = 4,
    RS2_DISTORTION_KANNALA_BRANDT4 = 5
} rs2_distortion;

typedef struct rs2_intrinsics {
    int width;
    int height;
    float ppx;
    float ppy;
    float fx;
    float fy;
    rs2_distortion model;
    float coeffs[5];
} rs2_intrinsics;

#ifndef RS2_API_VERSION_STR
#define RS2_API_VERSION_STR "realsense-disabled"
#endif

// Minimal pinhole deprojection (no distortion correction).
inline void rs2_deproject_pixel_to_point(float point[3], const rs2_intrinsics* intrin,
                                        const float pixel[2], float depth) {
    if (!intrin || intrin->fx == 0.0f || intrin->fy == 0.0f) {
        point[0] = point[1] = 0.0f;
        point[2] = depth;
        return;
    }
    const float x = (pixel[0] - intrin->ppx) / intrin->fx;
    const float y = (pixel[1] - intrin->ppy) / intrin->fy;
    point[0] = depth * x;
    point[1] = depth * y;
    point[2] = depth;
}

// Dummy rs2 namespace types so headers compile without librealsense2.
namespace rs2 {
class pipeline {};
class pipeline_profile {};
class config {};
}  // namespace rs2

#endif

#include "mag_force.h"
#include <math.h>
#include <stdio.h>
static float clampf_local(float v, float min_v, float max_v)
{
    if (v < min_v)
    {
        return min_v;
    }

    if (v > max_v)
    {
        return max_v;
    }

    return v;
}

static float deadzonef_local(float v, float dz)
{
    if (fabsf(v) < dz)
    {
        return 0.0f;
    }

    if (v > 0.0f)
    {
        return v - dz;
    }

    return v + dz;
}

void MAG_FORCE_Calc3DNoCalib(
    const MagForce_Mag3D_t delta[2][2],
    MagForce_Vec3_t *out)
{
    if (out == 0)
    {
        return;
    }

    /*
     * Sensor layout:
     *
     * delta[1][0] = S3, left-top
     * delta[1][1] = S4, right-top
     * delta[0][0] = S1, left-bottom
     * delta[0][1] = S2, right-bottom
     */

    float bx1 = delta[0][0].x;
    float by1 = delta[0][0].y;
    float bz1 = delta[0][0].z;

    float bx2 = delta[0][1].x;
    float by2 = delta[0][1].y;
    float bz2 = delta[0][1].z;

    float bx3 = delta[1][0].x;
    float by3 = delta[1][0].y;
    float bz3 = delta[1][0].z;

    float bx4 = delta[1][1].x;
    float by4 = delta[1][1].y;
    float bz4 = delta[1][1].z;

    float b1 = sqrtf(bx1 * bx1 + by1 * by1 + bz1 * bz1);
    float b2 = sqrtf(bx2 * bx2 + by2 * by2 + bz2 * bz2);
    float b3 = sqrtf(bx3 * bx3 + by3 * by3 + bz3 * bz3);
    float b4 = sqrtf(bx4 * bx4 + by4 * by4 + bz4 * bz4);

    float fz_raw = 0.25f * (b1 + b2 + b3 + b4);
    float fx_raw = 0.5f * ((b2 + b4) - (b1 + b3));
    float fy_raw = 0.5f * ((b3 + b4) - (b1 + b2));

    float dxx = 0.5f * ((bx2 + bx4) - (bx1 + bx3));
    float dxy = 0.5f * ((by2 + by4) - (by1 + by3));
    float dxz = 0.5f * ((bz2 + bz4) - (bz1 + bz3));

    float dyx = 0.5f * ((bx3 + bx4) - (bx1 + bx2));
    float dyy = 0.5f * ((by3 + by4) - (by1 + by2));
    float dyz = 0.5f * ((bz3 + bz4) - (bz1 + bz2));

    float mx = 0.25f * (bx1 + bx2 + bx3 + bx4);
    float my = 0.25f * (by1 + by2 + by3 + by4);
    float mz = 0.25f * (bz1 + bz2 + bz3 + bz4);

    fx_raw += 0.20f * dxx + 0.10f * dxy + 0.10f * dxz;
    fy_raw += 0.10f * dyx + 0.20f * dyy + 0.10f * dyz;
    fz_raw += 0.15f * fabsf(mx) + 0.15f * fabsf(my) + 0.30f * fabsf(mz);

    const float deadzone_xy = 2000.0f;
    const float deadzone_z = 3000.0f;

    fx_raw = deadzonef_local(fx_raw, deadzone_xy);
    fy_raw = deadzonef_local(fy_raw, deadzone_xy);

    if (fz_raw < deadzone_z)
    {
        fz_raw = 0.0f;
    }
    else
    {
        fz_raw -= deadzone_z;
    }

    const float scale_xy = 800.0f;
    const float scale_z = 1000.0f;

    float fx = fx_raw / scale_xy;
    float fy = fy_raw / scale_xy;
    float fz = fz_raw / scale_z;

    out->x = fx;
    out->y = fy;
    out->z = fz;

}
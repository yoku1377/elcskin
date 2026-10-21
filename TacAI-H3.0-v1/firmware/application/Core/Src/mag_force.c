#include "mag_force.h"
#include "no8_region_model_v3.h"
#include <math.h>
#include <stdint.h>

/*
 * Set to 1 only after replacing force_calib with coefficients generated from
 * this board's 24-channel delta-B calibration data.  The default keeps the
 * legacy estimate available for collecting that data.
 */
#ifndef MAG_FORCE_USE_POLY_CALIBRATION
#define MAG_FORCE_USE_POLY_CALIBRATION 1
#endif

/*
 * Each physical finger needs its own calibration data.  The normal project
 * default remains NO6; the NO7 delivery is built with
 * MAG_FORCE_CALIBRATION_PROFILE=7 from the Keil project definition.
 */
#define MAG_FORCE_PROFILE_NO6 6u
#define MAG_FORCE_PROFILE_NO7 7u
#define MAG_FORCE_PROFILE_NO8 8u
#ifndef MAG_FORCE_CALIBRATION_PROFILE
#define MAG_FORCE_CALIBRATION_PROFILE MAG_FORCE_PROFILE_NO6
#endif

#if (MAG_FORCE_CALIBRATION_PROFILE != MAG_FORCE_PROFILE_NO6) && \
    (MAG_FORCE_CALIBRATION_PROFILE != MAG_FORCE_PROFILE_NO7) && \
    (MAG_FORCE_CALIBRATION_PROFILE != MAG_FORCE_PROFILE_NO8)
#error "Unsupported magnetic force calibration profile"
#endif

#if MAG_FORCE_USE_POLY_CALIBRATION
static float clamp_fz_calibration(float fz);
/* NO6 +Z calibration, 2026-08-07.  Front 23 N is excluded. */
static const float no6_front_mean[12] = {34278.2672f,2927.02256f,-15702.1723f,-36718.0787f,-4092.38611f,8753.43828f,-47373.4538f,-10120.6633f,-22120.9174f,43779.5443f,8542.24888f,32529.0321f};
static const float no6_front_scale[12] = {17070.7180f,1310.92719f,10156.5241f,18532.3425f,1410.21944f,6958.46379f,25936.0347f,3914.88859f,8546.10530f,24657.9335f,3495.03292f,14208.8432f};
static const float no6_front_weight[13] = {15.78125f,.927312114f,-.943224486f,-1.36450754f,-.994636797f,.240179995f,1.51823863f,-1.08688911f,-.352168095f,-.649484010f,1.14386862f,.395643806f,.901580055f};
static const float no6_back_mean[12] = {1685.69851f,512.584976f,-6173.14444f,-915.502634f,-626.970951f,9353.85917f,5112.71930f,-3268.17562f,-19190.9121f,-6322.93602f,134.872024f,14768.9905f};
static const float no6_back_scale[12] = {1092.03332f,995.706339f,3328.74187f,917.789917f,1015.79997f,4400.64719f,4004.02279f,2151.13586f,9726.24681f,4073.57758f,1596.24438f,6807.28393f};
static const float no6_back_weight[13] = {15.2317073f,-.437200826f,-.0623803026f,1.58431061f,-.367272050f,-.0604076774f,.535406131f,2.06302267f,-.820839706f,-2.66199390f,-1.68049247f,-.501410112f,2.32279301f};

/*
 * NO7 +Z calibration, collected with the V4.2 FORCE.Fz monitor on
 * 2026-08-11.  Each model uses the 12 raw Delta-B components of its own
 * 2x2 half: front = columns 0..1, back = columns 2..3.
 */
static const float no7_front_mean[12] = {15598.307f,-906.30927f,-9691.8536f,-18369.647f,1140.8414f,4183.563f,-18888.739f,15822.069f,-20866.157f,25583.189f,-21073.44f,22101.742f};
static const float no7_front_scale[12] = {9001.2036f,673.67336f,5746.6055f,10655.185f,1040.7247f,3688.5269f,12604.721f,10377.057f,11548.266f,16890.064f,13497.025f,13527.89f};
static const float no7_front_weight[13] = {15.5f,1.36736323f,-0.330543418f,-0.494261933f,-1.00361524f,-0.0980348731f,0.522097153f,-1.04006537f,0.613313015f,-1.53620746f,0.772833947f,-0.545995096f,1.21322692f};
static const float no7_back_mean[12] = {5867.6395f,-693.92341f,-21673.895f,-6682.2103f,-2234.1873f,16799.809f,-7902.6593f,-4022.2418f,-11926.845f,6975.6253f,94.024906f,14720.628f};
static const float no7_back_scale[12] = {3452.2792f,249.82032f,7764.4437f,3753.931f,855.73144f,5941.5318f,4992.7029f,1988.3077f,6472.8735f,4330.5669f,892.77325f,9405.5714f};
static const float no7_back_weight[13] = {15.5f,1.11682674f,-0.514798483f,-0.589310672f,-1.22070946f,0.0417528966f,0.377777173f,-1.29606654f,-0.196046473f,-0.999164963f,1.44472096f,1.09667334f,1.25261124f};

/*
 * NO8 v2 +Z calibration, collected 2026-09-08 with the fixed Z-axis fixture.
 * A single standardized 24-channel model covers the four tested 4x2-array
 * contact locations: front tip, front center, key boundary, and rear center.
 * It replaces the v1 "front/back maximum" logic, which produced false force
 * at the key boundary.  The interrupted boundary 29 N capture and superseded
 * front-tip 24 N / 28 N captures are excluded from this fit.
 */
static const float no8_region_mean[24] = {-5874.78101f,-312.082222f,-4209.72432f,3957.47802f,278.871479f,4457.8413f,8630.74215f,-971.168003f,8513.69714f,-6040.0425f,1882.54859f,-7333.20625f,-1451.34925f,-225.258278f,-9869.36631f,923.062271f,-344.834208f,7575.06869f,221.461677f,310.705389f,6973.09666f,82.4012778f,250.333646f,-5035.4814f};
static const float no8_region_scale[24] = {8470.75024f,921.248646f,13891.1545f,6988.23734f,1312.93487f,11632.0442f,13444.1347f,3755.40579f,15564.9117f,9157.97042f,5637.08576f,13041.8578f,3672.37848f,496.369527f,18312.749f,3517.24443f,1479.92255f,14967.2216f,6131.08483f,1282.50136f,11144.4323f,4784.13976f,746.730357f,10149.3577f};
static const float no8_region_weight[25] = {17.5f,-16.9493889f,-0.248846316f,-10.0608669f,-31.6876338f,-1.05134955f,-2.45769477f,18.9589587f,5.30944781f,-6.80346592f,-0.348876893f,16.5792808f,-5.21892546f,11.9653465f,-1.2576977f,0.015704047f,0.434433836f,-4.47751616f,17.6819587f,-2.75410221f,3.16366715f,-3.23730183f,-28.9055646f,1.79583467f,-2.85817576f};

static float no6_vector_fz(const MagForce_Mag3D_t delta[4][2], uint8_t start_col)
{
    const float *mean = (start_col == 0u) ? no6_front_mean : no6_back_mean;
    const float *scale = (start_col == 0u) ? no6_front_scale : no6_back_scale;
    const float *weight = (start_col == 0u) ? no6_front_weight : no6_back_weight;
    float fz = weight[0];
    uint8_t col, row, axis = 0u;
    for (col = start_col; col < (uint8_t)(start_col + 2u); ++col)
        for (row = 0u; row < 2u; ++row)
        {
            const MagForce_Mag3D_t *v = &delta[col][row];
            const float values[3] = {v->x, v->y, v->z};
            uint8_t k;
            for (k = 0u; k < 3u; ++k, ++axis)
                fz += weight[axis + 1u] * (values[k] - mean[axis]) / scale[axis];
        }
    return clamp_fz_calibration(fz);
}

static float no7_vector_fz(const MagForce_Mag3D_t delta[4][2], uint8_t start_col)
{
    const float *mean = (start_col == 0u) ? no7_front_mean : no7_back_mean;
    const float *scale = (start_col == 0u) ? no7_front_scale : no7_back_scale;
    const float *weight = (start_col == 0u) ? no7_front_weight : no7_back_weight;
    float fz = weight[0];
    uint8_t col, row, axis = 0u;

    for (col = start_col; col < (uint8_t)(start_col + 2u); ++col)
        for (row = 0u; row < 2u; ++row)
        {
            const MagForce_Mag3D_t *v = &delta[col][row];
            const float values[3] = {v->x, v->y, v->z};
            uint8_t k;
            for (k = 0u; k < 3u; ++k, ++axis)
                fz += weight[axis + 1u] * (values[k] - mean[axis]) / scale[axis];
        }

    return fz;
}

static float no8_global_vector_fz(const MagForce_Mag3D_t delta[4][2])
{
    float fz = no8_region_weight[0];
    uint8_t col, row, axis = 0u;

    for (col = 0u; col < 4u; ++col)
        for (row = 0u; row < 2u; ++row)
        {
            const MagForce_Mag3D_t *v = &delta[col][row];
            const float values[3] = {v->x, v->y, v->z};
            uint8_t k;
            for (k = 0u; k < 3u; ++k, ++axis)
                fz += no8_region_weight[axis + 1u] *
                      (values[k] - no8_region_mean[axis]) / no8_region_scale[axis];
        }

    return fz;
}

static float no8_region_local_fz(const float feature[NO8_REGION_FEATURE_COUNT], uint8_t region)
{
    float fz = no8_region_v3_weight[region][0];
    uint8_t axis;

    for (axis = 0u; axis < NO8_REGION_FEATURE_COUNT; ++axis)
    {
        fz += no8_region_v3_weight[region][axis + 1u] *
              (feature[axis] - no8_region_v3_mean[region][axis]) /
              no8_region_v3_scale[region][axis];
    }

    return clamp_fz_calibration(fz);
}

static float no8_region_vector_fz(const MagForce_Mag3D_t delta[4][2])
{
    float feature[NO8_REGION_FEATURE_COUNT];
    float gate[NO8_REGION_MODEL_COUNT];
    float candidate[NO8_REGION_MODEL_COUNT];
    float norm_sq = 0.0f;
    float max_score = -2.0f;
    float max_exp = -100.0f;
    float gate_sum = 0.0f;
    float fused = 0.0f;
    float confidence;
    uint8_t col, row, axis = 0u, region;

    for (col = 0u; col < 4u; ++col)
    {
        for (row = 0u; row < 2u; ++row)
        {
            const MagForce_Mag3D_t *v = &delta[col][row];
            feature[axis++] = v->x;
            feature[axis++] = v->y;
            feature[axis++] = v->z;
        }
    }

    for (axis = 0u; axis < NO8_REGION_FEATURE_COUNT; ++axis)
    {
        norm_sq += feature[axis] * feature[axis];
    }
    if (norm_sq < (NO8_REGION_SIGNAL_NORM_MIN * NO8_REGION_SIGNAL_NORM_MIN))
    {
        return 0.0f;
    }

    /*
     * Soft region gate.  Unlike the earlier front/back maximum selector, all
     * four fitted regions contribute continuously.  This preserves force at
     * physical key edges and at the inter-key boundary.
     */
    for (region = 0u; region < NO8_REGION_MODEL_COUNT; ++region)
    {
        float score = 0.0f;
        for (axis = 0u; axis < NO8_REGION_FEATURE_COUNT; ++axis)
        {
            score += feature[axis] * no8_region_v3_prototype[region][axis];
        }
        score /= sqrtf(norm_sq);
        if (score > max_score)
        {
            max_score = score;
        }
        gate[region] = score * NO8_REGION_GATE_SHARPNESS;
        candidate[region] = no8_region_local_fz(feature, region);
        if (gate[region] > max_exp)
        {
            max_exp = gate[region];
        }
    }

    for (region = 0u; region < NO8_REGION_MODEL_COUNT; ++region)
    {
        gate[region] = expf(gate[region] - max_exp);
        gate_sum += gate[region];
    }
    for (region = 0u; region < NO8_REGION_MODEL_COUNT; ++region)
    {
        fused += (gate[region] / gate_sum) * candidate[region];
    }

    /*
     * A response that is unlike every recorded location falls back gradually
     * to the validated v2 global model.  All retained calibration samples have
     * a best prototype cosine >= 0.928, so this does not alter normal samples.
     */
    confidence = (max_score - 0.65f) /
                 (NO8_REGION_COSINE_FALLBACK - 0.65f);
    if (confidence < 0.0f)
    {
        confidence = 0.0f;
    }
    else if (confidence > 1.0f)
    {
        confidence = 1.0f;
    }
    fused = confidence * fused + (1.0f - confidence) *
            clamp_fz_calibration(no8_global_vector_fz(delta));

    return clamp_fz_calibration(fused);
}
/*
 * NO2 vertical-load calibration, collected on 2026-08-06.
 *
 * The front and back key centers excite different 2x2 halves of the matrix.
 * One common Fz curve produced multi-newton errors, so the stronger half
 * selects the matching table at run time.  Each Fz table maps its own raw
 * half-feature to newtons: F=a1*B+a2*B^2+...+a5*B^5.
 *
 * Fx/Fy remain zero deliberately: the supplied data contains only +Z loads.
 * Do not claim lateral-force calibration until +/-X and +/-Y data is fitted.
 */
static const float force_calib_front[3][5] = {
    /* Fx */ {0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    /* Fy */ {0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    /* Fz */ {4.42773806744e-04f, -1.65507797305e-08f,
              5.39855833034e-13f, -7.43139285850e-18f,
              3.95414051414e-23f},
};

static const float force_calib_back[3][5] = {
    /* Fx */ {0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    /* Fy */ {0.0f, 0.0f, 0.0f, 0.0f, 0.0f},
    /* Fz */ {7.26596095840e-05f, 2.34450597430e-08f,
              -9.88559372315e-13f, 1.43428157997e-17f,
              1.07723543844e-22f},
};

static float poly5_eval(const float a[5], float x)
{
    return x * (a[0] + x * (a[1] + x * (a[2] + x * (a[3] + x * a[4]))));
}

static float clamp_fz_calibration(float fz)
{
    if (fz < 0.0f)
    {
        return 0.0f;
    }

    /* The fitted range is 0 to 32 N.  Avoid extrapolating a fifth-order fit. */
    if (fz > 35.0f)
    {
        return 35.0f;
    }

    return fz;
}

/*
 * NO6 v4 output correction, measured on 2026-08-10 after the v2 back-key
 * firmware update.  Each pair is {uncorrected NO6 vector Fz, reference N}.
 * The 24-channel vector model remains unchanged.  The independently held
 * front and back loads below only correct its repeatable final-output error.
 * Piecewise linear interpolation is deliberate: it passes measured points
 * without the oscillation risk of another high-order polynomial.
 */
typedef struct
{
    float displayed_fz;
    float reference_fz;
} FzOutputCorrectionPoint_t;

/*
 * NO7 front-button independent verification, 2026-08-12.
 * x is the NO7 v1 firmware Fz before the V4.2 monitor tare; y is the
 * applied reference load.  Only the front model is corrected here.  The
 * NO7 back-button model is intentionally left unchanged.
 */
static const FzOutputCorrectionPoint_t no7_front_output_correction[] = {
    {0.000f, 0.000f}, {1.492f, 1.500f}, {6.094f, 6.500f},
    {10.779f, 11.500f}, {15.482f, 16.500f}, {20.283f, 21.500f},
    {25.189f, 26.500f}, {29.687f, 31.500f},
};

static const FzOutputCorrectionPoint_t fz_output_correction_front[] = {
    {1.105f, 0.0f}, {2.798f, 1.5f}, {5.483f, 3.5f},
    {8.161f, 5.5f}, {10.814f, 7.5f}, {13.240f, 9.5f},
    {15.520f, 11.5f}, {17.537f, 13.5f}, {19.269f, 15.5f},
    {21.313f, 17.5f}, {22.567f, 19.5f}, {24.227f, 21.5f},
    {25.347f, 23.5f}, {26.985f, 25.5f}, {27.514f, 27.5f},
    {28.685f, 29.5f}, {30.152f, 33.5f},
};

static const FzOutputCorrectionPoint_t fz_output_correction_back[] = {
    {1.115f, 0.0f}, {2.433f, 1.5f}, {3.941f, 3.5f},
    {6.505f, 5.5f}, {8.869f, 7.5f}, {13.175f, 11.5f},
    {17.803f, 15.5f}, {21.731f, 19.5f}, {25.635f, 23.5f},
    {28.950f, 27.5f}, {32.339f, 31.5f},
};

static float fz_output_correct(
    const FzOutputCorrectionPoint_t *points,
    uint32_t count,
    float displayed_fz)
{
    uint32_t i;

    if (displayed_fz <= points[0].displayed_fz)
    {
        return points[0].reference_fz;
    }

    for (i = 1u; i < count; ++i)
    {
        if (displayed_fz <= points[i].displayed_fz)
        {
            const float x0 = points[i - 1u].displayed_fz;
            const float y0 = points[i - 1u].reference_fz;
            const float x1 = points[i].displayed_fz;
            const float y1 = points[i].reference_fz;
            return y0 + (displayed_fz - x0) * (y1 - y0) / (x1 - x0);
        }
    }

    /* The fitted range is 0..32 N; use the last local slope only at its edge. */
    {
        const FzOutputCorrectionPoint_t *p0 = &points[count - 2u];
        const FzOutputCorrectionPoint_t *p1 = &points[count - 1u];
        return p1->reference_fz + (displayed_fz - p1->displayed_fz)
               * (p1->reference_fz - p0->reference_fz)
               / (p1->displayed_fz - p0->displayed_fz);
    }
}
#endif

static float deadzonef_local(float v, float dz)
{
    if (fabsf(v) < dz)
    {
        return 0.0f;
    }

    return (v > 0.0f) ? (v - dz) : (v + dz);
}

/* PDF section 2.4 feature construction for one 2x2 half of the matrix. */
static void mag_force_build_half_features(
    const MagForce_Mag3D_t delta[2][2],
    MagForce_Vec3_t *out)
{
    const float bx1 = delta[0][0].x;
    const float by1 = delta[0][0].y;
    const float bz1 = delta[0][0].z;
    const float bx2 = delta[0][1].x;
    const float by2 = delta[0][1].y;
    const float bz2 = delta[0][1].z;
    const float bx3 = delta[1][0].x;
    const float by3 = delta[1][0].y;
    const float bz3 = delta[1][0].z;
    const float bx4 = delta[1][1].x;
    const float by4 = delta[1][1].y;
    const float bz4 = delta[1][1].z;

    const float b1 = sqrtf(bx1 * bx1 + by1 * by1 + bz1 * bz1);
    const float b2 = sqrtf(bx2 * bx2 + by2 * by2 + bz2 * bz2);
    const float b3 = sqrtf(bx3 * bx3 + by3 * by3 + bz3 * bz3);
    const float b4 = sqrtf(bx4 * bx4 + by4 * by4 + bz4 * bz4);

    float fz_raw = 0.25f * (b1 + b2 + b3 + b4);
    float fx_raw = 0.5f * ((b2 + b4) - (b1 + b3));
    float fy_raw = 0.5f * ((b3 + b4) - (b1 + b2));

    const float dxx = 0.5f * ((bx2 + bx4) - (bx1 + bx3));
    const float dxy = 0.5f * ((by2 + by4) - (by1 + by3));
    const float dxz = 0.5f * ((bz2 + bz4) - (bz1 + bz3));
    const float dyx = 0.5f * ((bx3 + bx4) - (bx1 + bx2));
    const float dyy = 0.5f * ((by3 + by4) - (by1 + by2));
    const float dyz = 0.5f * ((bz3 + bz4) - (bz1 + bz2));
    const float mx = 0.25f * (bx1 + bx2 + bx3 + bx4);
    const float my = 0.25f * (by1 + by2 + by3 + by4);
    const float mz = 0.25f * (bz1 + bz2 + bz3 + bz4);

    fx_raw += 0.20f * dxx + 0.10f * dxy + 0.10f * dxz;
    fy_raw += 0.10f * dyx + 0.20f * dyy + 0.10f * dyz;
    fz_raw += 0.15f * fabsf(mx) + 0.15f * fabsf(my) + 0.30f * fabsf(mz);

    out->x = fx_raw;
    out->y = fy_raw;
    out->z = fz_raw;
}

static void mag_force_apply_legacy_scale(
    const MagForce_Vec3_t *features,
    MagForce_Vec3_t *out)
{
    float fx_raw = deadzonef_local(features->x, 2000.0f);
    float fy_raw = deadzonef_local(features->y, 2000.0f);
    float fz_raw = features->z;

    if (fz_raw < 3000.0f)
    {
        fz_raw = 0.0f;
    }
    else
    {
        fz_raw -= 3000.0f;
    }

    out->x = fx_raw / 800.0f;
    out->y = fy_raw / 800.0f;
    out->z = fz_raw / 1000.0f;
}

void MAG_FORCE_Calc3DNoCalib(
    const MagForce_Mag3D_t delta[4][2],
    MagForce_Vec3_t *out)
{
#if MAG_FORCE_CALIBRATION_PROFILE == MAG_FORCE_PROFILE_NO7
    const float front_fz = no7_vector_fz(delta, 0u);
    const float back_fz = no7_vector_fz(delta, 2u);

    if (out == 0)
    {
        return;
    }

    /*
     * Select the half whose own calibrated model sees the stronger load.
     * This is more reliable for NO7 around 1 N than comparing uncalibrated
     * magnetic magnitudes, while the empty-load offset is removed by the
     * V4.2 monitor's normal FORCE.Fz tare operation.
     */
    out->x = 0.0f;
    out->y = 0.0f;
    if (front_fz >= back_fz)
    {
        out->z = clamp_fz_calibration(fz_output_correct(
            no7_front_output_correction,
            sizeof(no7_front_output_correction) / sizeof(no7_front_output_correction[0]),
            front_fz));
    }
    else
    {
        /* NO7 rear-key validation is already within about 0.6 N MAE. */
        out->z = clamp_fz_calibration(back_fz);
    }
#elif MAG_FORCE_CALIBRATION_PROFILE == MAG_FORCE_PROFILE_NO8
    {
        /* NO8 v3: four fitted regions, soft-gated from 24-channel Delta-B. */
        if (out == 0)
        {
            return;
        }
        out->x = 0.0f;
        out->y = 0.0f;
        out->z = clamp_fz_calibration(no8_region_vector_fz(delta));
    }
#else
    MagForce_Vec3_t features_top;
    MagForce_Vec3_t features_bottom;
    MagForce_Vec3_t features;

    if (out == 0)
    {
        return;
    }

    /* PDF layout: columns 0..1 and 2..3 are the two 2x2 halves. */
    mag_force_build_half_features(&delta[0], &features_top);
    mag_force_build_half_features(&delta[2], &features_bottom);

    features.x = features_top.x + features_bottom.x;
    features.y = features_top.y + features_bottom.y;
    features.z = features_top.z + features_bottom.z;

#if MAG_FORCE_USE_POLY_CALIBRATION
    if (features_top.z > features_bottom.z)
    {
        /* Front-key response is dominated by columns 0..1. */
        out->x = poly5_eval(force_calib_front[0], features_top.x);
        out->y = poly5_eval(force_calib_front[1], features_top.y);
        out->z = clamp_fz_calibration(fz_output_correct(
            fz_output_correction_front,
            sizeof(fz_output_correction_front) / sizeof(fz_output_correction_front[0]),
            no6_vector_fz(delta, 0u)));
    }
    else
    {
        /* Back-key response is dominated by columns 2..3. */
        out->x = poly5_eval(force_calib_back[0], features_bottom.x);
        out->y = poly5_eval(force_calib_back[1], features_bottom.y);
        out->z = clamp_fz_calibration(fz_output_correct(
            fz_output_correction_back,
            sizeof(fz_output_correction_back) / sizeof(fz_output_correction_back[0]),
            no6_vector_fz(delta, 2u)));
    }
#else
    mag_force_apply_legacy_scale(&features, out);
#endif
#endif
}

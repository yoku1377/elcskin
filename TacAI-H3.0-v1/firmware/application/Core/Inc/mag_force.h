#ifndef MAG_FORCE_H
#define MAG_FORCE_H

#ifdef __cplusplus
extern "C"
{
#endif

    typedef struct
    {
        float x;
        float y;
        float z;
    } MagForce_Mag3D_t;

    typedef struct
    {
        float x;
        float y;
        float z;
    } MagForce_Vec3_t;

    /*
     * 4x2 magnetic matrix -> total 3D force estimate.
     * Columns 0..1 form the top half and columns 2..3 form the bottom half.
     *
     * delta[1][0] = S3, 左上
     * delta[1][1] = S4, 右上
     * delta[0][0] = S1, 左下
     * delta[0][1] = S2, 右下
     */
    void MAG_FORCE_Calc3DNoCalib(
        const MagForce_Mag3D_t delta[4][2],
        MagForce_Vec3_t *out);

#ifdef __cplusplus
}
#endif

#endif

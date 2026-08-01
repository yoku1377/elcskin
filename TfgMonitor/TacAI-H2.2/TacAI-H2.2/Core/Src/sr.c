#include <math.h>
#include "sr.h"

void SuperResolution_2Force_To_3x4_FixedWeight_UpDown(
    const ForceVec3_t *F_top,
    const ForceVec3_t *F_bottom,
    ForceVec3_t force_map[SR_ROWS][SR_COLS]
)
{
    const float W_TOP[SR_ROWS][SR_COLS] = {
        {0.08f, 0.14f, 0.08f},
        {0.12f, 0.22f, 0.12f},
        {0.06f, 0.12f, 0.06f},
        {0.00f, 0.00f, 0.00f}
    };

    const float W_BOTTOM[SR_ROWS][SR_COLS] = {
        {0.00f, 0.00f, 0.00f},
        {0.06f, 0.12f, 0.06f},
        {0.12f, 0.22f, 0.12f},
        {0.08f, 0.14f, 0.08f}
    };

    for (uint8_t i = 0; i < SR_ROWS; i++) {
        for (uint8_t j = 0; j < SR_COLS; j++) {
            float wt = W_TOP[i][j];
            float wb = W_BOTTOM[i][j];

            force_map[i][j].x = wt * F_top->x + wb * F_bottom->x;
            force_map[i][j].y = wt * F_top->y + wb * F_bottom->y;
            force_map[i][j].z = wt * F_top->z + wb * F_bottom->z;
        }
    }
}
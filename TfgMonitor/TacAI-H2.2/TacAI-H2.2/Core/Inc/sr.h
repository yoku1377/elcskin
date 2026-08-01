#ifndef __SR_H__
#define __SR_H__

#include <stdint.h>

typedef struct {
    float x;
    float y;
    float z;
} ForceVec3_t;

#define SR_ROWS 4
#define SR_COLS 3

void SuperResolution_2Force_To_3x4_FixedWeight_UpDown(
    const ForceVec3_t *F_top,
    const ForceVec3_t *F_bottom,
    ForceVec3_t force_map[SR_ROWS][SR_COLS]
);
#endif
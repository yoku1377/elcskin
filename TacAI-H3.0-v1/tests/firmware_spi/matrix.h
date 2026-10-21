#ifndef TEST_SPI_MATRIX_H
#define TEST_SPI_MATRIX_H
#include <stdint.h>
#define MATRIX_SCL_COL_NUM 4
#define MATRIX_SDA_ROW_NUM 2
#define MATRIX_VALID_MASK_ALL 0xFFu
typedef struct { float x, y, z; } test_matrix_sample_t;
typedef struct {
    uint8_t valid_mask;
    uint32_t error_count;
    uint32_t baseline_epoch;
} matrix_diagnostics_t;
extern test_matrix_sample_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
void MATRIX_GetDiagnostics(matrix_diagnostics_t *diagnostics);
#endif

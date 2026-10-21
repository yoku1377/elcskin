/**
 * @file matrix.h
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief
 * @version 0.1
 * @date 2025-10-09
 *
 * @copyright Copyright (c) 2025
 *
 */
#ifndef __MATRIX_H__
#define __MATRIX_H__

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g0xx_hal.h"
#include "mlx90393.h"
#include "i2c.h"

/* Private defines -----------------------------------------------------------*/
/* USER CODE BEGIN Private defines */
#define MATRIX_I2C_BUS (&hi2c2)
#define MATRIX_I2C_ADDR_BASE 0x0c
#define MATRIX_SCL_COL_NUM 4
#define MATRIX_SDA_ROW_NUM 2
#define MATRIX_EMPTY_ROUND 5
#define MATRIX_BASELINE_ROUND 10
#define MATRIX_VALID_MASK_ALL 0xFFu
#define CFG_REF_RAM_OFFSET 0x00
#define STATUS_REF_RAM_OFFSET 0x01

#define DATA_REG_RAM_OFFSET 0x10
/* USER CODE END Private defines */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */
typedef struct {
    uint8_t valid_mask; /* bit 0..7 = B00,B01,B10,B11,B20,B21,B30,B31 */
    uint32_t error_count; /* failed channel acquisitions since boot, saturating */
    uint32_t baseline_epoch; /* zero until the first successful whole baseline */
} matrix_diagnostics_t;

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */
extern mlx90393_data_t g_baseline[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
extern mlx90393_data_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/

/* USER CODE BEGIN EFP */
HAL_StatusTypeDef MATRIX_Init(void);
/* Failed acquisitions leave the entire published g_data unchanged. */
HAL_StatusTypeDef MATRIX_Update_Measurement(uint8_t sub_baseline);
HAL_StatusTypeDef MATRIX_Update_Magnetic(uint8_t sub_baseline);
/* Failure preserves both g_baseline and g_data, including baseline_epoch. */
HAL_StatusTypeDef MATRIX_Update_Baseline(void);
void MATRIX_GetDiagnostics(matrix_diagnostics_t *diagnostics);
void MATRIX_I2C_Scan(void);
/* USER CODE END EFP */



#ifdef __cplusplus
}
#endif

#endif

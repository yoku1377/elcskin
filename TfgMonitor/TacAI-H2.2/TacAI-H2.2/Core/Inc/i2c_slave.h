/**
 * @file i2c_slave.h
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief 
 * @version 0.1
 * @date 2025-10-09
 * 
 * @copyright Copyright (c) 2025
 * 
 */
#ifndef __I2C_SLAVE_H__
#define __I2C_SLAVE_H__

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g0xx_hal.h"
#include "i2c.h"
#include "tim.h"
/* Private defines -----------------------------------------------------------*/
/* USER CODE BEGIN Private defines */
#define I2C_REG_RAM_SIZE 256


/* USER CODE END Private defines */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

typedef enum {
    I2C_STATE_WAITING_FOR_OFFSET,  // 等待接收地址偏移量
    I2C_STATE_PROCESSING_DATA      // 正常数据处理状态
} I2C_SlaveState;

typedef union {
    int32_t value;
    uint8_t bytes[4];
} int32_to_bytes_t;
/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */
extern uint8_t g_i2c_reg_ram_buf0[I2C_REG_RAM_SIZE]; // 缓冲区0
extern uint8_t g_i2c_reg_ram_buf1[I2C_REG_RAM_SIZE]; // 缓冲区1
extern volatile uint8_t *g_i2c_active_ram;
extern uint8_t *g_i2c_write_ram;
extern volatile uint8_t buffer_swap_lock;
/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */



#ifdef __cplusplus
}
#endif

#endif



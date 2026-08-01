/**
 * @file lsm6ds3tr-c.h
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief  Header file of vcnl4020 module.
 * @version 0.1
 * @date 2025-02-24
 *
 * @copyright Copyright (c) 2025
 *
 */

#ifndef __LSM6DS3TR_C_H__
#define __LSM6DS3TR_C_H__

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------*/
/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "stm32g0xx_hal.h"
#include "lsm6ds3tr-c_reg.h"
#include <string.h>
#include "i2c.h"
    /* USER CODE END Includes */

    /* Private defines -----------------------------------------------------------*/
    /* USER CODE BEGIN Private defines */
    #define LSM6DS3TR_C_I2C_BUS hi2c2
    #define LSM6DS3TR_C_I2C_TIMEOUT_DEFAULT        (1000)  /*!< Timeout in milliseconds */
    #define LSM6DS3TR_C_OK                           0
    #define LSM6DS3TR_C_ERROR                       -1



    /* USER CODE END Private defines */

    /* Exported types ------------------------------------------------------------*/
    /* USER CODE BEGIN ET */

    typedef struct{
        int16_t xl_x;
        int16_t xl_y;
        int16_t xl_z;
        int16_t gy_x;
        int16_t gy_y;
        int16_t gy_z;
    }LSM6DS3TR_C_6DAxesRaw_t;

    typedef struct{
        int32_t xl_x;
        int32_t xl_y;
        int32_t xl_z;
        int32_t gy_x;
        int32_t gy_y;
        int32_t gy_z;
    }LSM6DS3TR_C_6DAxesRaw_Big_t;

    typedef struct{
        float_t xl_x;
        float_t xl_y;
        float_t xl_z;
        float_t gy_x;
        float_t gy_y;
        float_t gy_z;
    }LSM6DS3TR_C_6DAxes_t;

    typedef struct{
        lsm6ds3tr_c_odr_xl_t xl_odr;
        lsm6ds3tr_c_odr_g_t gy_odr;
        lsm6ds3tr_c_fs_xl_t xl_fs;
        lsm6ds3tr_c_fs_g_t gy_fs;
    }lsm6ds3tr_c_config_t;

    typedef struct 
    {
        stmdev_ctx_t Ctx;
        uint8_t is_initialized;
    } LSM6DS3TR_C_Object_t;
    /* USER CODE END ET */

    /* Exported constants --------------------------------------------------------*/
    /* USER CODE BEGIN EC */

    /* USER CODE END EC */

    /* Exported macro ------------------------------------------------------------*/
    /* USER CODE BEGIN EM */

    /* USER CODE END EM */

    /* Exported functions prototypes ---------------------------------------------*/

    /* USER CODE BEGIN EFP */
    int32_t LSM6DS3TR_C_Init(LSM6DS3TR_C_Object_t *pObj);
    int32_t LSM6DS3TR_C_Get6DAxesRaw(LSM6DS3TR_C_Object_t *pObj, LSM6DS3TR_C_6DAxesRaw_t *Value);
    int32_t LSM6DS3TR_C_Get6DAxes(LSM6DS3TR_C_Object_t *pObj, LSM6DS3TR_C_6DAxes_t *Value);
    /* USER CODE END EFP */

    /* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif

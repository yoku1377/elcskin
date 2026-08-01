/**
 * @file vcnl4020.c
 * @brief This file contains the implementation for the VCNL4020 sensor.
 *
 * The VCNL4020 is a fully integrated proximity and ambient light sensor.
 * This file provides functions to initialize the sensor, read proximity
 * and ambient light data, and configure sensor settings.
 *
 * @note Ensure that the I2C communication is properly configured before
 * using the functions provided in this file.
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @version 0.1
 * @date 2025-02-24
 *
 * @copyright Copyright (c) 2025
 *
 */

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------*/
#include "vcnl4020.h"
#include "log.h"
#include <stdlib.h>

    static HAL_StatusTypeDef VCNL4020_ReadReg(uint8_t addr, uint8_t *value);
    static HAL_StatusTypeDef VCNL4020_WriteReg(uint8_t addr, uint8_t *value);
    static uint8_t VCNL4020_Is_Prox_Ready(void);
    static uint8_t VCNL4020_Is_Als_Ready(void);
    static void VCNL4020_Set_Als_Enable(uint8_t enable);
    static void VCNL4020_Set_Prox_Enable(uint8_t enable);
    static void VCNL4020_Set_Sleftimed_Enable(uint8_t enable);
    static void VCNL4020_Set_Als_OnDemand(uint8_t enable);
    static void VCNL4020_Set_Prox_OnDemand(uint8_t enable);
    static void VCNL4020_Set_Prox_Rate(vcnl4020_prox_rate_t rate);
    static void VCNL4020_Set_Prox_LED_mA(uint8_t current);
    static void VCNL4020_Set_Ambient_Rate(vcnl4020_ambient_rate_t rate);
    static void VCNL4020_Set_Ambient_Averaging(vcnl4020_average_func_t avg);
    static void VCNL4020_Set_Auto_Offset_Compensation(uint8_t enable);
    static void VCNL4020_Set_Cont_Conv_Mode(uint8_t enable);
    static void VCNL4020_Set_Interupt_Disabled(uint8_t enable);
    static void VCNL4020_Set_Prox_Frequency(vcnl4020_prox_freq_t freq);

    const vcnl4020_configure_t VCNL4020_DEFAULT_CONFIG = {
        .prox_led_mA = 200,
        .prox_rate = VCNL4020_PROX_RATE_62_5,
        .als_rate = VCNL4020_AMBIENT_RATE_10,
        .average_func = VCNL4020_AVERAGE_8CONV,
        .prox_freq = VCNL4020_PROX_FREQ_390_625KHZ};

    const vcnl4020_configure_t VCNL4020_ZERO_CONFIG = {0};

    /**
     * @brief Initialize the VCNL4020
     *
     * @param pObj
     * @return int32_t
     */
    int32_t VCNL4020_Init(vcnl4020_object_t *pObj)
    {
        int32_t ret = 0;
        if (pObj == NULL)
        {
            LOG_Error("object is NULL");
            return -1;
        }

        /* Initialize the I2C bus */
        //    if (BSP_I2C1_Init() != BSP_ERROR_NONE)
        //    {
        //        return BSP_ERROR_BUS_FAILURE;
        //    }
        /* get product ID */
        uint8_t product_id = 0;
        VCNL4020_ReadReg(VCNL4020_REG_PRODUCT_ID, &product_id);
        if (product_id != VCNL4020_CURRENT_REVISION)
        {
            LOG_Error("this device does not match vcnl4020's current revison:%d (should be %d)", product_id, VCNL4020_CURRENT_REVISION);
            return -1;
        }
        // LOG_Debug("product id is 0x%02x",product_id);
        /* disable all functions */
        VCNL4020_Set_Als_Enable(0);
        VCNL4020_Set_Prox_Enable(0);
        VCNL4020_Set_Sleftimed_Enable(0);
        /* disable on-demand func */
        VCNL4020_Set_Als_OnDemand(0);
        VCNL4020_Set_Prox_OnDemand(0);
        /* set prox rate */
        VCNL4020_Set_Prox_Rate(pObj->config.prox_rate);
        /* set prox LED current */
        VCNL4020_Set_Prox_LED_mA(pObj->config.prox_led_mA);
        /* set ambient rate */
        VCNL4020_Set_Ambient_Rate(pObj->config.als_rate);
        /* set ambient averaging */
        VCNL4020_Set_Ambient_Averaging(pObj->config.average_func);
        /* set auto offset compensation */
        VCNL4020_Set_Auto_Offset_Compensation(0);
        /* set cont conv mode */
        VCNL4020_Set_Cont_Conv_Mode(0);
        /* set interrupt disabled */
        VCNL4020_Set_Interupt_Disabled(1);
        /* set prox frequency */
        VCNL4020_Set_Prox_Frequency(pObj->config.prox_freq);
        /* enable all functions */
        VCNL4020_Set_Als_Enable(1);
        VCNL4020_Set_Prox_Enable(1);
        VCNL4020_Set_Sleftimed_Enable(1);

        pObj->is_initialized = 1;
        return ret;
    }

    /**
     * @brief Read the proximity data from the VCNL4020
     *
     * @param pObj
     * @param prox
     * @return int32_t
     */
    int32_t VCNL4020_Read_Proximity(vcnl4020_object_t *pObj, uint16_t *prox)
    {
        uint16_t tmp;
        uint8_t data[2];
        static uint16_t prox_sort_data[VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE];

        if (pObj == NULL || prox == NULL)
        {
            LOG_Error("object is NULL");
            return -1;
        }
        if (pObj->is_initialized == 0)
        {
            // LOG_Error("object is not initialized");
            return -2;
        }
        if (VCNL4020_Is_Prox_Ready() == 0)
        {
            // LOG_Error("PROX is not ready");
			*prox = pObj->prox_history_data[0];
            return -3;
        }
        VCNL4020_ReadReg(VCNL4020_REG_PROX_RESULT_HIGH, &(data[0]));
        VCNL4020_ReadReg(VCNL4020_REG_PROX_RESULT_LOW, &(data[1]));
        /* unique spike filtering */
        tmp = (data[0] << 8) | data[1];
        /* Shift the history data to make room for the new value */ 
        for (int i = VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE - 1; i > 0; i--)
        {
            pObj->prox_history_data[i] = pObj->prox_history_data[i - 1];
        }
        pObj->prox_history_data[0] = tmp;
        /* Copy prox_history_data to prox_sort_data */
        for (int i = 0; i
				< VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE; i++)
        {
            prox_sort_data[i] = pObj->prox_history_data[i];
        }
        /* Perform bubble sort on prox_history_data */ 
        for (int i = 0; i < VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE - 1; i++)
        {
            for (int j = 0; j < VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE - i - 1; j++)
            {
                if (prox_sort_data[j] > prox_sort_data[j + 1])
                {
                    uint16_t temp = prox_sort_data[j];
                    prox_sort_data[j] = prox_sort_data[j + 1];
                    prox_sort_data[j + 1] = temp;
                }
            }
        }
        *prox = prox_sort_data[2];
        return 0;
    }

    /**
     * @brief
     *
     * @param pObj
     * @param ambient
     * @return int32_t
     */
    int32_t VCNL4020_Read_Ambient(vcnl4020_object_t *pObj, uint16_t *ambient)
    {
        uint8_t data[2];
        if (pObj == NULL || ambient == NULL)
        {
            LOG_Error("object is NULL");
            return -1;
        }
        if (pObj->is_initialized == 0)
        {
            // LOG_Error("object is not initialized");
            return -2;
        }
        if (VCNL4020_Is_Als_Ready() == 0)
        {
            // LOG_Error("ALS is not ready");
            
            return -3;
        }
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_RESULT_HIGH, &(data[0]));
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_RESULT_LOW, &(data[1]));
        *ambient = (data[0] << 8) | data[1];
        return 0;
    }
    /**
     * @brief Read a register value
     *
     * @param addr
     * @param value
     * @return HAL_StatusTypeDef
     */
    static HAL_StatusTypeDef VCNL4020_ReadReg(uint8_t addr, uint8_t *value)
    {
        HAL_StatusTypeDef ret = HAL_OK;
        ret = HAL_I2C_Mem_Read(&VCNL4020_I2C_BUS, (uint16_t)(VCNL4020_I2C_ADDRESS << 1), addr, I2C_MEMADD_SIZE_8BIT, value, 1, VCNL4020_I2C_TIMEOUT_DEFAULT);
        if (ret != HAL_OK)
        {
            LOG_Error("I2C read register failed:%d", ret);
            return HAL_ERROR;
        }
        return ret;
    }

    /**
     * @brief Write a value to a register
     * @param addr The register address
     * @param value The value to write
     * @return HAL_StatusTypeDef
     */
    static HAL_StatusTypeDef VCNL4020_WriteReg(uint8_t addr, uint8_t *value)
    {
        HAL_StatusTypeDef ret = HAL_OK;
        ret = HAL_I2C_Mem_Write(&VCNL4020_I2C_BUS, (uint16_t)(VCNL4020_I2C_ADDRESS << 1), addr, I2C_MEMADD_SIZE_8BIT, value, 1, VCNL4020_I2C_TIMEOUT_DEFAULT);
        if (ret != HAL_OK)
        {
            LOG_Error("I2C write register failed:%d", ret);
            return HAL_ERROR;
        }
        return ret;
    }

    static uint8_t VCNL4020_Is_Prox_Ready(void)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        return reg.prox_data_rdy;
    }

    static uint8_t VCNL4020_Is_Als_Ready(void)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        return reg.als_data_rdy;
    }
    static void VCNL4020_Set_Als_Enable(uint8_t enable)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        reg.als_en = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Prox_Enable(uint8_t enable)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        reg.prox_en = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Sleftimed_Enable(uint8_t enable)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        reg.selftimed_en = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Als_OnDemand(uint8_t enable)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        reg.als_od = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Prox_OnDemand(uint8_t enable)
    {
        vcnl4020_reg_command_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
        reg.prox_en = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Prox_Rate(vcnl4020_prox_rate_t rate)
    {
        vcnl4020_reg_prox_rate_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_PROX_RATE, (uint8_t *)&reg);
        reg.prox_rate = rate;
        VCNL4020_WriteReg(VCNL4020_REG_PROX_RATE, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Prox_LED_mA(uint8_t current)
    {
        if (current > 200)
        {
            LOG_Error("current should be less than 20mA");
            return;
        }
        vcnl4020_reg_ir_led_current_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_IR_LED_CURRENT, (uint8_t *)&reg);
        reg.ir_led_current_value = current / 10;
        VCNL4020_WriteReg(VCNL4020_REG_IR_LED_CURRENT, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Ambient_Rate(vcnl4020_ambient_rate_t rate)
    {
        if (rate > VCNL4020_AMBIENT_RATE_10)
        {
            LOG_Error("rate should be less than 10");
            return;
        }
        vcnl4020_reg_ambient_param_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
        reg.als_rate = rate;
        VCNL4020_WriteReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Ambient_Averaging(vcnl4020_average_func_t avg)
    {
        if (avg > VCNL4020_AVERAGE_128CONV)
        {
            LOG_Error("avg should be less than 128");
            return;
        }
        vcnl4020_reg_ambient_param_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
        reg.average_func = avg;
        VCNL4020_WriteReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Auto_Offset_Compensation(uint8_t enable)
    {
        vcnl4020_reg_ambient_param_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
        reg.auto_offset_compensation = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Cont_Conv_Mode(uint8_t enable)
    {
        vcnl4020_reg_ambient_param_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
        reg.cont_conv_mode = enable ? 1 : 0;
        VCNL4020_WriteReg(VCNL4020_REG_AMBIENT_PARAM, (uint8_t *)&reg);
    }

    static void VCNL4020_Set_Interupt_Disabled(uint8_t enable)
    {
        vcnl4020_reg_int_ctrl_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_INT_CTRL, (uint8_t *)&reg);
        reg.int_als_ready_en = 0;
        reg.int_prox_ready_en = 0;
        reg.int_thres_en = 0;
        VCNL4020_WriteReg(VCNL4020_REG_COMMAND, (uint8_t *)&reg);
    }

    // todo: setProxFrequency
    static void VCNL4020_Set_Prox_Frequency(vcnl4020_prox_freq_t freq)
    {
        vcnl4020_reg_prox_status_t reg;
        VCNL4020_ReadReg(VCNL4020_REG_PROX_ADJUST, (uint8_t *)&reg);
        reg.modual_dead_time = 1;
        reg.prox_freq = freq;
        VCNL4020_WriteReg(VCNL4020_REG_PROX_ADJUST, (uint8_t *)&reg);
    }

#ifdef __cplusplus
}
#endif

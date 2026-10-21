/**
 ******************************************************************************
 * @file    lsm6ds3tr-c.c
 * @author  MEMS Software Solutions Team
 * @brief   LSM6DS3TR-C driver file
 ******************************************************************************
 * @attention
 *
 * <h2><center>&copy; Copyright (c) 2019 STMicroelectronics.
 * All rights reserved.</center></h2>
 *
 * This software component is licensed by ST under BSD 3-Clause license,
 * the "License"; You may not use this file except in compliance with the
 * License. You may obtain a copy of the License at:
 *                        opensource.org/licenses/BSD-3-Clause
 *
 ******************************************************************************
 */

/* Includes ------------------------------------------------------------------*/
#include "lsm6ds3tr-c.h"
#include "log.h"

#define USE_SENSORHUB_DIRECT_MODE 1

const lsm6ds3tr_c_config_t lsm6ds3tr_c_defualt_config = {
    .gy_fs = LSM6DS3TR_C_2000dps,
    .gy_odr = LSM6DS3TR_C_GY_ODR_416Hz,
    .xl_fs = LSM6DS3TR_C_2g,
    .xl_odr = LSM6DS3TR_C_XL_ODR_416Hz
};
static int32_t LSM6DS3TR_C_IO_Init(void);
static int32_t LSM6DS3TR_C_Write(void *handle, uint8_t reg, const uint8_t *bufp, uint16_t len);
static int32_t LSM6DS3TR_C_Read(void *handle, uint8_t reg, uint8_t *bufp, uint16_t len);
static void LSM6DS3TR_C_Delay(uint32_t ms);

/**
 * @brief  Register Component Bus IO operations
 * @param  pObj the device pObj
 * @retval 0 in case of success, an error code otherwise
 */
int32_t LSM6DS3TR_C_RegisterBusIO(LSM6DS3TR_C_Object_t *pObj)
{
    int32_t ret = LSM6DS3TR_C_OK;
    if (pObj == NULL)
    {
        ret = LSM6DS3TR_C_ERROR;
    }
    else
    {
        /* Initialize mems driver interface */
        pObj->Ctx.write_reg = LSM6DS3TR_C_Write;
        pObj->Ctx.read_reg = LSM6DS3TR_C_Read;
        pObj->Ctx.mdelay = LSM6DS3TR_C_Delay;
        pObj->Ctx.handle = &LSM6DS3TR_C_I2C_BUS;
        /* Init test platform */
        LSM6DS3TR_C_IO_Init();
        /* Wait sensor boot time */
        LSM6DS3TR_C_Delay(15);
    }
    return ret;
}


/**
 * @brief  Initialize the LIS2MDL sensor
 * @param  pObj the device pObj
 * @retval 0 in case of success, an error code otherwise
 */
int32_t LSM6DS3TR_C_Init(LSM6DS3TR_C_Object_t *pObj)
{
    uint8_t id, reset;
    uint32_t timeout = 10000;
    if (pObj == NULL)
    {
        return LSM6DS3TR_C_ERROR;
    }
//    if ((pObj->is_initialized) != 0)
//    {
//        LOG_Info("this lsm6ds3tr-c has been initialized.");
//        return LSM6DS3TR_C_ERROR;
//    }
    LSM6DS3TR_C_RegisterBusIO(pObj);
    
    if (lsm6ds3tr_c_device_id_get(&(pObj->Ctx), &id) != LSM6DS3TR_C_OK)
    {
        return LSM6DS3TR_C_ERROR;
    }
    if (id != LSM6DS3TR_C_ID)
    {
        LOG_Error("get warn device id: 0x%02x", id);
        return LSM6DS3TR_C_ERROR;
    }
    // LOG_Info("get device id: 0x%02x (should be 0x%02x)", id, LSM6DS3TR_C_ID);
    // lsm6ds3tr_c_boot_set(&(pObj->Ctx), PROPERTY_ENABLE);
    // HAL_Delay(15);
    lsm6ds3tr_c_reset_set(&(pObj->Ctx), PROPERTY_ENABLE);
    do
    {
        lsm6ds3tr_c_reset_get(&(pObj->Ctx), &reset);
    } while (reset && --timeout);

    /* Enable Block Data Update */
    lsm6ds3tr_c_block_data_update_set(&(pObj->Ctx), PROPERTY_ENABLE);
    /* Set output data rate */
    lsm6ds3tr_c_xl_data_rate_set(&(pObj->Ctx), lsm6ds3tr_c_defualt_config.xl_odr);
    lsm6ds3tr_c_gy_data_rate_set(&(pObj->Ctx), lsm6ds3tr_c_defualt_config.gy_odr);
    /* Set full scale */
    lsm6ds3tr_c_xl_full_scale_set(&(pObj->Ctx), lsm6ds3tr_c_defualt_config.xl_fs);
    lsm6ds3tr_c_gy_full_scale_set(&(pObj->Ctx), lsm6ds3tr_c_defualt_config.gy_fs);
    /* Configure filtering chain(No aux interface) */
    /* Configure filtering chain(No aux interface) */
    /* Accelerometer - analog filter */
    lsm6ds3tr_c_xl_filter_analog_set(&(pObj->Ctx), LSM6DS3TR_C_XL_ANA_BW_400Hz);
    /* Accelerometer - LPF1 path ( LPF2 not used )*/
    // lsm6ds3tr_c_xl_lp1_bandwidth_set(&dev_ctx, LSM6DS3TR_C_XL_LP1_ODR_DIV_4);
    /* Accelerometer - LPF1 + LPF2 path */
    lsm6ds3tr_c_xl_lp2_bandwidth_set(&(pObj->Ctx), LSM6DS3TR_C_XL_LOW_NOISE_LP_ODR_DIV_100);
    /* Accelerometer - High Pass / Slope path */
    // lsm6ds3tr_c_xl_reference_mode_set(&dev_ctx, PROPERTY_DISABLE);
    // lsm6ds3tr_c_xl_hp_bandwidth_set(&dev_ctx, LSM6DS3TR_C_XL_HP_ODR_DIV_100);
    /* Gyroscope - filtering chain */
    lsm6ds3tr_c_gy_band_pass_set(&(pObj->Ctx), LSM6DS3TR_C_HP_260mHz_LP1_STRONG);

    /* open sensorhub (direct mode) */
#if USE_SENSORHUB_DIRECT_MODE
    if (lsm6ds3tr_c_sh_master_set(&(pObj->Ctx), PROPERTY_DISABLE) != LSM6DS3TR_C_OK)
    {
        return LSM6DS3TR_C_ERROR;
    }
    if (lsm6ds3tr_c_sh_pass_through_set(&(pObj->Ctx), PROPERTY_ENABLE) != LSM6DS3TR_C_OK)
    {
        return LSM6DS3TR_C_ERROR;
    }
#else
    lsm6ds3tr_c_sh_num_of_dev_connected_set(&(pObj->Ctx), LSM6DS3TR_C_SLV_0);
#endif
    return LSM6DS3TR_C_OK;
}

int32_t LSM6DS3TR_C_Get6DAxesRaw(LSM6DS3TR_C_Object_t *pObj, LSM6DS3TR_C_6DAxesRaw_t *Value)
{
    static int16_t data_raw_acceleration[3];
    static int16_t data_raw_angular_rate[3];
    /* Read output only if new value is available */
    static lsm6ds3tr_c_status_reg_t status;
    lsm6ds3tr_c_status_reg_get(&(pObj->Ctx), &status);

    if (status.xlda) 
    {
        lsm6ds3tr_c_acceleration_raw_get(&(pObj->Ctx), data_raw_acceleration);
        Value->xl_x = data_raw_acceleration[0];
        Value->xl_y = data_raw_acceleration[1];
        Value->xl_z = data_raw_acceleration[2];
    }
    if (status.gda) 
    {
        lsm6ds3tr_c_angular_rate_raw_get(&(pObj->Ctx), data_raw_angular_rate);
        Value->gy_x = data_raw_angular_rate[0];
        Value->gy_y = data_raw_angular_rate[1];
        Value->gy_z = data_raw_angular_rate[2];
    }
    return LSM6DS3TR_C_OK;
}

int32_t LSM6DS3TR_C_Get6DAxes(LSM6DS3TR_C_Object_t *pObj, LSM6DS3TR_C_6DAxes_t *Value)
{
    static LSM6DS3TR_C_6DAxesRaw_t data_raw;
    LSM6DS3TR_C_Get6DAxesRaw(pObj, &data_raw);

    if(lsm6ds3tr_c_defualt_config.xl_fs == LSM6DS3TR_C_2g)
    {
        Value->xl_x = lsm6ds3tr_c_from_fs2g_to_mg(data_raw.xl_x);
        Value->xl_y = lsm6ds3tr_c_from_fs2g_to_mg(data_raw.xl_y);
        Value->xl_z = lsm6ds3tr_c_from_fs2g_to_mg(data_raw.xl_z);
    }
    else
    {
        LOG_Error("not support xl_fs: %d", lsm6ds3tr_c_defualt_config.xl_fs);
        return LSM6DS3TR_C_ERROR;
    }
    
    if(lsm6ds3tr_c_defualt_config.gy_fs == LSM6DS3TR_C_2000dps)
    {
        Value->gy_x = lsm6ds3tr_c_from_fs2000dps_to_mdps(data_raw.gy_x);
        Value->gy_y = lsm6ds3tr_c_from_fs2000dps_to_mdps(data_raw.gy_y);
        Value->gy_z = lsm6ds3tr_c_from_fs2000dps_to_mdps(data_raw.gy_z);
    }
    else
    {
        LOG_Error("not support gy_fs: %d", lsm6ds3tr_c_defualt_config.gy_fs);
        return LSM6DS3TR_C_ERROR;
    }
    return LSM6DS3TR_C_OK;
}

static int32_t LSM6DS3TR_C_IO_Init(void)
{
    return 0;
}

static int32_t LSM6DS3TR_C_Write(void *handle, uint8_t reg, const uint8_t *bufp, uint16_t len)
{
    return HAL_I2C_Mem_Write(&LSM6DS3TR_C_I2C_BUS, LSM6DS3TR_C_I2C_ADD_L, reg, I2C_MEMADD_SIZE_8BIT, (uint8_t *)bufp, len, LSM6DS3TR_C_I2C_TIMEOUT_DEFAULT);
}

static int32_t LSM6DS3TR_C_Read(void *handle, uint8_t reg, uint8_t *bufp, uint16_t len)
{
    return HAL_I2C_Mem_Read(&LSM6DS3TR_C_I2C_BUS, LSM6DS3TR_C_I2C_ADD_L, reg, I2C_MEMADD_SIZE_8BIT, (uint8_t *)bufp, len, LSM6DS3TR_C_I2C_TIMEOUT_DEFAULT);
}

static void LSM6DS3TR_C_Delay(uint32_t ms)
{
    HAL_Delay(ms);
}
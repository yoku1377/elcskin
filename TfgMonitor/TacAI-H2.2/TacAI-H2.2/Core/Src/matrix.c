/**
 * @file matrix.c
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief
 * @version 0.1
 * @date 2025-10-09
 *
 * @copyright Copyright (c) 2025
 *
 */

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------*/
#include "matrix.h"
#include "log.h"

    static mlx90393_config_t mlx90393_handle;
    static mlx90393_metrics_selector_t mlx90393_selector = {
        .x_axis = true,
        .y_axis = true,
        .z_axis = true,
        .temperature = false};

    mlx90393_data_t g_baseline[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM] = {0};
    mlx90393_data_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];

    /* MLX90393 I2C address lookup: 0x0C + y * 2 + x */
    static const uint8_t mlx_addr_map[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM] = {
        {0x0C, 0x0E},
        {0x0D, 0x0F}
    };

    static HAL_StatusTypeDef MATRIX_Mlx90393_Init(void);

    /**
     * @brief Scan I2C bus and log found devices
     */
    void MATRIX_I2C_Scan(void)
    {
        uint8_t found_devices = 0;
        LOG_Info("Starting I2C scan...");

        for (uint8_t addr = 0x01; addr < 0x80; addr++)
        {
            if (HAL_I2C_IsDeviceReady(&hi2c2, addr << 1, 2, 10) == HAL_OK)
            {
                LOG_Info("Device found at address: 0x%02X", addr);
                found_devices++;
            }
        }
        LOG_Info("Scan complete. Found %d device(s).", found_devices);
    }

    /**
     * @brief Initialize all sensors on the matrix
     */
    HAL_StatusTypeDef MATRIX_Init(void)
    {
        HAL_StatusTypeDef status = HAL_OK;
        status |= MATRIX_Mlx90393_Init();
        return status;
    }

    /**
     * @brief Calculate baseline by averaging multiple rounds of measurements
     */
    void MATRIX_Update_Baseline(void)
    {
        /* Dummy rounds to let sensor readings stabilize */
        for (uint16_t round = 0; round < MATRIX_EMPTY_ROUND; round++)
        {
            MATRIX_Update_Measurement(false);
        }

        /* Accumulate baseline rounds */
        for (uint8_t round = 0; round < MATRIX_BASELINE_ROUND; round++)
        {
            MATRIX_Update_Measurement(false);
            for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
            {
                for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
                {
                    g_baseline[x][y].x += g_data[x][y].x;
                    g_baseline[x][y].y += g_data[x][y].y;
                    g_baseline[x][y].z += g_data[x][y].z;
                }
            }
        }

        /* Average */
        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                g_baseline[x][y].x /= MATRIX_BASELINE_ROUND;
                g_baseline[x][y].y /= MATRIX_BASELINE_ROUND;
                g_baseline[x][y].z /= MATRIX_BASELINE_ROUND;
            }
        }
    }

    /**
     * @brief Update magnetic data from all MLX90393 sensors
     *
     * @param sub_baseline  If true, subtract baseline from readings
     */
    void MATRIX_Update_Magnetic(uint8_t sub_baseline)
    {
        /* Start measurement on all 4 sensors */
        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                mlx90393_handle.i2c_slave_addr = mlx_addr_map[x][y];
                if (mlx90393_cmd_start_measurement(&mlx90393_handle) != HAL_OK)
                {
                    LOG_Error("start measurement failed at 0x%02X", mlx_addr_map[x][y]);
                }
            }
        }

        /* Wait for conversion to complete */
        HAL_Delay(mlx90393_handle.conv_time);

        /* Read back measurements */
        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                mlx90393_handle.i2c_slave_addr = mlx_addr_map[x][y];
                if (mlx90393_cmd_read_measurement(&mlx90393_handle, &(g_data[x][y])) != HAL_OK)
                {
                    LOG_Error("read measurement failed at 0x%02X", mlx_addr_map[x][y]);
                }
            }
        }

        /* Subtract baseline if requested */
        if (sub_baseline)
        {
            for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
            {
                for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
                {
                    g_data[x][y].x -= g_baseline[x][y].x;
                    g_data[x][y].y -= g_baseline[x][y].y;
                    g_data[x][y].z -= g_baseline[x][y].z;
                }
            }
        }
    }

    /**
     * @brief Update all measurements
     */
    void MATRIX_Update_Measurement(uint8_t sub_baseline)
    {
        MATRIX_Update_Magnetic(sub_baseline);
    }

    /**
     * @brief Initialize all MLX90393 sensors on the I2C bus
     */
    static HAL_StatusTypeDef MATRIX_Mlx90393_Init(void)
    {
        HAL_StatusTypeDef status = HAL_OK;

        mlx90393_handle.manage_i2c_driver = false;
        mlx90393_handle.i2c_handle = MATRIX_I2C_BUS;
        mlx90393_handle.int_gpio_port = 0;
        mlx90393_handle.int_gpio_pin = 0;
        mlx90393_handle.mlx_metrics_selector = mlx90393_selector;


        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                mlx90393_handle.i2c_slave_addr = mlx_addr_map[x][y];
                if (mlx90393_init(&mlx90393_handle) != HAL_OK)
                {
                    LOG_Error("mlx90393 (0x%02X) init failed", mlx_addr_map[x][y]);
                    status = HAL_ERROR;
                }
                else
                {
                    LOG_Info("mlx90393 (0x%02X) init OK", mlx_addr_map[x][y]);
                }
            }
        }
        LOG_Info("MLX90393 conv_time: %d ms", mlx90393_handle.conv_time);
        return status;
    }

#ifdef __cplusplus
}
#endif

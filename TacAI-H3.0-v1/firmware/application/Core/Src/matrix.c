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
#include "main.h"
#include "log.h"
#include <string.h>

    static mlx90393_config_t mlx90393_handle;
    static mlx90393_metrics_selector_t mlx90393_selector = {
        .x_axis = true,
        .y_axis = true,
        .z_axis = true,
        .temperature = false};

    mlx90393_data_t g_baseline[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM] = {0};
    mlx90393_data_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
    static matrix_diagnostics_t s_diagnostics;

    static HAL_StatusTypeDef MATRIX_Mlx90393_Init(void);
    static HAL_StatusTypeDef MATRIX_AcquireSnapshot(
        mlx90393_data_t snapshot[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM]);

    void MATRIX_GetDiagnostics(matrix_diagnostics_t *diagnostics)
    {
        if (diagnostics != NULL)
        {
            *diagnostics = s_diagnostics;
        }
    }

    static void MATRIX_CountError(void)
    {
        if (s_diagnostics.error_count != UINT32_MAX)
        {
            s_diagnostics.error_count++;
        }
    }

    /*
     * The board uses two TMUX1204 switches as an I2C crosspoint.  The selector
     * routing follows TacAI-H2.1_4.21.pdf exactly:
     * - U9 SCL: A0=PB4, A1=PB5; S1..S4 route SCL_3, SCL_2, SCL_1, SCL_4.
     * - U10 SDA: A0=PA15, A1=PB3; S1..S4 route SDA_2, unused, unused, SDA_1.
     * Thus the physical TMUX channel codes are not in logical matrix order.
     * Each selected MLX90393 is therefore accessed at the common 7-bit address 0x0C.
     */
    static const uint8_t s_scl_select_code[MATRIX_SCL_COL_NUM] = {2u, 1u, 0u, 3u};
    static const uint8_t s_sda_select_code[MATRIX_SDA_ROW_NUM] = {3u, 0u};

    static void MATRIX_Select_Channel(uint8_t col, uint8_t row)
    {
        const uint8_t scl_code = s_scl_select_code[col];
        const uint8_t sda_code = s_sda_select_code[row];

        HAL_GPIO_WritePin(I2C1_SCL_SEL0_GPIO_Port, I2C1_SCL_SEL0_Pin,
                          (scl_code & 0x01u) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(I2C1_SCL_SEL1_GPIO_Port, I2C1_SCL_SEL1_Pin,
                          (scl_code & 0x02u) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(I2C1_SDA_SEL0_GPIO_Port, I2C1_SDA_SEL0_Pin,
                          (sda_code & 0x01u) ? GPIO_PIN_SET : GPIO_PIN_RESET);
        HAL_GPIO_WritePin(I2C1_SDA_SEL1_GPIO_Port, I2C1_SDA_SEL1_Pin,
                          (sda_code & 0x02u) ? GPIO_PIN_SET : GPIO_PIN_RESET);
    }

    /**
     * @brief Scan I2C bus and log found devices
     */
    void MATRIX_I2C_Scan(void)
    {
        uint8_t found_devices = 0;
        LOG_Info("Starting I2C scan...");

        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                MATRIX_Select_Channel(x, y);
                if (HAL_I2C_IsDeviceReady(MATRIX_I2C_BUS,
                                          MATRIX_I2C_ADDR_BASE << 1,
                                          2, 10) == HAL_OK)
                {
                    LOG_Info("MLX90393 found at matrix (%u,%u)", x, y);
                    found_devices++;
                }
                else
                {
                    LOG_Error("MLX90393 not found at matrix (%u,%u)", x, y);
                }
            }
        }
        LOG_Info("Matrix scan complete. Found %d/8 sensor(s).", found_devices);
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
    HAL_StatusTypeDef MATRIX_Update_Baseline(void)
    {
        mlx90393_data_t candidate[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM] = {0};
        mlx90393_data_t snapshot[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];

        /* Dummy rounds to let sensor readings stabilize */
        for (uint16_t round = 0; round < MATRIX_EMPTY_ROUND; round++)
        {
            if (MATRIX_AcquireSnapshot(snapshot) != HAL_OK)
            {
                return HAL_ERROR;
            }
        }

        /* Accumulate baseline rounds */
        for (uint8_t round = 0; round < MATRIX_BASELINE_ROUND; round++)
        {
            if (MATRIX_AcquireSnapshot(snapshot) != HAL_OK)
            {
                return HAL_ERROR;
            }
            for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
            {
                for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
                {
                    candidate[x][y].x += snapshot[x][y].x;
                    candidate[x][y].y += snapshot[x][y].y;
                    candidate[x][y].z += snapshot[x][y].z;
                }
            }
        }

        /* Average */
        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                candidate[x][y].x /= MATRIX_BASELINE_ROUND;
                candidate[x][y].y /= MATRIX_BASELINE_ROUND;
                candidate[x][y].z /= MATRIX_BASELINE_ROUND;
            }
        }
        /* Commit only after every channel in every required round succeeded. */
        memcpy(g_baseline, candidate, sizeof(g_baseline));
        s_diagnostics.baseline_epoch++;
        if (s_diagnostics.baseline_epoch == 0u)
        {
            s_diagnostics.baseline_epoch = 1u;
        }
        return HAL_OK;
    }

    /**
     * @brief Update magnetic data from all MLX90393 sensors
     *
     * @param sub_baseline  If true, subtract baseline from readings
     */
    static HAL_StatusTypeDef MATRIX_AcquireSnapshot(
        mlx90393_data_t snapshot[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM])
    {
        uint8_t started_mask = 0u;
        s_diagnostics.valid_mask = 0u;
        /* Start measurement on all 8 sensors through the TMUX crosspoint. */
        for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
        {
            for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
            {
                MATRIX_Select_Channel(x, y);
                mlx90393_handle.i2c_slave_addr = MATRIX_I2C_ADDR_BASE;
                if (mlx90393_cmd_start_measurement(&mlx90393_handle) != HAL_OK)
                {
                    MATRIX_CountError();
                    LOG_Error("start measurement failed at matrix (%u,%u)", x, y);
                }
                else
                {
                    started_mask |= (uint8_t)(1u << (x * MATRIX_SDA_ROW_NUM + y));
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
                const uint8_t bit = (uint8_t)(1u << (x * MATRIX_SDA_ROW_NUM + y));
                /* A failed start cannot make an old conversion a new sample. */
                if ((started_mask & bit) == 0u)
                {
                    continue;
                }
                MATRIX_Select_Channel(x, y);
                mlx90393_handle.i2c_slave_addr = MATRIX_I2C_ADDR_BASE;
                if (mlx90393_cmd_read_measurement(&mlx90393_handle, &(snapshot[x][y])) != HAL_OK)
                {
                    MATRIX_CountError();
                    LOG_Error("read measurement failed at matrix (%u,%u)", x, y);
                }
                else
                {
                    s_diagnostics.valid_mask |= bit;
                }
            }
        }

        return s_diagnostics.valid_mask == MATRIX_VALID_MASK_ALL ? HAL_OK : HAL_ERROR;
    }

    HAL_StatusTypeDef MATRIX_Update_Magnetic(uint8_t sub_baseline)
    {
        mlx90393_data_t snapshot[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
        if (MATRIX_AcquireSnapshot(snapshot) != HAL_OK)
        {
            return HAL_ERROR;
        }

        /* Only fresh, fully valid readings may be baseline-subtracted. */
        if (sub_baseline)
        {
            for (uint8_t x = 0; x < MATRIX_SCL_COL_NUM; x++)
            {
                for (uint8_t y = 0; y < MATRIX_SDA_ROW_NUM; y++)
                {
                    snapshot[x][y].x -= g_baseline[x][y].x;
                    snapshot[x][y].y -= g_baseline[x][y].y;
                    snapshot[x][y].z -= g_baseline[x][y].z;
                }
            }
        }
        memcpy(g_data, snapshot, sizeof(g_data));
        return HAL_OK;
    }

    /**
     * @brief Update all measurements
     */
    HAL_StatusTypeDef MATRIX_Update_Measurement(uint8_t sub_baseline)
    {
        return MATRIX_Update_Magnetic(sub_baseline);
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
                MATRIX_Select_Channel(x, y);
                mlx90393_handle.i2c_slave_addr = MATRIX_I2C_ADDR_BASE;
                if (mlx90393_init(&mlx90393_handle) != HAL_OK)
                {
                    LOG_Error("mlx90393 init failed at matrix (%u,%u)", x, y);
                    status = HAL_ERROR;
                }
                else
                {
                    LOG_Info("mlx90393 init OK at matrix (%u,%u)", x, y);
                }
            }
        }
        LOG_Info("MLX90393 conv_time: %d ms", mlx90393_handle.conv_time);
        return status;
    }

#ifdef __cplusplus
}
#endif

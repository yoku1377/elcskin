#include "app.h"

#include <stdio.h>
#include <stdint.h>
#include <stdbool.h>

#include "iwdg.h"
#include "usart.h"

#include "matrix.h"
#include "regs.h"
#include "rs485.h"
#include "modbus_slave.h"
#include "mag_force.h"
#include "flash_config.h"
#include "log.h"
#include "iap_control.h"
#include "app_spi.h"

/*
 * 如果 printf 用的是 USART2，也就是 Modbus 那个口，必须关掉。
 * 否则 Modbus RTU 帧会被 printf 打乱。
 */
#ifndef APP_DEBUG_PRINT_FORCE
#define APP_DEBUG_PRINT_FORCE 0
#endif

/*
 * Calibration stream for the companion PC logger.  Frames are sent on
 * USART1 as: RAW,<device_ms>,B00x,B00y,B00z,...,B31x,B31y,B31z\r\n
 * The 24 B values are baseline-subtracted magnetic deltas.  10 Hz keeps the
 * 115200-baud link reliable while providing enough frames for stable holds.
 */
#ifndef APP_RAW_CALIBRATION_STREAM
#define APP_RAW_CALIBRATION_STREAM 1
#endif

#ifndef APP_RAW_CALIBRATION_PERIOD_MS
#define APP_RAW_CALIBRATION_PERIOD_MS 100u
#endif

#ifndef APP_FIRMWARE_BUILD_ID
#if APP_ENABLE_SPI
#define APP_FIRMWARE_BUILD_ID APP_SPI_FIRMWARE_ID
#else
#define APP_FIRMWARE_BUILD_ID "h3_diag_20261014_v1"
#endif
#endif

/*
 * force 写入 Modbus 寄存器时的缩放。
 * 例如 force = 12.34，则寄存器写入 1234。
 */
#ifndef APP_FORCE_REG_SCALE
#define APP_FORCE_REG_SCALE 100.0f
#endif

uint8_t g_measurement_disabled = 0;
/* Fatal sensor initialization/baseline state; individual failed samples retry. */
uint8_t g_error_flag = 0;

#if !APP_ENABLE_SPI
static modbus_slave_t s_modbus_slave;
#endif

static MagForce_Mag3D_t s_delta[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
static MagForce_Vec3_t s_force;

#if APP_RAW_CALIBRATION_STREAM
static uint32_t s_last_raw_stream_ms;
#endif

#if !APP_ENABLE_SPI
static int16_t app_float_to_i16(float v)
{
    if (v > 32767.0f)
    {
        return 32767;
    }

    if (v < -32768.0f)
    {
        return -32768;
    }

    return (int16_t)v;
}

static int16_t app_force_to_reg(float force_value)
{
    return app_float_to_i16(force_value * APP_FORCE_REG_SCALE);
}
#endif

/* Keep the serial protocol human-readable without linking newlib's large
 * floating-point printf formatter. Sensor and force streams retain 0.001
 * resolution and are parsed by the PC exactly as before. */
static void APP_PrintFixed3(float value)
{
    int32_t scaled = (int32_t)(value * 1000.0f + (value >= 0.0f ? 0.5f : -0.5f));
    uint32_t magnitude;
    if (scaled < 0)
    {
        printf("-");
        magnitude = (uint32_t)(-(scaled + 1)) + 1u;
    }
    else
    {
        magnitude = (uint32_t)scaled;
    }
    printf("%lu.%03lu", (unsigned long)(magnitude / 1000u),
           (unsigned long)(magnitude % 1000u));
}

static void APP_StreamRawCalibrationFrame(bool sample_valid)
{
#if APP_ENABLE_SPI
    APP_SPI_Publish(sample_valid && !g_measurement_disabled && !g_error_flag, &s_force);
#endif
#if APP_RAW_CALIBRATION_STREAM
    const uint32_t now = HAL_GetTick();
    matrix_diagnostics_t diagnostics;

    if ((uint32_t)(now - s_last_raw_stream_ms) < APP_RAW_CALIBRATION_PERIOD_MS)
    {
        return;
    }

    s_last_raw_stream_ms = now;
    MATRIX_GetDiagnostics(&diagnostics);
    /* DIAG,<ms>,<fw_id>,<valid_mask_decimal>,<errors>,<baseline_epoch>.
     * Invalid cycles never masquerade as fresh RAW/FORCE to older monitors. */
    if (g_measurement_disabled || g_error_flag)
    {
        diagnostics.valid_mask = 0u;
        sample_valid = false;
    }
    printf("DIAG,%lu,%s,%u,%lu,%lu\r\n", (unsigned long)now,
           APP_FIRMWARE_BUILD_ID, (unsigned int)diagnostics.valid_mask,
           (unsigned long)diagnostics.error_count,
           (unsigned long)diagnostics.baseline_epoch);
    if (!sample_valid)
    {
        return;
    }
    printf("RAW,%lu", (unsigned long)now);

    for (uint8_t col = 0; col < MATRIX_SCL_COL_NUM; col++)
    {
        for (uint8_t row = 0; row < MATRIX_SDA_ROW_NUM; row++)
        {
            printf(","); APP_PrintFixed3(g_data[col][row].x);
            printf(","); APP_PrintFixed3(g_data[col][row].y);
            printf(","); APP_PrintFixed3(g_data[col][row].z);
        }
    }

    printf("\r\n");
    printf("FORCE,%lu,", (unsigned long)now);
    APP_PrintFixed3(s_force.x); printf(",");
    APP_PrintFixed3(s_force.y); printf(",");
    APP_PrintFixed3(s_force.z); printf("\r\n");
#endif
}

#if !APP_ENABLE_SPI
static void APP_ModbusOnFrame(const uint8_t *rx, uint16_t rx_len)
{
    uint8_t tx[MODBUS_RTU_MAX_ADU_SIZE];
    size_t tx_len = 0u;
    modbus_slave_status_t st;

    st = modbus_slave_process_frame(
        &s_modbus_slave,
        rx,
        rx_len,
        tx,
        sizeof(tx),
        &tx_len);

    if ((st == MODBUS_SLAVE_OK) && (tx_len > 0u))
    {
        (void)rs485_port_send(tx, (uint16_t)tx_len);
    }
}

static void APP_ModbusInit(void)
{
    flash_config_t flash_cfg;
    modbus_regs_init();

    FLASH_CONFIG_Load(&flash_cfg);
    modbus_regs_set_slave_id_runtime(flash_cfg.slave_id);
    printf("Modbus slave ID: %u\r\n", flash_cfg.slave_id);
    modbus_slave_init(
        &s_modbus_slave,
        modbus_regs_get_slave_id(),
        modbus_regs_read_cb,
        modbus_regs_write_cb,
        0);

    modbus_regs_bind_slave(&s_modbus_slave);
    (void)rs485_port_init(
        &huart2,
        APP_ModbusOnFrame);

    (void)rs485_port_start_rx();
}

static void APP_ModbusLoop(void)
{
    rs485_port_poll();

    if (modbus_regs_take_zero_cal_request())
    {
        if (MATRIX_Update_Baseline() == HAL_OK)
        {
            LOG_Info("Matrix zero calibration done");
        }
        else
        {
            LOG_Error("Matrix zero calibration failed; previous baseline retained");
            APP_StreamRawCalibrationFrame(false);
        }
    }

    if (modbus_regs_take_save_config_request())
    {
    flash_config_t cfg;

        cfg.slave_id = modbus_regs_get_slave_id();

        HAL_IWDG_Refresh(&hiwdg);

        if (FLASH_CONFIG_Save(&cfg) == HAL_OK)
        {
            LOG_Info("Flash config saved");
        }
        else
        {
            LOG_Error("Flash config save failed");
        }

        HAL_IWDG_Refresh(&hiwdg);
    }

    if (modbus_regs_take_comm_reconfig_request())
    {
        /*
         * 地址已经实时更新。
         * 波特率、校验位、停止位建议保存后重启生效。
         */
        LOG_Info("Communication reconfig request");
    }

    if (modbus_regs_take_reboot_request())
    {
        /*
         * 如果确认需要远程重启，取消注释。
         */
        NVIC_SystemReset();
        LOG_Info("Reboot request");
    }
}

#endif

static void APP_MatrixInit(void)
{
    MATRIX_I2C_Scan();

    if (MATRIX_Init() != HAL_OK)
    {
        g_error_flag = 1u;
        LOG_Error("Matrix init failed");
        return;
    }

    LOG_Info("Matrix init success");

    if (MATRIX_Update_Baseline() != HAL_OK)
    {
        g_error_flag = 1u;
        LOG_Error("Matrix initial baseline failed; measurements disabled");
        return;
    }
    /* A preliminary scan can fail transiently. Only the completed sensor
     * initialization and whole-matrix baseline determine readiness. */
    g_error_flag = 0u;
    LOG_Info("Matrix baseline updated");
}

static void APP_MatrixUpdate(void)
{
    if (g_measurement_disabled || g_error_flag)
    {
        APP_StreamRawCalibrationFrame(false);
        return;
    }

    if (MATRIX_Update_Measurement(true) != HAL_OK)
    {
        APP_StreamRawCalibrationFrame(false);
        return;
    }

    for (uint8_t i = 0; i < MATRIX_SCL_COL_NUM; i++)
    {
        for (uint8_t j = 0; j < MATRIX_SDA_ROW_NUM; j++)
        {
            s_delta[i][j].x = g_data[i][j].x;
            s_delta[i][j].y = g_data[i][j].y;
            s_delta[i][j].z = g_data[i][j].z;
        }
    }
    MAG_FORCE_Calc3DNoCalib(s_delta, &s_force);

    /*
     * 这里先把整体 3D 力写到 Modbus force point 0。
     * 上位机可读 input registers:
     *   0x0010 = Fx
     *   0x0011 = Fy
     *   0x0012 = Fz
     *
     * 单位由 APP_FORCE_REG_SCALE 决定。
     * 默认 100 倍，即 0.01 为一个寄存器单位。
     */
#if !APP_ENABLE_SPI
    modbus_regs_set_force_xyz(
        0,
        app_force_to_reg(s_force.x),
        app_force_to_reg(s_force.y),
        app_force_to_reg(s_force.z));
    modbus_regs_inc_frame_counter();
#endif

    APP_StreamRawCalibrationFrame(true);

#if APP_DEBUG_PRINT_FORCE
    printf("%.2f, %.2f, %.2f\r\n", s_force.x, s_force.y, s_force.z);
#endif
}

void APP_Init(void)
{
    printf("TacAI-H3 starting; firmware=%s\r\n", APP_FIRMWARE_BUILD_ID);
    APP_MatrixInit();
#if !APP_ENABLE_SPI
    APP_ModbusInit();
#endif
}

void APP_Loop(void)
{
    /* A complete, exact command is required before resetting into the
     * immutable UART bootloader. Ordinary monitor traffic cannot trigger it. */
    IAP_ControlPoll();
#if APP_ENABLE_SPI
    APP_SPI_Poll();
#endif

    /*
     * 先处理 Modbus，避免主站请求堆积。
     */
#if !APP_ENABLE_SPI
    APP_ModbusLoop();
#endif

    /*
     * 更新传感器和寄存器数据。
     */
    APP_MatrixUpdate();

    /*
     * 再处理一次 Modbus，降低采样耗时带来的响应延迟。
     */
#if APP_ENABLE_SPI
    APP_SPI_Poll();
#else
    APP_ModbusLoop();
#endif

    HAL_IWDG_Refresh(&hiwdg);
}

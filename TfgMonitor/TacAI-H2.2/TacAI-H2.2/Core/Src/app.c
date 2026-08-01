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

/*
 * 如果 printf 用的是 USART2，也就是 Modbus 那个口，必须关掉。
 * 否则 Modbus RTU 帧会被 printf 打乱。
 */
#ifndef APP_DEBUG_PRINT_FORCE
#define APP_DEBUG_PRINT_FORCE 0
#endif

/*
 * force 写入 Modbus 寄存器时的缩放。
 * 例如 force = 12.34，则寄存器写入 1234。
 */
#ifndef APP_FORCE_REG_SCALE
#define APP_FORCE_REG_SCALE 100.0f
#endif

uint8_t g_measurement_disabled = 0;
uint8_t g_error_flag = 0;

static modbus_slave_t s_modbus_slave;

static MagForce_Mag3D_t s_delta[2][2];
static MagForce_Vec3_t s_force;

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
        MATRIX_Update_Baseline();
        LOG_Info("Matrix zero calibration done");
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

    MATRIX_Update_Baseline();
    LOG_Info("Matrix baseline updated");

    MATRIX_Update_Measurement(true);
}

static void APP_MatrixUpdate(void)
{
    if (g_measurement_disabled)
    {
        return;
    }

    MATRIX_Update_Measurement(true);

    for (uint8_t i = 0; i < 2; i++)
    {
        for (uint8_t j = 0; j < 2; j++)
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
    modbus_regs_set_force_xyz(
        0,
        app_force_to_reg(s_force.x),
        app_force_to_reg(s_force.y),
        app_force_to_reg(s_force.z));
    modbus_regs_inc_frame_counter();

#if APP_DEBUG_PRINT_FORCE
    printf("%.2f, %.2f, %.2f\r\n", s_force.x, s_force.y, s_force.z);
#endif
}

void APP_Init(void)
{
    printf("TacAI-H2.2 starting...\r\n");
    APP_MatrixInit();
    APP_ModbusInit();
}

void APP_Loop(void)
{
    /*
     * 先处理 Modbus，避免主站请求堆积。
     */
    APP_ModbusLoop();

    /*
     * 更新传感器和寄存器数据。
     */
    APP_MatrixUpdate();

    /*
     * 再处理一次 Modbus，降低采样耗时带来的响应延迟。
     */
    APP_ModbusLoop();

    HAL_IWDG_Refresh(&hiwdg);
}

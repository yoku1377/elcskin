#ifndef MODBUS_REGS_H
#define MODBUS_REGS_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include "modbus_slave.h"

    /*
     * TacAI-H2.x Modbus 寄存器规划
     *
     * Input Registers, 0x04:
     *   只读，主要放实时传感器数据、状态、电压、电流、力数据
     *
     * Holding Registers, 0x03/0x06/0x10:
     *   可读可写，主要放设备地址、波特率、滤波参数、校准命令等配置
     */

#define MODBUS_REGS_INPUT_COUNT 128u
#define MODBUS_REGS_HOLDING_COUNT 64u

#define MODBUS_REGS_FORCE_POINT_COUNT 12u
#define MODBUS_REGS_FORCE_AXIS_COUNT 3u

#define MODBUS_REGS_DEFAULT_SLAVE_ID 1u

/*
 * 写入命令值
 */
#define MODBUS_REGS_CMD_ZERO_CAL 0xA55Au
#define MODBUS_REGS_CMD_SAVE_CONFIG 0x5AA5u
#define MODBUS_REGS_CMD_REBOOT 0xCAFEu

    /*
     * Input Registers 地址表，功能码 0x04
     */
    typedef enum
    {
        MB_IREG_DEVICE_STATUS = 0x0000,
        MB_IREG_FW_VERSION = 0x0001,
        MB_IREG_PROTOCOL_VERSION = 0x0002,
        MB_IREG_SAMPLE_RATE_HZ = 0x0003,
        MB_IREG_SUPPLY_MV = 0x0004,
        MB_IREG_CURRENT_MA = 0x0005,
        MB_IREG_TEMPERATURE_0P1C = 0x0006,
        MB_IREG_FRAME_COUNTER_L = 0x0007,
        MB_IREG_FRAME_COUNTER_H = 0x0008,
        MB_IREG_ERROR_CODE = 0x0009,

        /*
         * 4x3 = 12 个力点，每个点 xyz 三轴
         *
         * 地址：
         *   点 i 的 Fx = MB_IREG_FORCE_BASE + i * 3 + 0
         *   点 i 的 Fy = MB_IREG_FORCE_BASE + i * 3 + 1
         *   点 i 的 Fz = MB_IREG_FORCE_BASE + i * 3 + 2
         *
         * 数据格式：
         *   int16_t，有符号
         *   单位建议：mN 或 0.01N，由上位机约定
         */
        MB_IREG_FORCE_BASE = 0x0010,

        /*
         * 预留给原始磁场 / 调试数据
         */
        MB_IREG_RAW_BASE = 0x0040
    } modbus_input_reg_addr_t;

#define MB_IREG_FORCE_X(i) ((uint16_t)(MB_IREG_FORCE_BASE + ((uint16_t)(i) * 3u) + 0u))
#define MB_IREG_FORCE_Y(i) ((uint16_t)(MB_IREG_FORCE_BASE + ((uint16_t)(i) * 3u) + 1u))
#define MB_IREG_FORCE_Z(i) ((uint16_t)(MB_IREG_FORCE_BASE + ((uint16_t)(i) * 3u) + 2u))

    /*
     * Holding Registers 地址表，功能码 0x03/0x06/0x10
     */
    typedef enum
    {
        MB_HREG_SLAVE_ID = 0x0000,
        MB_HREG_BAUD_INDEX = 0x0001,
        MB_HREG_PARITY = 0x0002,
        MB_HREG_STOP_BITS = 0x0003,
        MB_HREG_SAMPLE_RATE_HZ = 0x0004,
        MB_HREG_FILTER_LEVEL = 0x0005,
        MB_HREG_OUTPUT_MODE = 0x0006,

        /*
         * 写 0xA55A 触发零点校准
         */
        MB_HREG_ZERO_CAL_CMD = 0x0007,

        /*
         * 写 0x5AA5 触发保存配置
         */
        MB_HREG_SAVE_CONFIG_CMD = 0x0008,

        /*
         * 写 0xCAFE 触发重启
         */
        MB_HREG_REBOOT_CMD = 0x0009,

        MB_HREG_LED_MODE = 0x000A,

        /*
         * 用户自定义参数区
         */
        MB_HREG_USER_BASE = 0x0020
    } modbus_holding_reg_addr_t;

    typedef enum
    {
        MB_BAUD_9600 = 0,
        MB_BAUD_19200 = 1,
        MB_BAUD_38400 = 2,
        MB_BAUD_57600 = 3,
        MB_BAUD_115200 = 4
    } modbus_baud_index_t;

    typedef enum
    {
        MB_PARITY_NONE = 0,
        MB_PARITY_EVEN = 1,
        MB_PARITY_ODD = 2
    } modbus_parity_t;

    void modbus_regs_init(void);

    /*
     * 可选：绑定 modbus_slave_t。
     * 这样写 MB_HREG_SLAVE_ID 时，可以自动更新协议栈里的 slave_id。
     */
    void modbus_regs_bind_slave(modbus_slave_t *slave);

    /*
     * 给 modbus_slave_init() 使用的回调
     */
    int modbus_regs_read_cb(
        void *user_ctx,
        uint8_t function,
        uint16_t address,
        uint16_t *value);

    int modbus_regs_write_cb(
        void *user_ctx,
        uint8_t function,
        uint16_t address,
        uint16_t value);

    /*
     * 常用读接口
     */
    uint8_t modbus_regs_get_slave_id(void);
    uint8_t modbus_regs_get_baud_index(void);
    uint32_t modbus_regs_get_baudrate(void);
    uint16_t modbus_regs_get_sample_rate_hz(void);
    uint16_t modbus_regs_get_filter_level(void);
    uint16_t modbus_regs_get_output_mode(void);

    /*
     * 实时数据更新接口
     */
    void modbus_regs_set_input_u16(uint16_t address, uint16_t value);
    void modbus_regs_set_input_i16(uint16_t address, int16_t value);

    void modbus_regs_set_status(uint16_t status);
    void modbus_regs_set_error_code(uint16_t error_code);
    void modbus_regs_set_supply(uint16_t mv, uint16_t ma);
    void modbus_regs_set_temperature_0p1c(int16_t temp_0p1c);
    void modbus_regs_set_sample_rate_report(uint16_t hz);

    void modbus_regs_set_force_xyz(
        uint8_t point_index,
        int16_t fx,
        int16_t fy,
        int16_t fz);

    void modbus_regs_inc_frame_counter(void);

    /*
     * 命令事件读取。读取后自动清除。
     */
    uint8_t modbus_regs_take_zero_cal_request(void);
    uint8_t modbus_regs_take_save_config_request(void);
    uint8_t modbus_regs_take_reboot_request(void);

    /*
     * 通讯参数被修改，例如地址、波特率、校验位、停止位。
     * 主循环可以根据这个标志决定是否保存配置/重启串口。
     */
    uint8_t modbus_regs_take_comm_reconfig_request(void);
    
    void modbus_regs_set_slave_id_runtime(uint8_t slave_id);
#ifdef __cplusplus
}
#endif

#endif
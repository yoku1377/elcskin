#ifndef MODBUS_SLAVE_H
#define MODBUS_SLAVE_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include <stddef.h>

/*
 * Modbus RTU 最大 ADU 长度：
 * Address 1 byte + PDU 最多 253 bytes + CRC 2 bytes = 256 bytes
 */
#define MODBUS_RTU_MAX_ADU_SIZE        256u

#define MODBUS_FC_READ_HOLDING_REGS    0x03u
#define MODBUS_FC_READ_INPUT_REGS      0x04u
#define MODBUS_FC_WRITE_SINGLE_REG     0x06u
#define MODBUS_FC_WRITE_MULTIPLE_REGS  0x10u

#define MODBUS_MAX_READ_REGS           125u
#define MODBUS_MAX_WRITE_REGS          123u

typedef enum
{
    MODBUS_EX_NONE                  = 0x00,
    MODBUS_EX_ILLEGAL_FUNCTION      = 0x01,
    MODBUS_EX_ILLEGAL_DATA_ADDRESS  = 0x02,
    MODBUS_EX_ILLEGAL_DATA_VALUE    = 0x03,
    MODBUS_EX_SERVER_DEVICE_FAILURE = 0x04
} modbus_exception_t;

typedef enum
{
    MODBUS_SLAVE_OK = 0,
    MODBUS_SLAVE_NO_RESPONSE,
    MODBUS_SLAVE_BAD_ARGUMENT,
    MODBUS_SLAVE_BAD_LENGTH,
    MODBUS_SLAVE_BAD_CRC,
    MODBUS_SLAVE_TX_OVERFLOW
} modbus_slave_status_t;

/*
 * 读寄存器回调
 *
 * function:
 *   0x03 = holding register
 *   0x04 = input register
 *
 * address:
 *   Modbus 寄存器地址，从 0 开始
 *
 * value:
 *   返回 16-bit 寄存器值
 *
 * 返回值:
 *   0 表示成功
 *   非 0 建议返回 modbus_exception_t，例如 MODBUS_EX_ILLEGAL_DATA_ADDRESS
 */
typedef int (*modbus_read_reg_cb_t)(
    void *user_ctx,
    uint8_t function,
    uint16_t address,
    uint16_t *value
);

/*
 * 写寄存器回调
 *
 * function:
 *   0x06 = write single register
 *   0x10 = write multiple registers
 *
 * address:
 *   Modbus 寄存器地址，从 0 开始
 *
 * value:
 *   要写入的 16-bit 寄存器值
 *
 * 返回值:
 *   0 表示成功
 *   非 0 建议返回 modbus_exception_t
 */
typedef int (*modbus_write_reg_cb_t)(
    void *user_ctx,
    uint8_t function,
    uint16_t address,
    uint16_t value
);

typedef struct
{
    uint8_t slave_id;

    modbus_read_reg_cb_t read_reg;
    modbus_write_reg_cb_t write_reg;

    void *user_ctx;
} modbus_slave_t;

void modbus_slave_init(
    modbus_slave_t *ctx,
    uint8_t slave_id,
    modbus_read_reg_cb_t read_cb,
    modbus_write_reg_cb_t write_cb,
    void *user_ctx
);

void modbus_slave_set_id(modbus_slave_t *ctx, uint8_t slave_id);

uint16_t modbus_crc16(const uint8_t *data, size_t len);

/*
 * 处理一帧完整 Modbus RTU 数据
 *
 * rx:
 *   UART 层接收到的一整帧 RTU 数据，包含 slave id 和 CRC
 *
 * rx_len:
 *   rx 数据长度
 *
 * tx:
 *   输出响应帧缓冲区
 *
 * tx_max:
 *   tx 缓冲区最大长度，建议 >= MODBUS_RTU_MAX_ADU_SIZE
 *
 * tx_len:
 *   输出响应帧长度
 *
 * 返回:
 *   MODBUS_SLAVE_OK:
 *     处理成功。如果 tx_len > 0，需要 UART 层发送 tx。
 *
 *   MODBUS_SLAVE_NO_RESPONSE:
 *     不需要响应，例如地址不匹配、广播帧、CRC 错误等。
 *
 *   其他:
 *     参数或缓冲区错误。
 */
modbus_slave_status_t modbus_slave_process_frame(
    modbus_slave_t *ctx,
    const uint8_t *rx,
    size_t rx_len,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len
);

#ifdef __cplusplus
}
#endif

#endif
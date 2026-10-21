#include "modbus_slave.h"

#define MODBUS_BROADCAST_ID  0u

static uint16_t mb_get_be16(const uint8_t *p)
{
    return (uint16_t)(((uint16_t)p[0] << 8) | (uint16_t)p[1]);
}

static void mb_put_be16(uint8_t *p, uint16_t v)
{
    p[0] = (uint8_t)((v >> 8) & 0xFFu);
    p[1] = (uint8_t)(v & 0xFFu);
}

static uint8_t mb_cb_result_to_exception(int ret)
{
    if (ret == 0)
    {
        return MODBUS_EX_NONE;
    }

    switch (ret)
    {
    case MODBUS_EX_ILLEGAL_FUNCTION:
    case MODBUS_EX_ILLEGAL_DATA_ADDRESS:
    case MODBUS_EX_ILLEGAL_DATA_VALUE:
    case MODBUS_EX_SERVER_DEVICE_FAILURE:
        return (uint8_t)ret;

    default:
        return MODBUS_EX_SERVER_DEVICE_FAILURE;
    }
}

static modbus_slave_status_t mb_append_crc(
    uint8_t *tx,
    size_t tx_max,
    size_t payload_len,
    size_t *tx_len
)
{
    uint16_t crc;

    if ((payload_len + 2u) > tx_max)
    {
        *tx_len = 0u;
        return MODBUS_SLAVE_TX_OVERFLOW;
    }

    crc = modbus_crc16(tx, payload_len);

    /*
     * Modbus RTU CRC 低字节在前，高字节在后
     */
    tx[payload_len] = (uint8_t)(crc & 0xFFu);
    tx[payload_len + 1u] = (uint8_t)((crc >> 8) & 0xFFu);

    *tx_len = payload_len + 2u;
    return MODBUS_SLAVE_OK;
}

static modbus_slave_status_t mb_build_exception(
    uint8_t slave_id,
    uint8_t function,
    uint8_t exception_code,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len
)
{
    if (tx_max < 5u)
    {
        *tx_len = 0u;
        return MODBUS_SLAVE_TX_OVERFLOW;
    }

    tx[0] = slave_id;
    tx[1] = (uint8_t)(function | 0x80u);
    tx[2] = exception_code;

    return mb_append_crc(tx, tx_max, 3u, tx_len);
}

static modbus_slave_status_t mb_handle_read_regs(
    modbus_slave_t *ctx,
    uint8_t function,
    const uint8_t *rx,
    size_t rx_len,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len,
    uint8_t is_broadcast
)
{
    uint16_t start_addr;
    uint16_t quantity;
    uint16_t value;
    uint8_t byte_count;
    uint8_t ex;
    size_t payload_len;
    uint16_t i;

    /*
     * Read Holding/Input Registers 请求固定 8 字节：
     * id, fc, addr_hi, addr_lo, qty_hi, qty_lo, crc_lo, crc_hi
     */
    if (rx_len != 8u)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    if (is_broadcast)
    {
        /*
         * 广播读没有意义，不响应
         */
        return MODBUS_SLAVE_NO_RESPONSE;
    }

    if (ctx->read_reg == 0)
    {
        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_FUNCTION,
            tx,
            tx_max,
            tx_len
        );
    }

    start_addr = mb_get_be16(&rx[2]);
    quantity = mb_get_be16(&rx[4]);

    if ((quantity == 0u) || (quantity > MODBUS_MAX_READ_REGS))
    {
        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    byte_count = (uint8_t)(quantity * 2u);
    payload_len = 3u + (size_t)byte_count;

    if ((payload_len + 2u) > tx_max)
    {
        *tx_len = 0u;
        return MODBUS_SLAVE_TX_OVERFLOW;
    }

    tx[0] = rx[0];
    tx[1] = function;
    tx[2] = byte_count;

    for (i = 0u; i < quantity; i++)
    {
        int ret;
        uint16_t addr = (uint16_t)(start_addr + i);

        ret = ctx->read_reg(ctx->user_ctx, function, addr, &value);
        ex = mb_cb_result_to_exception(ret);

        if (ex != MODBUS_EX_NONE)
        {
            return mb_build_exception(
                rx[0],
                function,
                ex,
                tx,
                tx_max,
                tx_len
            );
        }

        mb_put_be16(&tx[3u + ((size_t)i * 2u)], value);
    }

    return mb_append_crc(tx, tx_max, payload_len, tx_len);
}

static modbus_slave_status_t mb_handle_write_single_reg(
    modbus_slave_t *ctx,
    uint8_t function,
    const uint8_t *rx,
    size_t rx_len,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len,
    uint8_t is_broadcast
)
{
    uint16_t address;
    uint16_t value;
    uint8_t ex;
    int ret;

    /*
     * Write Single Register 请求固定 8 字节：
     * id, fc, addr_hi, addr_lo, val_hi, val_lo, crc_lo, crc_hi
     */
    if (rx_len != 8u)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    if (ctx->write_reg == 0)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_FUNCTION,
            tx,
            tx_max,
            tx_len
        );
    }

    address = mb_get_be16(&rx[2]);
    value = mb_get_be16(&rx[4]);

    ret = ctx->write_reg(ctx->user_ctx, function, address, value);
    ex = mb_cb_result_to_exception(ret);

    if (ex != MODBUS_EX_NONE)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            ex,
            tx,
            tx_max,
            tx_len
        );
    }

    if (is_broadcast)
    {
        return MODBUS_SLAVE_NO_RESPONSE;
    }

    /*
     * 0x06 正常响应是原请求前 6 字节回显
     */
    if (tx_max < 8u)
    {
        *tx_len = 0u;
        return MODBUS_SLAVE_TX_OVERFLOW;
    }

    tx[0] = rx[0];
    tx[1] = rx[1];
    tx[2] = rx[2];
    tx[3] = rx[3];
    tx[4] = rx[4];
    tx[5] = rx[5];

    return mb_append_crc(tx, tx_max, 6u, tx_len);
}

static modbus_slave_status_t mb_handle_write_multiple_regs(
    modbus_slave_t *ctx,
    uint8_t function,
    const uint8_t *rx,
    size_t rx_len,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len,
    uint8_t is_broadcast
)
{
    uint16_t start_addr;
    uint16_t quantity;
    uint8_t byte_count;
    uint16_t i;
    uint8_t ex;

    /*
     * Write Multiple Registers 请求：
     * id, fc, addr_hi, addr_lo, qty_hi, qty_lo, byte_count, data..., crc_lo, crc_hi
     *
     * 最短长度：
     * id 1 + fc 1 + addr 2 + qty 2 + byte_count 1 + data 2 + crc 2 = 11
     */
    if (rx_len < 11u)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    if (ctx->write_reg == 0)
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_FUNCTION,
            tx,
            tx_max,
            tx_len
        );
    }

    start_addr = mb_get_be16(&rx[2]);
    quantity = mb_get_be16(&rx[4]);
    byte_count = rx[6];

    if ((quantity == 0u) || (quantity > MODBUS_MAX_WRITE_REGS))
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    if (byte_count != (uint8_t)(quantity * 2u))
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    /*
     * 总长度必须精确匹配：
     * 7 字节头 + byte_count 数据 + 2 字节 CRC
     */
    if (rx_len != (size_t)(9u + byte_count))
    {
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            rx[0],
            function,
            MODBUS_EX_ILLEGAL_DATA_VALUE,
            tx,
            tx_max,
            tx_len
        );
    }

    for (i = 0u; i < quantity; i++)
    {
        uint16_t addr;
        uint16_t value;
        int ret;

        addr = (uint16_t)(start_addr + i);
        value = mb_get_be16(&rx[7u + ((size_t)i * 2u)]);

        ret = ctx->write_reg(ctx->user_ctx, function, addr, value);
        ex = mb_cb_result_to_exception(ret);

        if (ex != MODBUS_EX_NONE)
        {
            if (is_broadcast)
            {
                return MODBUS_SLAVE_NO_RESPONSE;
            }

            return mb_build_exception(
                rx[0],
                function,
                ex,
                tx,
                tx_max,
                tx_len
            );
        }
    }

    if (is_broadcast)
    {
        return MODBUS_SLAVE_NO_RESPONSE;
    }

    /*
     * 0x10 正常响应：
     * id, fc, start_addr_hi, start_addr_lo, qty_hi, qty_lo, crc_lo, crc_hi
     */
    if (tx_max < 8u)
    {
        *tx_len = 0u;
        return MODBUS_SLAVE_TX_OVERFLOW;
    }

    tx[0] = rx[0];
    tx[1] = function;
    tx[2] = rx[2];
    tx[3] = rx[3];
    tx[4] = rx[4];
    tx[5] = rx[5];

    return mb_append_crc(tx, tx_max, 6u, tx_len);
}

void modbus_slave_init(
    modbus_slave_t *ctx,
    uint8_t slave_id,
    modbus_read_reg_cb_t read_cb,
    modbus_write_reg_cb_t write_cb,
    void *user_ctx
)
{
    if (ctx == 0)
    {
        return;
    }

    ctx->slave_id = slave_id;
    ctx->read_reg = read_cb;
    ctx->write_reg = write_cb;
    ctx->user_ctx = user_ctx;
}

void modbus_slave_set_id(modbus_slave_t *ctx, uint8_t slave_id)
{
    if (ctx == 0)
    {
        return;
    }

    ctx->slave_id = slave_id;
}

uint16_t modbus_crc16(const uint8_t *data, size_t len)
{
    uint16_t crc = 0xFFFFu;
    size_t i;

    for (i = 0u; i < len; i++)
    {
        uint8_t j;

        crc ^= (uint16_t)data[i];

        for (j = 0u; j < 8u; j++)
        {
            if ((crc & 0x0001u) != 0u)
            {
                crc >>= 1;
                crc ^= 0xA001u;
            }
            else
            {
                crc >>= 1;
            }
        }
    }

    return crc;
}

modbus_slave_status_t modbus_slave_process_frame(
    modbus_slave_t *ctx,
    const uint8_t *rx,
    size_t rx_len,
    uint8_t *tx,
    size_t tx_max,
    size_t *tx_len
)
{
    uint16_t crc_calc;
    uint16_t crc_recv;
    uint8_t slave_id;
    uint8_t function;
    uint8_t is_broadcast;

    if (tx_len != 0)
    {
        *tx_len = 0u;
    }

    if ((ctx == 0) || (rx == 0) || (tx == 0) || (tx_len == 0))
    {
        return MODBUS_SLAVE_BAD_ARGUMENT;
    }

    /*
     * 最短 RTU 帧：
     * id 1 + function 1 + crc 2 = 4
     */
    if (rx_len < 4u)
    {
        return MODBUS_SLAVE_BAD_LENGTH;
    }

    if (rx_len > MODBUS_RTU_MAX_ADU_SIZE)
    {
        return MODBUS_SLAVE_BAD_LENGTH;
    }

    slave_id = rx[0];
    function = rx[1];

    /*
     * 地址不匹配，直接静默
     */
    if ((slave_id != ctx->slave_id) && (slave_id != MODBUS_BROADCAST_ID))
    {
        return MODBUS_SLAVE_NO_RESPONSE;
    }

    crc_calc = modbus_crc16(rx, rx_len - 2u);
    crc_recv = (uint16_t)(((uint16_t)rx[rx_len - 1u] << 8) | rx[rx_len - 2u]);

    if (crc_calc != crc_recv)
    {
        /*
         * CRC 错误不响应
         */
        return MODBUS_SLAVE_BAD_CRC;
    }

    is_broadcast = (slave_id == MODBUS_BROADCAST_ID) ? 1u : 0u;

    switch (function)
    {
    case MODBUS_FC_READ_HOLDING_REGS:
    case MODBUS_FC_READ_INPUT_REGS:
        return mb_handle_read_regs(
            ctx,
            function,
            rx,
            rx_len,
            tx,
            tx_max,
            tx_len,
            is_broadcast
        );

    case MODBUS_FC_WRITE_SINGLE_REG:
        return mb_handle_write_single_reg(
            ctx,
            function,
            rx,
            rx_len,
            tx,
            tx_max,
            tx_len,
            is_broadcast
        );

    case MODBUS_FC_WRITE_MULTIPLE_REGS:
        return mb_handle_write_multiple_regs(
            ctx,
            function,
            rx,
            rx_len,
            tx,
            tx_max,
            tx_len,
            is_broadcast
        );

    default:
        if (is_broadcast)
        {
            return MODBUS_SLAVE_NO_RESPONSE;
        }

        return mb_build_exception(
            slave_id,
            function,
            MODBUS_EX_ILLEGAL_FUNCTION,
            tx,
            tx_max,
            tx_len
        );
    }
}
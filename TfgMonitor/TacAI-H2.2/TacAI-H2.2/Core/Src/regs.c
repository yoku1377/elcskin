#include "regs.h"
#include <string.h>

#define FW_VERSION_ENCODED        0x2201u
#define PROTOCOL_VERSION_ENCODED  0x5978u

static uint16_t s_input_regs[MODBUS_REGS_INPUT_COUNT];
static uint16_t s_holding_regs[MODBUS_REGS_HOLDING_COUNT];

static modbus_slave_t *s_bound_slave = 0;

static volatile uint8_t s_zero_cal_requested = 0;
static volatile uint8_t s_save_config_requested = 0;
static volatile uint8_t s_reboot_requested = 0;
static volatile uint8_t s_comm_reconfig_requested = 0;

static uint8_t is_valid_holding_addr(uint16_t address)
{
    return (address < MODBUS_REGS_HOLDING_COUNT) ? 1u : 0u;
}

static uint8_t is_valid_input_addr(uint16_t address)
{
    return (address < MODBUS_REGS_INPUT_COUNT) ? 1u : 0u;
}

static uint8_t is_user_holding_addr(uint16_t address)
{
    return ((address >= MB_HREG_USER_BASE) &&
            (address < MODBUS_REGS_HOLDING_COUNT)) ? 1u : 0u;
}

void modbus_regs_init(void)
{
    memset(s_input_regs, 0, sizeof(s_input_regs));
    memset(s_holding_regs, 0, sizeof(s_holding_regs));

    s_input_regs[MB_IREG_FW_VERSION] = FW_VERSION_ENCODED;
    s_input_regs[MB_IREG_PROTOCOL_VERSION] = PROTOCOL_VERSION_ENCODED;
    s_input_regs[MB_IREG_SAMPLE_RATE_HZ] = 250u;

    s_holding_regs[MB_HREG_SLAVE_ID] = MODBUS_REGS_DEFAULT_SLAVE_ID;
    s_holding_regs[MB_HREG_BAUD_INDEX] = MB_BAUD_115200;
    s_holding_regs[MB_HREG_PARITY] = MB_PARITY_NONE;
    s_holding_regs[MB_HREG_STOP_BITS] = 1u;
    s_holding_regs[MB_HREG_SAMPLE_RATE_HZ] = 100u;
    s_holding_regs[MB_HREG_FILTER_LEVEL] = 0u;
    s_holding_regs[MB_HREG_OUTPUT_MODE] = 0u;
    s_holding_regs[MB_HREG_LED_MODE] = 1u;

    s_zero_cal_requested = 0u;
    s_save_config_requested = 0u;
    s_reboot_requested = 0u;
    s_comm_reconfig_requested = 0u;
}

void modbus_regs_bind_slave(modbus_slave_t *slave)
{
    s_bound_slave = slave;

    if (s_bound_slave != 0)
    {
        modbus_slave_set_id(s_bound_slave, modbus_regs_get_slave_id());
    }
}

uint8_t modbus_regs_get_slave_id(void)
{
    uint16_t id = s_holding_regs[MB_HREG_SLAVE_ID];

    if ((id == 0u) || (id > 247u))
    {
        return MODBUS_REGS_DEFAULT_SLAVE_ID;
    }

    return (uint8_t)id;
}

uint8_t modbus_regs_get_baud_index(void)
{
    return (uint8_t)s_holding_regs[MB_HREG_BAUD_INDEX];
}

uint32_t modbus_regs_get_baudrate(void)
{
    switch (s_holding_regs[MB_HREG_BAUD_INDEX])
    {
    case MB_BAUD_9600:
        return 9600u;

    case MB_BAUD_19200:
        return 19200u;

    case MB_BAUD_38400:
        return 38400u;

    case MB_BAUD_57600:
        return 57600u;

    case MB_BAUD_115200:
    default:
        return 115200u;
    }
}

uint16_t modbus_regs_get_sample_rate_hz(void)
{
    return s_holding_regs[MB_HREG_SAMPLE_RATE_HZ];
}

uint16_t modbus_regs_get_filter_level(void)
{
    return s_holding_regs[MB_HREG_FILTER_LEVEL];
}

uint16_t modbus_regs_get_output_mode(void)
{
    return s_holding_regs[MB_HREG_OUTPUT_MODE];
}

static int write_holding_reg(uint16_t address, uint16_t value)
{
    if (!is_valid_holding_addr(address))
    {
        return MODBUS_EX_ILLEGAL_DATA_ADDRESS;
    }

    switch (address)
    {
    case MB_HREG_SLAVE_ID:
        /*
         * Modbus RTU 合法从机地址：1~247
         * 0 是广播地址，不建议作为本机地址
         */
        if ((value < 1u) || (value > 247u))
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        s_holding_regs[address] = value;
        s_comm_reconfig_requested = 1u;

        if (s_bound_slave != 0)
        {
            modbus_slave_set_id(s_bound_slave, (uint8_t)value);
        }
        break;

    case MB_HREG_BAUD_INDEX:
        if (value > MB_BAUD_115200)
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        if (s_holding_regs[address] != value)
        {
            s_comm_reconfig_requested = 1u;
        }

        s_holding_regs[address] = value;
        break;

    case MB_HREG_PARITY:
        if (value > MB_PARITY_ODD)
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        if (s_holding_regs[address] != value)
        {
            s_comm_reconfig_requested = 1u;
        }

        s_holding_regs[address] = value;
        break;

    case MB_HREG_STOP_BITS:
        if ((value != 1u) && (value != 2u))
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        if (s_holding_regs[address] != value)
        {
            s_comm_reconfig_requested = 1u;
        }

        s_holding_regs[address] = value;
        break;

    case MB_HREG_SAMPLE_RATE_HZ:
        /*
         * 按你的传感器输出能力改范围。
         * 这里先限制在 1~1000 Hz。
         */
        if ((value < 1u) || (value > 1000u))
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        s_holding_regs[address] = value;
        s_input_regs[MB_IREG_SAMPLE_RATE_HZ] = value;
        break;

    case MB_HREG_FILTER_LEVEL:
        /*
         * 0 表示关闭滤波，数值越大滤波越强。
         * 具体含义由你的应用层解释。
         */
        if (value > 1000u)
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        s_holding_regs[address] = value;
        break;

    case MB_HREG_OUTPUT_MODE:
        /*
         * 0: 默认力数据
         * 1: 原始磁场
         * 2: 调试模式
         */
        if (value > 2u)
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        s_holding_regs[address] = value;
        break;

    case MB_HREG_ZERO_CAL_CMD:
        if (value == MODBUS_REGS_CMD_ZERO_CAL)
        {
            s_zero_cal_requested = 1u;
            s_holding_regs[address] = 0u;
        }
        else if (value == 0u)
        {
            s_holding_regs[address] = 0u;
        }
        else
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }
        break;

    case MB_HREG_SAVE_CONFIG_CMD:
        if (value == MODBUS_REGS_CMD_SAVE_CONFIG)
        {
            s_save_config_requested = 1u;
            s_holding_regs[address] = 0u;
        }
        else if (value == 0u)
        {
            s_holding_regs[address] = 0u;
        }
        else
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }
        break;

    case MB_HREG_REBOOT_CMD:
        if (value == MODBUS_REGS_CMD_REBOOT)
        {
            s_reboot_requested = 1u;
            s_holding_regs[address] = 0u;
        }
        else if (value == 0u)
        {
            s_holding_regs[address] = 0u;
        }
        else
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }
        break;

    case MB_HREG_LED_MODE:
        if (value > 3u)
        {
            return MODBUS_EX_ILLEGAL_DATA_VALUE;
        }

        s_holding_regs[address] = value;
        break;

    default:
        /*
         * 用户参数区允许自由写入。
         * 非用户参数区的空洞地址，返回非法地址。
         */
        if (is_user_holding_addr(address))
        {
            s_holding_regs[address] = value;
        }
        else
        {
            return MODBUS_EX_ILLEGAL_DATA_ADDRESS;
        }
        break;
    }

    return MODBUS_EX_NONE;
}

int modbus_regs_read_cb(
    void *user_ctx,
    uint8_t function,
    uint16_t address,
    uint16_t *value
)
{
    (void)user_ctx;

    if (value == 0)
    {
        return MODBUS_EX_SERVER_DEVICE_FAILURE;
    }

    if (function == MODBUS_FC_READ_INPUT_REGS)
    {
        if (!is_valid_input_addr(address))
        {
            return MODBUS_EX_ILLEGAL_DATA_ADDRESS;
        }

        *value = s_input_regs[address];
        return MODBUS_EX_NONE;
    }

    if (function == MODBUS_FC_READ_HOLDING_REGS)
    {
        if (!is_valid_holding_addr(address))
        {
            return MODBUS_EX_ILLEGAL_DATA_ADDRESS;
        }

        *value = s_holding_regs[address];
        return MODBUS_EX_NONE;
    }

    return MODBUS_EX_ILLEGAL_FUNCTION;
}

int modbus_regs_write_cb(
    void *user_ctx,
    uint8_t function,
    uint16_t address,
    uint16_t value
)
{
    (void)user_ctx;

    if ((function != MODBUS_FC_WRITE_SINGLE_REG) &&
        (function != MODBUS_FC_WRITE_MULTIPLE_REGS))
    {
        return MODBUS_EX_ILLEGAL_FUNCTION;
    }

    return write_holding_reg(address, value);
}

void modbus_regs_set_input_u16(uint16_t address, uint16_t value)
{
    if (is_valid_input_addr(address))
    {
        s_input_regs[address] = value;
    }
}

void modbus_regs_set_input_i16(uint16_t address, int16_t value)
{
    if (is_valid_input_addr(address))
    {
        s_input_regs[address] = (uint16_t)value;
    }
}

void modbus_regs_set_status(uint16_t status)
{
    s_input_regs[MB_IREG_DEVICE_STATUS] = status;
}

void modbus_regs_set_error_code(uint16_t error_code)
{
    s_input_regs[MB_IREG_ERROR_CODE] = error_code;
}

void modbus_regs_set_supply(uint16_t mv, uint16_t ma)
{
    s_input_regs[MB_IREG_SUPPLY_MV] = mv;
    s_input_regs[MB_IREG_CURRENT_MA] = ma;
}

void modbus_regs_set_temperature_0p1c(int16_t temp_0p1c)
{
    s_input_regs[MB_IREG_TEMPERATURE_0P1C] = (uint16_t)temp_0p1c;
}

void modbus_regs_set_sample_rate_report(uint16_t hz)
{
    s_input_regs[MB_IREG_SAMPLE_RATE_HZ] = hz;
}

void modbus_regs_set_force_xyz(
    uint8_t point_index,
    int16_t fx,
    int16_t fy,
    int16_t fz
)
{
    if (point_index >= MODBUS_REGS_FORCE_POINT_COUNT)
    {
        return;
    }

    s_input_regs[MB_IREG_FORCE_X(point_index)] = (uint16_t)fx;
    s_input_regs[MB_IREG_FORCE_Y(point_index)] = (uint16_t)fy;
    s_input_regs[MB_IREG_FORCE_Z(point_index)] = (uint16_t)fz;
}

void modbus_regs_inc_frame_counter(void)
{
    uint32_t counter;

    counter = ((uint32_t)s_input_regs[MB_IREG_FRAME_COUNTER_H] << 16) |
               (uint32_t)s_input_regs[MB_IREG_FRAME_COUNTER_L];

    counter++;

    s_input_regs[MB_IREG_FRAME_COUNTER_L] = (uint16_t)(counter & 0xFFFFu);
    s_input_regs[MB_IREG_FRAME_COUNTER_H] = (uint16_t)((counter >> 16) & 0xFFFFu);
}

uint8_t modbus_regs_take_zero_cal_request(void)
{
    uint8_t ret = s_zero_cal_requested;
    s_zero_cal_requested = 0u;
    return ret;
}

uint8_t modbus_regs_take_save_config_request(void)
{
    uint8_t ret = s_save_config_requested;
    s_save_config_requested = 0u;
    return ret;
}

uint8_t modbus_regs_take_reboot_request(void)
{
    uint8_t ret = s_reboot_requested;
    s_reboot_requested = 0u;
    return ret;
}

uint8_t modbus_regs_take_comm_reconfig_request(void)
{
    uint8_t ret = s_comm_reconfig_requested;
    s_comm_reconfig_requested = 0u;
    return ret;
}

void modbus_regs_set_slave_id_runtime(uint8_t slave_id)
{
    if ((slave_id < 1u) || (slave_id > 247u))
    {
        return;
    }

    s_holding_regs[MB_HREG_SLAVE_ID] = slave_id;

    if (s_bound_slave != 0)
    {
        modbus_slave_set_id(s_bound_slave, slave_id);
    }
}
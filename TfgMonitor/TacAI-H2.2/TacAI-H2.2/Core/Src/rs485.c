#include "rs485.h"
#include <string.h>

static UART_HandleTypeDef *s_huart = 0;
static rs485_frame_callback_t s_frame_cb = 0;

static uint8_t s_rx_dma_buf[RS485_RX_BUFFER_SIZE];
static uint8_t s_rx_frame_buf[RS485_RX_BUFFER_SIZE];
static uint8_t s_tx_buf[RS485_TX_BUFFER_SIZE];

static volatile uint16_t s_rx_frame_len = 0;
static volatile uint8_t s_rx_pending = 0;
static volatile uint8_t s_tx_busy = 0;

static volatile uint32_t s_rx_drop_count = 0;
static volatile uint32_t s_error_count = 0;

static rs485_port_status_t rs485_start_rx_internal(void)
{
    HAL_StatusTypeDef ret;

    if (s_huart == 0)
    {
        return RS485_PORT_NOT_INIT;
    }

    ret = HAL_UARTEx_ReceiveToIdle_DMA(
        s_huart,
        s_rx_dma_buf,
        RS485_RX_BUFFER_SIZE);

    if (ret != HAL_OK)
    {
        return RS485_PORT_ERROR;
    }

    /*
     * 关闭 DMA 半传输中断。
     * 否则收到一半 buffer 时也可能进 RxEventCallback。
     */
    if (s_huart->hdmarx != 0)
    {
        __HAL_DMA_DISABLE_IT(s_huart->hdmarx, DMA_IT_HT);
    }

    return RS485_PORT_OK;
}

rs485_port_status_t rs485_port_init(
    UART_HandleTypeDef *huart,
    rs485_frame_callback_t frame_cb)
{
    if (huart == 0)
    {
        return RS485_PORT_BAD_ARGUMENT;
    }

    s_huart = huart;
    s_frame_cb = frame_cb;

    s_rx_frame_len = 0u;
    s_rx_pending = 0u;
    s_tx_busy = 0u;
    s_rx_drop_count = 0u;
    s_error_count = 0u;

    return RS485_PORT_OK;
}

rs485_port_status_t rs485_port_start_rx(void)
{
    s_rx_frame_len = 0u;
    s_rx_pending = 0u;

    return rs485_start_rx_internal();
}

void rs485_port_stop_rx(void)
{
    if (s_huart != 0)
    {
        HAL_UART_AbortReceive(s_huart);
    }
}

rs485_port_status_t rs485_port_send(const uint8_t *data, uint16_t len)
{
    HAL_StatusTypeDef ret;

    if (s_huart == 0)
    {
        return RS485_PORT_NOT_INIT;
    }

    if ((data == 0) || (len == 0u) || (len > RS485_TX_BUFFER_SIZE))
    {
        return RS485_PORT_BAD_ARGUMENT;
    }

    if (s_tx_busy)
    {
        return RS485_PORT_BUSY;
    }

    memcpy(s_tx_buf, data, len);

    s_tx_busy = 1u;

    /*
     * 使用 DMA 发送。
     * DE 由 USART 硬件自动控制，不需要软件拉 GPIO。
     */
    ret = HAL_UART_Transmit_DMA(s_huart, s_tx_buf, len);

    if (ret != HAL_OK)
    {
        s_tx_busy = 0u;
        return RS485_PORT_ERROR;
    }

    return RS485_PORT_OK;
}

void rs485_port_poll(void)
{
    uint8_t local_buf[RS485_RX_BUFFER_SIZE];
    uint16_t local_len = 0u;
    uint32_t primask;

    if (!s_rx_pending)
    {
        return;
    }

    /*
     * 从中断上下文共享区复制出一帧。
     * 裸机用关中断保护即可。
     */
    primask = __get_PRIMASK();
    __disable_irq();

    if (s_rx_pending)
    {
        local_len = s_rx_frame_len;

        if (local_len > RS485_RX_BUFFER_SIZE)
        {
            local_len = RS485_RX_BUFFER_SIZE;
        }

        memcpy(local_buf, s_rx_frame_buf, local_len);

        s_rx_frame_len = 0u;
        s_rx_pending = 0u;
    }

    if (!primask)
    {
        __enable_irq();
    }

    if ((local_len > 0u) && (s_frame_cb != 0))
    {
        s_frame_cb(local_buf, local_len);
    }
}

void rs485_port_on_rx_event(UART_HandleTypeDef *huart, uint16_t size)
{
    if ((s_huart == 0) || (huart != s_huart))
    {
        return;
    }

    if (size > RS485_RX_BUFFER_SIZE)
    {
        size = RS485_RX_BUFFER_SIZE;
    }

    /*
     * 如果 /RE 没有和 DE 联动，而是一直使能接收，
     * 发送时可能收到自己的回环数据。
     * 这里直接丢弃发送期间收到的数据。
     */
    if (s_tx_busy)
    {
        (void)rs485_start_rx_internal();
        return;
    }

    if (size > 0u)
    {
        if (!s_rx_pending)
        {
            memcpy(s_rx_frame_buf, s_rx_dma_buf, size);
            s_rx_frame_len = size;
            s_rx_pending = 1u;
        }
        else
        {
            /*
             * 上一帧还没被 main loop 处理，又来了新帧。
             */
            s_rx_drop_count++;
        }
    }

    /*
     * ReceiveToIdle_DMA 是单次接收，需要在回调里重启。
     */
    (void)rs485_start_rx_internal();
}

void rs485_port_on_tx_cplt(UART_HandleTypeDef *huart)
{
    if ((s_huart == 0) || (huart != s_huart))
    {
        return;
    }

    /*
     * DMA 发送完成后，HAL 会在合适时机进 TxCpltCallback。
     * 硬件 DE 会由 USART 自动释放。
     */
    s_tx_busy = 0u;
}

void rs485_port_on_error(UART_HandleTypeDef *huart)
{
    if ((s_huart == 0) || (huart != s_huart))
    {
        return;
    }

    s_error_count++;
    s_tx_busy = 0u;

    /*
     * 出错后重新启动接收。
     */
    (void)rs485_start_rx_internal();
}

uint8_t rs485_port_is_tx_busy(void)
{
    return s_tx_busy;
}

uint32_t rs485_port_get_rx_drop_count(void)
{
    return s_rx_drop_count;
}

uint32_t rs485_port_get_error_count(void)
{
    return s_error_count;
}
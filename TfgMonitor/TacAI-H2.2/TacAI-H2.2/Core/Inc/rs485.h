#ifndef RS485_PORT_H
#define RS485_PORT_H

#ifdef __cplusplus
extern "C"
{
#endif

#include <stdint.h>
#include "main.h"

#ifndef RS485_RX_BUFFER_SIZE
#define RS485_RX_BUFFER_SIZE 256u
#endif

#ifndef RS485_TX_BUFFER_SIZE
#define RS485_TX_BUFFER_SIZE 256u
#endif

    typedef enum
    {
        RS485_PORT_OK = 0,
        RS485_PORT_ERROR,
        RS485_PORT_BUSY,
        RS485_PORT_NOT_INIT,
        RS485_PORT_BAD_ARGUMENT,
        RS485_PORT_OVERFLOW
    } rs485_port_status_t;

    typedef void (*rs485_frame_callback_t)(
        const uint8_t *frame,
        uint16_t len);

    /*
     * 硬件 DE 版本：
     * 不再传 DE GPIO，因为 DE 已经由 HAL_RS485Ex_Init + USART_DE 管理。
     */
    rs485_port_status_t rs485_port_init(
        UART_HandleTypeDef *huart,
        rs485_frame_callback_t frame_cb);

    rs485_port_status_t rs485_port_start_rx(void);
    void rs485_port_stop_rx(void);

    /*
     * 非阻塞发送。
     * 内部复制数据，因此调用者的 tx_buf 可以是局部变量。
     */
    rs485_port_status_t rs485_port_send(const uint8_t *data, uint16_t len);

    /*
     * 主循环中调用。
     * 收到完整 RTU 帧后，会在这里触发 frame_cb。
     */
    void rs485_port_poll(void);

    /*
     * 在 HAL 回调里转发调用。
     */
    void rs485_port_on_rx_event(UART_HandleTypeDef *huart, uint16_t size);
    void rs485_port_on_tx_cplt(UART_HandleTypeDef *huart);
    void rs485_port_on_error(UART_HandleTypeDef *huart);

    uint8_t rs485_port_is_tx_busy(void);
    uint32_t rs485_port_get_rx_drop_count(void);
    uint32_t rs485_port_get_error_count(void);

#ifdef __cplusplus
}
#endif

#endif
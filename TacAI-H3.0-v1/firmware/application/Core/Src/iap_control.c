#include "iap_control.h"

#include <stdint.h>
#include "main.h"
#include "usart.h"

#define IAP_REQUEST_ADDRESS ((volatile uint32_t *)0x20001FF0u)
#define IAP_REQUEST_MAGIC   0x33415049u

static const char s_enter_command[] = "TACAI:IAP:ENTER:H3G031G8\n";
static uint8_t s_match_index;

void IAP_ControlPoll(void)
{
    uint8_t value;

    while (HAL_UART_Receive(&huart1, &value, 1u, 0u) == HAL_OK)
    {
        if (value == (uint8_t)s_enter_command[s_match_index])
        {
            ++s_match_index;
            if (s_enter_command[s_match_index] == '\0')
            {
                static const uint8_t ack[] = "IAP,ACK,H3G031G8\r\n";
                (void)HAL_UART_Transmit(&huart1, (uint8_t *)ack, sizeof(ack) - 1u, 200u);
                while (__HAL_UART_GET_FLAG(&huart1, UART_FLAG_TC) == RESET) { }
                *IAP_REQUEST_ADDRESS = IAP_REQUEST_MAGIC;
                __DSB();
                NVIC_SystemReset();
            }
        }
        else
        {
            s_match_index = (value == (uint8_t)s_enter_command[0]) ? 1u : 0u;
        }
    }
}

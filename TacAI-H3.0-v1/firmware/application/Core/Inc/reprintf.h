#ifndef __REPRINTF_H__
#define __REPRINTF_H__

#include <stdio.h>
#include <string.h>
#include "stm32g0xx_hal.h"


void UART_DMA_Init(UART_HandleTypeDef *huart);
int UART_printf(const char *format, ...);
void UART_DMA_TxCpltCallback(UART_HandleTypeDef *huart);

#endif
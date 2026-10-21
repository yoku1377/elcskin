#ifndef TEST_STM32_HAL_H
#define TEST_STM32_HAL_H
#include <stdint.h>
typedef enum { HAL_OK=0, HAL_ERROR=1, HAL_BUSY=2, HAL_TIMEOUT=3 } HAL_StatusTypeDef;
typedef struct { int unused; } I2C_HandleTypeDef;
typedef struct { int unused; } UART_HandleTypeDef;
typedef struct { int unused; } IWDG_HandleTypeDef;
typedef struct { int unused; } DMA_HandleTypeDef;
typedef struct { int unused; } GPIO_TypeDef;
typedef enum { GPIO_PIN_RESET=0, GPIO_PIN_SET=1 } GPIO_PinState;
extern GPIO_TypeDef test_gpio_a, test_gpio_b;
#define GPIOA (&test_gpio_a)
#define GPIOB (&test_gpio_b)
#define GPIO_PIN_3 (1u<<3)
#define GPIO_PIN_4 (1u<<4)
#define GPIO_PIN_5 (1u<<5)
#define GPIO_PIN_15 (1u<<15)
void HAL_GPIO_WritePin(GPIO_TypeDef *, uint16_t, GPIO_PinState);
HAL_StatusTypeDef HAL_I2C_IsDeviceReady(I2C_HandleTypeDef *, uint16_t, uint32_t, uint32_t);
void HAL_Delay(uint32_t);
uint32_t HAL_GetTick(void);
HAL_StatusTypeDef HAL_IWDG_Refresh(IWDG_HandleTypeDef *);
void NVIC_SystemReset(void);
#endif

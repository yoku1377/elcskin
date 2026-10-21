#ifndef TEST_SPI_MAIN_H
#define TEST_SPI_MAIN_H
#include <stdint.h>
typedef enum { HAL_OK = 0, HAL_ERROR = 1 } HAL_StatusTypeDef;
typedef struct { unsigned unused; } IWDG_HandleTypeDef;
uint32_t HAL_GetTick(void);
HAL_StatusTypeDef HAL_IWDG_Refresh(IWDG_HandleTypeDef *handle);
void NVIC_SystemReset(void);
void test_memory_barrier(void);
#define __DSB() test_memory_barrier()
#ifdef _MSC_VER
/* The production struct has naturally identical offsets and a checked size. */
#define __attribute__(annotation)
#endif
#endif

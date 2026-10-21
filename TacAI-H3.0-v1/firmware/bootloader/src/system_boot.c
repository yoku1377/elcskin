#include "stm32g031xx.h"

uint32_t SystemCoreClock = 16000000u;

/* This must not use the application's SystemInit: its vector offset is 0x2000.
 * Bootloader SysTick and SPI CS interrupts belong to the bootloader vectors. */
void SystemInit(void)
{
    SCB->VTOR = FLASH_BASE;
    __DSB();
    __ISB();
}

/* The CMSIS C startup calls this hook. This freestanding, C-only bootloader
 * has no constructors and does not need a hosted C runtime or syscall stubs. */
void __libc_init_array(void) { }

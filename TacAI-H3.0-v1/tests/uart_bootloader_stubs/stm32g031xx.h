#ifndef TEST_UART_BOOTLOADER_STM32G031XX_H
#define TEST_UART_BOOTLOADER_STM32G031XX_H
#include <stdint.h>
/* Only peripheral definitions are replaced; the production frame parser,
 * payload bounds and CRC implementation are compiled without a copy. */
typedef struct { volatile uint32_t IOPENR, APBENR2; } test_rcc_t;
typedef struct { volatile uint32_t MODER, AFR[2], OSPEEDR; } test_gpio_t;
typedef struct { volatile uint32_t BRR, CR1, ISR, RDR, TDR; } test_usart_t;
typedef struct { volatile uint32_t KR; } test_iwdg_t;
typedef struct { volatile uint32_t SR, CR, KEYR; } test_flash_t;
typedef struct { volatile uint32_t CTRL; } test_systick_t;
typedef struct { volatile uint32_t VTOR, ICSR; } test_scb_t;
extern test_rcc_t test_rcc;
extern test_gpio_t test_gpiob;
extern test_usart_t test_usart;
extern test_iwdg_t test_iwdg;
extern test_flash_t test_flash;
extern test_systick_t test_systick;
extern test_scb_t test_scb;
#define RCC (&test_rcc)
#define GPIOB (&test_gpiob)
#define USART1 (&test_usart)
#define IWDG (&test_iwdg)
#define FLASH (&test_flash)
#define SysTick (&test_systick)
#define SCB (&test_scb)
#define RCC_IOPENR_GPIOBEN (1u << 1)
#define RCC_APBENR2_USART1EN (1u << 14)
#define USART_CR1_TE (1u << 3)
#define USART_CR1_RE (1u << 2)
#define USART_CR1_UE (1u << 0)
#define USART_ISR_TXE_TXFNF (1u << 7)
#define USART_ISR_RXNE_RXFNE (1u << 5)
#define USART_ISR_TC (1u << 6)
#define FLASH_SR_OPERR (1u << 1)
#define FLASH_SR_PROGERR (1u << 3)
#define FLASH_SR_WRPERR (1u << 4)
#define FLASH_SR_PGAERR (1u << 5)
#define FLASH_SR_SIZERR (1u << 6)
#define FLASH_SR_PGSERR (1u << 7)
#define FLASH_SR_BSY1 (1u << 16)
#define FLASH_SR_EOP (1u << 0)
#define FLASH_CR_LOCK (1u << 31)
#define FLASH_CR_PNB (0x7Fu << 3)
#define FLASH_CR_PNB_Pos 3u
#define FLASH_CR_PER (1u << 1)
#define FLASH_CR_STRT (1u << 16)
#define FLASH_CR_PG (1u << 0)
#define SCB_ICSR_PENDSTCLR_Msk (1u << 25)
#define SCB_ICSR_PENDSVCLR_Msk (1u << 27)
#define __disable_irq() ((void)0)
#define __enable_irq() ((void)0)
#define __DSB() ((void)0)
#define __ISB() ((void)0)
#define __set_MSP(value) ((void)(value))
#define SysTick_Config(value) ((void)(value))
#ifdef _MSC_VER
#define __attribute__(value)
#endif
#endif

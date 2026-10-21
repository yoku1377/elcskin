#ifndef TEST_SPI_STM32G031XX_H
#define TEST_SPI_STM32G031XX_H
#include <stdint.h>
typedef struct { volatile uint32_t IOPENR, AHBENR, APBENR1, APBRSTR1, BDCR; } test_rcc_t;
typedef struct { volatile uint32_t MODER, OTYPER, OSPEEDR, PUPDR, AFR[2], IDR; } test_gpio_t;
typedef struct { volatile uint32_t CR1, CR2, SR, DR; } test_spi_t;
typedef struct { volatile uint32_t CR; } test_rtc_t;
typedef struct { volatile uint32_t CR1; } test_tamp_t;
typedef struct { volatile uint32_t CCR, CPAR, CMAR, CNDTR; } test_dma_channel_t;
typedef struct { volatile uint32_t ISR, IFCR; } test_dma_t;
typedef struct { volatile uint32_t CCR; } test_dmamux_t;
typedef struct { volatile uint32_t EXTICR[4], RTSR1, FTSR1, RPR1, FPR1, IMR1; } test_exti_t;
extern test_rcc_t test_rcc;
extern test_gpio_t test_gpio;
#if defined(SPI_LINK_RX_IRQ) && SPI_LINK_RX_IRQ
extern test_gpio_t test_gpio_b;
extern test_rtc_t test_rtc;
extern test_tamp_t test_tamp;
uint8_t test_link_read_rx8(void);
#define SPI_LINK_READ_DR8() test_link_read_rx8()
#define GPIOB (&test_gpio_b)
#define RTC (&test_rtc)
#define TAMP (&test_tamp)
#endif
extern test_spi_t test_spi;
extern test_dma_t test_dma;
extern test_dma_channel_t test_dma_rx, test_dma_tx;
extern test_dmamux_t test_dmamux_rx, test_dmamux_tx;
extern test_exti_t test_exti;
#define RCC (&test_rcc)
#define GPIOA (&test_gpio)
#define SPI2 (&test_spi)
#define DMA1 (&test_dma)
#define DMA1_Channel1 (&test_dma_rx)
#define DMA1_Channel2 (&test_dma_tx)
#define DMAMUX1_Channel0 (&test_dmamux_rx)
#define DMAMUX1_Channel1 (&test_dmamux_tx)
#define EXTI (&test_exti)
#define RCC_APBRSTR1_SPI2RST (1u << 14)
#define RCC_IOPENR_GPIOAEN (1u << 0)
#define RCC_AHBENR_DMA1EN (1u << 0)
#define RCC_APBENR1_SPI2EN (1u << 14)
#define RCC_APBENR1_RTCAPBEN (1u << 10)
#define DMA_IFCR_CGIF1 (1u << 0)
#define DMA_IFCR_CGIF2 (1u << 4)
#define DMA_ISR_TEIF1 (1u << 3)
#define DMA_ISR_TEIF2 (1u << 7)
#define DMA_CCR_EN (1u << 0)
#define DMA_CCR_DIR (1u << 4)
#define DMA_CCR_MINC (1u << 7)
#define DMA_CCR_PL_1 (1u << 13)
#define SPI_CR2_DS_Pos 8u
#define SPI_CR2_FRXTH (1u << 12)
#define SPI_CR2_RXDMAEN (1u << 0)
#define SPI_CR2_TXDMAEN (1u << 1)
#define SPI_CR2_RXNEIE (1u << 6)
#define SPI_CR1_SPE (1u << 6)
#define SPI_SR_OVR (1u << 6)
#define SPI_SR_RXNE (1u << 0)
#define EXTI_EXTICR3_EXTI8_Msk (0xFFu << 0)
#define EXTI4_15_IRQn 7
#define DMA1_Channel1_IRQn 9
#define DMA1_Channel2_3_IRQn 10
#define SPI2_IRQn 26
uint32_t __get_PRIMASK(void);
void __disable_irq(void);
void __set_PRIMASK(uint32_t value);
void test_link_barrier(void);
#define __DSB() test_link_barrier()
#define __DMB() test_link_barrier()
void NVIC_ClearPendingIRQ(int irq);
void NVIC_SetPriority(int irq, unsigned priority);
void NVIC_EnableIRQ(int irq);
void NVIC_DisableIRQ(int irq);
#endif

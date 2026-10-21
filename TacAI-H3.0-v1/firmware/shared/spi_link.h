#ifndef TACAI_SPI_LINK_H
#define TACAI_SPI_LINK_H

#include <stdbool.h>
#include <stdint.h>

#define SPI_LINK_FRAME_MAX 536u
#define SPI_LINK_IAP_RAM_MAGIC 0x33535049u /* "IPS3" little endian */
#ifndef SPI_LINK_RX_IRQ
#define SPI_LINK_RX_IRQ 0
#endif

typedef struct {
    uint32_t (*tick_ms)(void);
    void (*idle)(void);
    void (*watchdog)(void);
} SPI_LinkCallbacks;

/* STM32G031 SPI2: PA0=SCK/AF0, PA3=MISO/AF0, PA4=MOSI/AF1,
 * PA8=NSS/AF1. Mode 0, MSB first, 8 bits; DMA1 channels 1 and 2.
 * USART2 must be disabled before calling this function. CS must be high.
 * Initial SCK <=468750 Hz, with >=1 ms CS-high between all transactions;
 * these are bring-up settings requiring physical timing validation.
 * The caller owns EXTI4_15_IRQHandler and calls SPI_LinkCsIRQ from it.
 * DMA TC interrupts are deliberately disabled: CS defines the byte count.
 * Callbacks are copied; tick_ms is required for bounded sends. */
void SPI_LinkInit(const SPI_LinkCallbacks *callbacks);
void SPI_LinkDeinit(void);
void SPI_LinkCsIRQ(void);
#if SPI_LINK_RX_IRQ
/* Candidate backend: RXNE IRQ reads bytes; TX remains on DMA channel 2.
 * SPI2_IRQHandler is supplied by this driver, above the CS IRQ priority. */
void SPI_LinkRxIRQ(void);
#endif

#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
/* Read-only bring-up counters. The ISR records facts; UART output stays in
 * the application main loop. Not part of the TAI3 wire protocol. */
typedef struct {
    uint32_t falling, rising, transactions, requests;
    uint32_t staged, committed, acknowledged, faults, resyncs;
    uint32_t last_length, data_length, data_head, last_spi_sr, last_dma_isr;
    uint32_t gpio_idr, spi_cr1, spi_cr2, spi_sr, rx_left, tx_left, flags;
    uint32_t gpio_moder, gpio_afr0, gpio_afr1;
#if SPI_LINK_RX_IRQ
    uint32_t gpio_b_moder, gpio_b_afr0, gpio_b_afr1, rx_mode;
    uint32_t rx_dma_ccr, rx_dma_cpar, rx_dma_cmar, rx_dmamux, tx_dma_ccr;
    uint32_t last_rx_head, rx_irq_calls, rx_overflow;
    uint32_t rtc_cr, rcc_bdcr, tamp_cr1;
#endif
} SPI_LinkDiagnostics;
void SPI_LinkGetDiagnostics(SPI_LinkDiagnostics *out);
#endif

/* A completed TAI3 request is copied once, in main-loop context. Requests
 * larger than 536 bytes and incomplete transactions are not queued.
 * capacity must be at least FRAME_MAX, otherwise the request is retained. */
uint16_t SPI_LinkReceive(uint8_t *buffer, uint16_t capacity);

/* Main-loop only, with interrupts enabled. Copy one response into the inactive
 * stage without waiting for CS or invoking tick/idle/watchdog callbacks. False
 * means busy/invalid; existing staged or unread responses remain untouched. */
bool SPI_LinkQueue(const uint8_t *buffer, uint16_t length);

/* Publish one complete TAI3 response, padded to 536 with FF. Wait until a
 * separate CS transaction clocks at least length bytes. Interrupts must be
 * enabled. Short reads retain the response for another poll. On timeout the
 * response remains available; the host must poll rather than repeat a write.
 * Preparation is staged: the first response poll can be all FF. A completed
 * frame remains readable for CRC retry until the next complete TAI3 request.
 * No Flash operation or wire-frame parsing is performed in the CS ISR. */
bool SPI_LinkSend(const uint8_t *buffer, uint16_t length, uint32_t timeout_ms);
bool SPI_LinkResponsePending(void);

#endif

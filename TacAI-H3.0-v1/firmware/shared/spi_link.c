#include "spi_link.h"
#include "stm32g031xx.h"
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
#include <string.h>
static volatile SPI_LinkDiagnostics s_diagnostics;
#define DIAG_INC(field) (++s_diagnostics.field)
#else
#define DIAG_INC(field) ((void)0)
#endif

#define CS_PIN (1u << 8)
#define RX_SIZE (SPI_LINK_FRAME_MAX + 1u)
#define SPI2_DMA_RX_REQUEST 0x12u
#define SPI2_DMA_TX_REQUEST 0x13u
#define DMA_STOP_POLL_LIMIT 64u
#define RX_FIFO_DRAIN_LIMIT 4u /* SPI2 has a four-byte RX FIFO. */

static uint8_t s_rx[RX_SIZE];
static uint8_t s_tx[2][SPI_LINK_FRAME_MAX];
static const uint8_t s_idle_ff = 0xFFu;
static uint8_t s_pending[SPI_LINK_FRAME_MAX];
static volatile uint16_t s_pending_length;
static volatile uint16_t s_response_length;
static volatile uint8_t s_response_ready;
static volatile uint8_t s_response_available;
static volatile uint8_t s_active_tx;
static volatile uint8_t s_stage_tx;
static volatile uint16_t s_stage_length;
static volatile uint8_t s_stage_ready;
static volatile uint8_t s_staging;
static volatile uint8_t s_cs_active;
static volatile uint8_t s_cs_response;
static volatile uint8_t s_resync;
static volatile uint8_t s_initialized;
static SPI_LinkCallbacks s_callbacks;
#if SPI_LINK_RX_IRQ
static volatile uint16_t s_irq_rx_length;
static volatile uint8_t s_irq_rx_fault;
/* The test register stub models the FIFO's read side effects. Production
 * always performs the same byte-wide volatile peripheral access as HAL. */
#ifndef SPI_LINK_READ_DR8
#define SPI_LINK_READ_DR8() (*(volatile uint8_t *)&SPI2->DR)
#endif
#endif

static uint32_t lock_irq(void)
{
    uint32_t previous = __get_PRIMASK();
    __disable_irq();
    return previous;
}

static void unlock_irq(uint32_t previous)
{
    __set_PRIMASK(previous);
}

static void idle(void)
{
    if (s_callbacks.watchdog != 0) s_callbacks.watchdog();
    if (s_callbacks.idle != 0) s_callbacks.idle();
}

/* IRQ paths cannot depend on SysTick or wait indefinitely for a faulty DMA. */
static bool stop_dma_channels(void)
{
    uint32_t attempt;
    DMA1_Channel1->CCR &= ~DMA_CCR_EN;
    DMA1_Channel2->CCR &= ~DMA_CCR_EN;
    for (attempt = 0u; attempt < DMA_STOP_POLL_LIMIT; ++attempt) {
        __DSB();
        if (((DMA1_Channel1->CCR | DMA1_Channel2->CCR) & DMA_CCR_EN) == 0u)
            return true;
    }
    return false;
}

static void abort_transaction(void)
{
#if SPI_LINK_RX_IRQ
    SPI2->CR2 &= ~SPI_CR2_RXNEIE;
    NVIC_ClearPendingIRQ(SPI2_IRQn);
#endif
    SPI2->CR2 &= ~(SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN);
    SPI2->CR1 = 0u;
    DMA1_Channel1->CCR &= ~DMA_CCR_EN;
    DMA1_Channel2->CCR &= ~DMA_CCR_EN;
    s_cs_active = 0u;
    s_cs_response = 0u;
    s_resync = 1u;
    DIAG_INC(faults);
    DIAG_INC(resyncs);
    __DSB();
}

#if SPI_LINK_RX_IRQ
static void mark_rx_overflow(void)
{
    if ((s_irq_rx_fault & 1u) == 0u) DIAG_INC(rx_overflow);
    s_irq_rx_fault |= 1u;
}

/* Four bytes is the hardware FIFO depth. Never spin indefinitely in an IRQ.
 * RXNE runs at higher priority than CS, so the CS handler masks RXNEIE before
 * draining a final FIFO byte and publishing the captured buffer. */
static bool drain_irq_rx(void)
{
    uint16_t reads;
    if ((SPI2->SR & SPI_SR_OVR) != 0u) s_irq_rx_fault |= 2u;
    for (reads = 0u; reads < RX_FIFO_DRAIN_LIMIT &&
         (SPI2->SR & SPI_SR_RXNE) != 0u; ++reads) {
        uint8_t value = SPI_LINK_READ_DR8();
        uint16_t length = s_irq_rx_length;
        if (length < RX_SIZE) {
            s_rx[length++] = value;
            s_irq_rx_length = length;
            if (length > SPI_LINK_FRAME_MAX) mark_rx_overflow();
        } else {
            mark_rx_overflow();
        }
        __DSB();
    }
    if ((SPI2->SR & SPI_SR_OVR) != 0u) s_irq_rx_fault |= 2u;
    return (SPI2->SR & SPI_SR_RXNE) == 0u;
}

void SPI_LinkRxIRQ(void)
{
    if (!s_initialized || (SPI2->CR1 & SPI_CR1_SPE) == 0u ||
        (SPI2->CR2 & SPI_CR2_RXNEIE) == 0u) return;
    DIAG_INC(rx_irq_calls);
    /* Do not require s_cs_active: a byte IRQ can preempt the pending falling
     * CS interrupt. The buffer counter was reset while CS was high. */
    if (!drain_irq_rx()) abort_transaction();
}

void SPI2_IRQHandler(void)
{
    SPI_LinkRxIRQ();
}
#endif

/* Always reset the SPI FIFOs at CS high. This avoids leftover prefetched TX
 * bytes and does not depend on the slave BSY flag (which is not a CS marker).
 * Do not rewrite channel addresses/counts until both DMA channels are stopped. */
static bool arm_transaction(void)
{
    if (!stop_dma_channels()) {
        abort_transaction();
        return false;
    }
    DMA1_Channel1->CCR = 0u;
    DMA1_Channel2->CCR = 0u;
    SPI2->CR1 = 0u;
    RCC->APBRSTR1 |= RCC_APBRSTR1_SPI2RST;
    RCC->APBRSTR1 &= ~RCC_APBRSTR1_SPI2RST;
    DMA1->IFCR = DMA_IFCR_CGIF1 | DMA_IFCR_CGIF2;
#if SPI_LINK_RX_IRQ
    NVIC_ClearPendingIRQ(SPI2_IRQn);
    s_irq_rx_length = 0u;
    s_irq_rx_fault = 0u;
    DMAMUX1_Channel0->CCR = 0u;
    DMA1_Channel1->CPAR = 0u;
    DMA1_Channel1->CMAR = 0u;
    DMA1_Channel1->CNDTR = 0u;
#else
    DMAMUX1_Channel0->CCR = SPI2_DMA_RX_REQUEST;
#endif
    DMAMUX1_Channel1->CCR = SPI2_DMA_TX_REQUEST;
#if !SPI_LINK_RX_IRQ
    DMA1_Channel1->CPAR = (uint32_t)&SPI2->DR;
    DMA1_Channel1->CMAR = (uint32_t)s_rx;
    DMA1_Channel1->CNDTR = RX_SIZE;
#endif
    DMA1_Channel2->CPAR = (uint32_t)&SPI2->DR;
    DMA1_Channel2->CMAR = s_response_available
        ? (uint32_t)s_tx[s_active_tx] : (uint32_t)&s_idle_ff;
    DMA1_Channel2->CNDTR = SPI_LINK_FRAME_MAX;
#if !SPI_LINK_RX_IRQ
    DMA1_Channel1->CCR = DMA_CCR_MINC | DMA_CCR_PL_1 | DMA_CCR_EN;
#endif
    DMA1_Channel2->CCR = (s_response_available ? DMA_CCR_MINC : 0u) |
                         DMA_CCR_PL_1 | DMA_CCR_DIR | DMA_CCR_EN;
    SPI2->CR2 = (7u << SPI_CR2_DS_Pos) | SPI_CR2_FRXTH |
#if SPI_LINK_RX_IRQ
                SPI_CR2_RXNEIE | SPI_CR2_TXDMAEN;
#else
                SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN;
#endif
    SPI2->CR1 = SPI_CR1_SPE;
    __DSB();
    return true;
}

/* Swap a fully prepared buffer only at the end of a CS transaction. There
 * is never a main-loop write to the buffer currently addressed by TX DMA. */
static void commit_stage(void)
{
    if (s_stage_ready) {
        s_active_tx = s_stage_tx;
        s_response_length = s_stage_length;
        s_response_available = 1u;
        s_response_ready = 1u;
        s_stage_ready = 0u;
        DIAG_INC(committed);
    }
}

void SPI_LinkInit(const SPI_LinkCallbacks *callbacks)
{
    const uint32_t pins = (3u << 0) | (3u << 6) | (3u << 8) | (3u << 16);
    uint32_t previous = lock_irq();
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
    memset((void *)&s_diagnostics, 0, sizeof(s_diagnostics));
#endif
    s_callbacks.tick_ms = callbacks != 0 ? callbacks->tick_ms : 0;
    s_callbacks.idle = callbacks != 0 ? callbacks->idle : 0;
    s_callbacks.watchdog = callbacks != 0 ? callbacks->watchdog : 0;
    s_pending_length = 0u;
    s_response_length = 0u;
    s_response_ready = 0u;
    s_response_available = 0u;
    s_active_tx = 0u;
    s_stage_ready = 0u;
    s_staging = 0u;
    s_cs_active = 0u;
    s_cs_response = 0u;
    s_resync = 0u;
    RCC->IOPENR |= RCC_IOPENR_GPIOAEN;
    RCC->AHBENR |= RCC_AHBENR_DMA1EN;
    RCC->APBENR1 |= RCC_APBENR1_SPI2EN;
    (void)RCC->IOPENR;
    GPIOA->MODER = (GPIOA->MODER & ~pins) |
                   (2u << 0) | (2u << 6) | (2u << 8) | (2u << 16);
    GPIOA->OTYPER &= ~((1u << 0) | (1u << 3) | (1u << 4) | CS_PIN);
    GPIOA->OSPEEDR = (GPIOA->OSPEEDR & ~pins) | pins;
    GPIOA->PUPDR = (GPIOA->PUPDR & ~pins) | (1u << 16); /* NSS pull-up */
    GPIOA->AFR[0] = (GPIOA->AFR[0] & ~((15u << 0) | (15u << 12) | (15u << 16))) |
                    (1u << 16);
    GPIOA->AFR[1] = (GPIOA->AFR[1] & ~15u) | 1u;
    /* Keep GPIO alternate-function mode while independently enabling EXTI. */
    EXTI->EXTICR[2] &= ~EXTI_EXTICR3_EXTI8_Msk;
    EXTI->RTSR1 |= CS_PIN;
    EXTI->FTSR1 |= CS_PIN;
    EXTI->RPR1 = CS_PIN;
    EXTI->FPR1 = CS_PIN;
    (void)arm_transaction();
    s_initialized = 1u;
    EXTI->IMR1 |= CS_PIN;
    NVIC_ClearPendingIRQ(EXTI4_15_IRQn);
    NVIC_SetPriority(EXTI4_15_IRQn, 1u);
    NVIC_EnableIRQ(EXTI4_15_IRQn);
#if SPI_LINK_RX_IRQ
    NVIC_ClearPendingIRQ(SPI2_IRQn);
    NVIC_SetPriority(SPI2_IRQn, 0u);
    NVIC_EnableIRQ(SPI2_IRQn);
#endif
    unlock_irq(previous);
}

void SPI_LinkCsIRQ(void)
{
    uint32_t falling = EXTI->FPR1 & CS_PIN;
    uint32_t rising = EXTI->RPR1 & CS_PIN;
    if (!s_initialized) return;
    if (falling != 0u) {
        DIAG_INC(falling);
        EXTI->FPR1 = CS_PIN;
        s_cs_active = 1u;
        s_cs_response = s_response_available;
    }
    if (rising != 0u) {
        DIAG_INC(rising);
        uint16_t length;
        uint16_t i;
#if !SPI_LINK_RX_IRQ
        uint16_t fifo_reads;
#endif
        uint32_t faults;
        EXTI->RPR1 = CS_PIN;
        /* A failed stop/drain left SPI disabled. Nothing clocked while disabled
         * can publish a request or consume a response. Re-arm at this CS high
         * boundary and retain the response for the next actual transaction. */
        if (s_resync && (SPI2->CR1 & SPI_CR1_SPE) == 0u) {
            s_cs_active = 0u;
            s_cs_response = 0u;
            if ((GPIOA->IDR & CS_PIN) != 0u) {
                commit_stage();
                if (arm_transaction() && (GPIOA->IDR & CS_PIN) != 0u &&
                    (EXTI->FPR1 & CS_PIN) == 0u) s_resync = 0u;
            }
            return;
        }
#if SPI_LINK_RX_IRQ
        SPI2->CR2 &= ~SPI_CR2_RXNEIE;
        NVIC_ClearPendingIRQ(SPI2_IRQn);
#endif
        SPI2->CR2 &= ~(SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN);
        if (!stop_dma_channels()) {
            abort_transaction();
            return;
        }
#if SPI_LINK_RX_IRQ
        if (!drain_irq_rx()) {
            abort_transaction();
            return;
        }
        length = s_irq_rx_length;
        faults = s_irq_rx_fault | (DMA1->ISR & DMA_ISR_TEIF2);
#else
        length = (uint16_t)(RX_SIZE - DMA1_Channel1->CNDTR);
        faults = (DMA1->ISR & (DMA_ISR_TEIF1 | DMA_ISR_TEIF2)) | (SPI2->SR & SPI_SR_OVR);
        /* A final byte can still be in the RX FIFO when CS rises. */
        for (fifo_reads = 0u; fifo_reads < RX_FIFO_DRAIN_LIMIT &&
             (SPI2->SR & SPI_SR_RXNE) != 0u; ++fifo_reads) {
            uint8_t value = *(volatile uint8_t *)&SPI2->DR;
            if (length < RX_SIZE) s_rx[length++] = value;
            else faults = 1u;
            __DSB();
        }
        if ((SPI2->SR & SPI_SR_RXNE) != 0u) {
            abort_transaction();
            return;
        }
#endif
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
        s_diagnostics.transactions++;
        s_diagnostics.last_length = length;
        s_diagnostics.last_spi_sr = SPI2->SR;
        s_diagnostics.last_dma_isr = DMA1->ISR;
        if (faults != 0u) s_diagnostics.faults++;
#if SPI_LINK_RX_IRQ
        s_diagnostics.last_rx_head = 0u;
        for (i = 0u; i < 4u && i < length; ++i)
            s_diagnostics.last_rx_head |= (uint32_t)s_rx[i] << (24u - 8u * i);
#endif
        /* Retain the last non-idle MOSI prefix; FF response polls must not
         * erase evidence of the original request or a malformed request. */
        for (i = 0u; i < length && i < RX_SIZE; ++i) {
            if (s_rx[i] != 0xFFu) {
                uint16_t j;
                uint32_t head = 0u;
                for (j = 0u; j < 4u && j < length; ++j)
                    head |= (uint32_t)s_rx[j] << (24u - 8u * j);
                s_diagnostics.data_length = length;
                s_diagnostics.data_head = head;
                break;
            }
        }
#endif
        /* A CS high gap shorter than the ISR service time can coalesce edges.
         * Fail closed: no request publication or ACK consumption while low. */
        if ((GPIOA->IDR & CS_PIN) == 0u) {
            DIAG_INC(resyncs);
            SPI2->CR1 = 0u;
            s_resync = 1u;
            s_cs_active = 0u;
            s_cs_response = 0u;
            return;
        }
        if (s_resync) {
            s_resync = 0u;
        } else if (faults == 0u && !s_response_ready && !s_stage_ready && !s_staging &&
                   s_pending_length == 0u && length >= 16u && length <= SPI_LINK_FRAME_MAX &&
                   s_rx[0] == 'T' && s_rx[1] == 'A' && s_rx[2] == 'I' && s_rx[3] == '3') {
            for (i = 0u; i < length; ++i) s_pending[i] = s_rx[i];
            s_response_available = 0u; /* Previous response is now obsolete. */
            __DMB();
            s_pending_length = length;
            DIAG_INC(requests);
        } else if (s_cs_response) {
            uint8_t dummy_only = 1u;
            for (i = 0u; i < length && i < RX_SIZE; ++i) {
                if (s_rx[i] != 0xFFu) dummy_only = 0u;
            }
            if (faults == 0u && dummy_only && !s_stage_ready && !s_staging &&
                length >= s_response_length && length <= SPI_LINK_FRAME_MAX) {
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
                if (s_response_ready) s_diagnostics.acknowledged++;
#endif
                s_response_ready = 0u;
                /* Keep the last complete frame readable until a new request.
                 * A host can re-read a corrupted ACK without repeating writes. */
            }
        }
        s_cs_active = 0u;
        s_cs_response = 0u;
        if ((GPIOA->IDR & CS_PIN) != 0u) {
            commit_stage();
            if (!arm_transaction()) return;
            if ((GPIOA->IDR & CS_PIN) == 0u || (EXTI->FPR1 & CS_PIN) != 0u) {
                /* A master violating the CS-high interval may start during
                 * register reload. Never consume that partial transaction. */
                s_resync = 1u;
                DIAG_INC(resyncs);
            }
        } else {
            SPI2->CR1 = 0u;
            s_resync = 1u;
            DIAG_INC(resyncs);
        }
    }
}

bool SPI_LinkResponsePending(void)
{
    return s_response_ready != 0u || s_stage_ready != 0u || s_staging != 0u;
}

uint16_t SPI_LinkReceive(uint8_t *buffer, uint16_t capacity)
{
    uint16_t length;
    uint16_t i;
    uint32_t previous;
    if (buffer == 0 || capacity < SPI_LINK_FRAME_MAX || !s_initialized) return 0u;
    previous = lock_irq();
    length = s_pending_length;
    for (i = 0u; i < length; ++i) buffer[i] = s_pending[i];
    s_pending_length = 0u;
    unlock_irq(previous);
    return length;
}

bool SPI_LinkQueue(const uint8_t *buffer, uint16_t length)
{
    uint16_t i;
    uint8_t stage;
    uint32_t previous;
    if (!s_initialized || buffer == 0 || length < 16u || length > SPI_LINK_FRAME_MAX ||
        __get_PRIMASK() != 0u) return false;
    previous = lock_irq();
    if (s_response_ready || s_stage_ready || s_staging) {
        unlock_irq(previous);
        return false;
    }
    s_staging = 1u;
    stage = (uint8_t)(s_active_tx ^ 1u);
    unlock_irq(previous);
    for (i = 0u; i < length; ++i) s_tx[stage][i] = buffer[i];
    for (; i < SPI_LINK_FRAME_MAX; ++i) s_tx[stage][i] = 0xFFu;
    {
        uint32_t previous = lock_irq();
        s_stage_tx = stage;
        s_stage_length = length;
        s_response_ready = 1u;
        __DMB();
        s_stage_ready = 1u;
        s_staging = 0u;
        DIAG_INC(staged);
        unlock_irq(previous);
    }
    return true;
}

bool SPI_LinkSend(const uint8_t *buffer, uint16_t length, uint32_t timeout_ms)
{
    uint32_t start;
    if (!s_initialized || buffer == 0 || length < 16u || length > SPI_LINK_FRAME_MAX ||
        s_callbacks.tick_ms == 0 || __get_PRIMASK() != 0u) return false;
    start = s_callbacks.tick_ms();
    /* Bootloader callers retain the synchronous wait/ACK/timeout contract. */
    while (!SPI_LinkQueue(buffer, length)) {
        if (timeout_ms != 0xFFFFFFFFu &&
            (uint32_t)(s_callbacks.tick_ms() - start) >= timeout_ms) return false;
        idle();
    }
    for (;;) {
        if (!s_response_ready && !s_stage_ready) return true;
        if (timeout_ms != 0xFFFFFFFFu &&
            (uint32_t)(s_callbacks.tick_ms() - start) >= timeout_ms) return false;
        idle();
    }
}

#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
void SPI_LinkGetDiagnostics(SPI_LinkDiagnostics *out)
{
    uint32_t previous;
    if (out == 0) return;
    previous = lock_irq();
    *out = s_diagnostics;
    out->flags = (uint32_t)s_initialized | ((uint32_t)s_cs_active << 1) |
        ((uint32_t)s_response_available << 2) | ((uint32_t)s_response_ready << 3) |
        ((uint32_t)s_stage_ready << 4) | ((uint32_t)s_resync << 5);
    if (s_initialized) {
        out->gpio_idr = GPIOA->IDR;
        out->gpio_moder = GPIOA->MODER;
        out->gpio_afr0 = GPIOA->AFR[0];
        out->gpio_afr1 = GPIOA->AFR[1];
        out->spi_cr1 = SPI2->CR1;
        out->spi_cr2 = SPI2->CR2;
        out->spi_sr = SPI2->SR;
#if SPI_LINK_RX_IRQ
        out->rx_left = RX_SIZE - s_irq_rx_length;
        out->gpio_b_moder = GPIOB->MODER;
        out->gpio_b_afr0 = GPIOB->AFR[0];
        out->gpio_b_afr1 = GPIOB->AFR[1];
        out->rx_mode = 1u;
        out->rx_dma_ccr = DMA1_Channel1->CCR;
        out->rx_dma_cpar = DMA1_Channel1->CPAR;
        out->rx_dma_cmar = DMA1_Channel1->CMAR;
        out->rx_dmamux = DMAMUX1_Channel0->CCR;
        out->tx_dma_ccr = DMA1_Channel2->CCR;
        out->rcc_bdcr = RCC->BDCR;
        {
            uint32_t apb = RCC->APBENR1;
            RCC->APBENR1 = apb | RCC_APBENR1_RTCAPBEN;
            (void)RCC->APBENR1;
            __DSB();
            out->rtc_cr = RTC->CR;
            out->tamp_cr1 = TAMP->CR1;
            RCC->APBENR1 = apb;
            __DSB();
        }
#else
        out->rx_left = DMA1_Channel1->CNDTR;
#endif
        out->tx_left = DMA1_Channel2->CNDTR;
    }
    unlock_irq(previous);
}
#endif

void SPI_LinkDeinit(void)
{
    uint32_t previous = lock_irq();
    if (s_initialized) {
        EXTI->IMR1 &= ~CS_PIN;
        EXTI->RTSR1 &= ~CS_PIN;
        EXTI->FTSR1 &= ~CS_PIN;
        EXTI->RPR1 = CS_PIN;
        EXTI->FPR1 = CS_PIN;
        NVIC_DisableIRQ(EXTI4_15_IRQn);
        NVIC_ClearPendingIRQ(EXTI4_15_IRQn);
#if SPI_LINK_RX_IRQ
        NVIC_DisableIRQ(SPI2_IRQn);
        NVIC_ClearPendingIRQ(SPI2_IRQn);
#endif
        SPI2->CR2 = 0u;
        SPI2->CR1 = 0u;
        if (stop_dma_channels()) {
            DMA1_Channel1->CCR = 0u;
            DMA1_Channel2->CCR = 0u;
            DMA1->IFCR = DMA_IFCR_CGIF1 | DMA_IFCR_CGIF2;
            DMAMUX1_Channel0->CCR = 0u;
            DMAMUX1_Channel1->CCR = 0u;
        } else {
            DIAG_INC(faults);
        }
        NVIC_ClearPendingIRQ(DMA1_Channel1_IRQn);
        NVIC_ClearPendingIRQ(DMA1_Channel2_3_IRQn);
        RCC->APBRSTR1 |= RCC_APBRSTR1_SPI2RST;
        RCC->APBRSTR1 &= ~RCC_APBRSTR1_SPI2RST;
        RCC->APBENR1 &= ~RCC_APBENR1_SPI2EN;
        GPIOA->MODER |= (3u << 0) | (3u << 6) | (3u << 8) | (3u << 16);
        s_initialized = 0u;
        s_pending_length = 0u;
        s_response_ready = 0u;
        s_response_available = 0u;
        s_stage_ready = 0u;
        s_staging = 0u;
    }
    unlock_irq(previous);
}

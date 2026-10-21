/* Exercise the production optional RXNE backend with FIFO read side effects. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#define SPI_LINK_RX_IRQ 1
#define SPI_LINK_DIAGNOSTICS 1
#include "../../firmware/shared/spi_link.c"

test_rcc_t test_rcc;
test_gpio_t test_gpio, test_gpio_b;
test_spi_t test_spi;
test_rtc_t test_rtc;
test_tamp_t test_tamp;
test_dma_t test_dma;
test_dma_channel_t test_dma_rx, test_dma_tx;
test_dmamux_t test_dmamux_rx, test_dmamux_tx;
test_exti_t test_exti;
static uint32_t primask, enabled_irqs, tick;
static unsigned barriers, reads, idle_calls, watchdog_calls, rtc_clock_seen;
static uint8_t fifo[4];
static unsigned fifo_count, fifo_index;
static bool stuck_rxne, stuck_tx, inject_irq_during_close;

uint32_t __get_PRIMASK(void) { return primask; }
void __disable_irq(void) { primask = 1u; }
void __set_PRIMASK(uint32_t value) { primask = value; }
void NVIC_ClearPendingIRQ(int irq)
{ assert(irq == SPI2_IRQn || irq == EXTI4_15_IRQn || irq == DMA1_Channel1_IRQn || irq == DMA1_Channel2_3_IRQn); }
void NVIC_SetPriority(int irq, unsigned priority)
{ assert((irq == SPI2_IRQn && priority == 0u) || (irq == EXTI4_15_IRQn && priority == 1u)); }
void NVIC_EnableIRQ(int irq)
{ assert(irq == SPI2_IRQn || irq == EXTI4_15_IRQn); enabled_irqs |= 1u << irq; }
void NVIC_DisableIRQ(int irq)
{ assert(irq == SPI2_IRQn || irq == EXTI4_15_IRQn); enabled_irqs &= ~(1u << irq); }
void test_link_barrier(void)
{
    barriers++;
    EXTI->RPR1 = EXTI->FPR1 = 0u; /* EXTI pending registers are W1C. */
    if (stuck_tx) DMA1_Channel2->CCR |= DMA_CCR_EN;
    if (stuck_rxne) SPI2->SR |= SPI_SR_RXNE;
    if ((RCC->APBENR1 & RCC_APBENR1_RTCAPBEN) != 0u) rtc_clock_seen++;
    if (inject_irq_during_close && (SPI2->CR2 & SPI_CR2_RXNEIE) == 0u) {
        inject_irq_during_close = false;
        SPI_LinkRxIRQ(); /* A pending high-priority interrupt sees the mask. */
    }
    /* Model the peripheral reset after the new transaction is fully armed. */
    if (!stuck_rxne && fifo_count == 0u && s_irq_rx_length == 0u &&
        (SPI2->CR2 & SPI_CR2_RXNEIE) != 0u && SPI2->CR1 == SPI_CR1_SPE)
        SPI2->SR = 0u;
}
uint8_t test_link_read_rx8(void)
{
    uint8_t value;
    reads++;
    assert((SPI2->SR & SPI_SR_RXNE) != 0u);
    if (stuck_rxne) return 0xFFu;
    assert(fifo_count != 0u);
    value = fifo[fifo_index++];
    if (--fifo_count == 0u) SPI2->SR &= ~SPI_SR_RXNE;
    return value;
}
static uint32_t tick_ms(void) { return tick++; }
static void watchdog(void) { watchdog_calls++; }
static void start_cs(void)
{
    GPIOA->IDR &= ~CS_PIN;
    EXTI->FPR1 = CS_PIN; EXTI->RPR1 = 0u;
    SPI_LinkCsIRQ(); EXTI->FPR1 = 0u;
}
static void feed_fifo(const uint8_t *data, unsigned count, bool deliver_irq)
{
    unsigned i;
    assert(count >= 1u && count <= 4u && fifo_count == 0u);
    for (i = 0u; i < count; ++i) fifo[i] = data != NULL ? data[i] : 0xFFu;
    fifo_index = 0u; fifo_count = count; SPI2->SR |= SPI_SR_RXNE;
    if (deliver_irq) SPI2_IRQHandler();
}
static void feed_bytes(const uint8_t *data, unsigned count)
{
    unsigned offset = 0u;
    while (offset < count) {
        unsigned chunk = count - offset > 4u ? 4u : count - offset;
        feed_fifo(data != NULL ? data + offset : NULL, chunk, true);
        offset += chunk;
    }
}
static void end_cs(void)
{
    GPIOA->IDR |= CS_PIN;
    EXTI->FPR1 = 0u; EXTI->RPR1 = CS_PIN;
    SPI_LinkCsIRQ(); EXTI->RPR1 = EXTI->FPR1 = 0u;
}
static void transaction(const uint8_t *data, unsigned count)
{ start_cs(); feed_bytes(data, count); end_cs(); }
static void idle_callback(void)
{ idle_calls++; transaction(NULL, 536u); }
static void reset_fixture(void)
{
    SPI_LinkCallbacks callbacks = {tick_ms, idle_callback, watchdog};
    memset(&test_rcc, 0, sizeof(test_rcc)); memset(&test_gpio, 0, sizeof(test_gpio));
    memset(&test_gpio_b, 0, sizeof(test_gpio_b)); memset(&test_spi, 0, sizeof(test_spi));
    memset(&test_rtc, 0, sizeof(test_rtc)); memset(&test_tamp, 0, sizeof(test_tamp));
    memset(&test_dma, 0, sizeof(test_dma)); memset(&test_dma_rx, 0, sizeof(test_dma_rx));
    memset(&test_dma_tx, 0, sizeof(test_dma_tx)); memset(&test_exti, 0, sizeof(test_exti));
    memset(&test_dmamux_rx, 0, sizeof(test_dmamux_rx)); memset(&test_dmamux_tx, 0, sizeof(test_dmamux_tx));
    primask = enabled_irqs = tick = 0u;
    barriers = reads = idle_calls = watchdog_calls = rtc_clock_seen = 0u;
    fifo_count = fifo_index = 0u;
    stuck_rxne = stuck_tx = inject_irq_during_close = false;
    GPIOA->IDR = CS_PIN;
    SPI_LinkInit(&callbacks);
}
static void make_frame(uint8_t *data, unsigned count, uint8_t tag)
{ memset(data, tag, count); memcpy(data, "TAI3", 4u); }

static void rxne_configuration_and_16_byte_request(void)
{
    uint8_t request[16], received[536];
    SPI_LinkDiagnostics d;
    reset_fixture(); make_frame(request, sizeof(request), 0x20u);
    assert(enabled_irqs == ((1u << SPI2_IRQn) | (1u << EXTI4_15_IRQn)));
    assert(DMA1_Channel1->CCR == 0u && DMAMUX1_Channel0->CCR == 0u);
    assert((SPI2->CR2 & SPI_CR2_RXDMAEN) == 0u && (SPI2->CR2 & SPI_CR2_RXNEIE) != 0u);
    assert((DMA1_Channel2->CCR & (DMA_CCR_EN | DMA_CCR_DIR)) == (DMA_CCR_EN | DMA_CCR_DIR));
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
    assert(memcmp(request, received, sizeof(request)) == 0);
    SPI_LinkGetDiagnostics(&d);
    assert(d.last_length == 16u && d.last_rx_head == 0x54414933u && d.data_head == 0x54414933u);
    assert(d.requests == 1u && d.faults == 0u && d.rx_irq_calls == 4u && d.rx_mode == 1u);
    assert(d.rx_dma_ccr == 0u && d.rx_dma_cpar == 0u && d.rx_dmamux == 0u);
    transaction(NULL, 536u); SPI_LinkGetDiagnostics(&d);
    assert(d.last_length == 536u && d.last_rx_head == 0xFFFFFFFFu);
    assert(d.data_head == 0x54414933u && d.data_length == 16u);
}
static void rx_irq_can_precede_cs_falling_and_tail_can_follow_irq(void)
{
    uint8_t request[16], received[536];
    unsigned tail;
    reset_fixture(); make_frame(request, sizeof(request), 0x31u);
    GPIOA->IDR &= ~CS_PIN;
    EXTI->FPR1 = CS_PIN; EXTI->RPR1 = 0u;
    feed_fifo(request, 4u, true); /* RXNE preempts the falling-CS ISR. */
    assert(s_irq_rx_length == 4u);
    EXTI->FPR1 = CS_PIN; SPI_LinkCsIRQ(); EXTI->FPR1 = 0u;
    feed_bytes(request + 4u, 11u);
    feed_fifo(request + 15u, 1u, false); /* End marker wins over pending RXNE. */
    inject_irq_during_close = true;
    end_cs();
    assert(!inject_irq_during_close && fifo_count == 0u && reads == 16u);
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
    assert(memcmp(received, request, sizeof(request)) == 0 && s_diagnostics.faults == 0u);
    for (tail = 1u; tail <= 4u; ++tail) {
        reset_fixture(); make_frame(request, sizeof(request), (uint8_t)(0x32u + tail));
        start_cs(); feed_bytes(request, sizeof(request) - tail);
        feed_fifo(request + sizeof(request) - tail, tail, false);
        end_cs();
        assert(reads == 16u && fifo_count == 0u);
        assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
        assert(memcmp(received, request, sizeof(request)) == 0);
    }
    reset_fixture(); make_frame(request, sizeof(request), 0x32u);
    start_cs(); feed_bytes(request, sizeof(request));
    GPIOA->IDR |= CS_PIN; SPI_LinkRxIRQ(); /* RXNE ISR wins before rising CS. */
    end_cs();
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
    assert(memcmp(received, request, sizeof(request)) == 0);
}
static void full_frame_and_overflow_are_bounded(void)
{
    uint8_t request[536], received[536];
    const unsigned overlong[] = {537u, 538u, 601u};
    unsigned i;
    reset_fixture(); make_frame(request, sizeof(request), 0x42u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
    assert(memcmp(request, received, sizeof(request)) == 0 && s_diagnostics.rx_overflow == 0u);
    for (i = 0u; i < sizeof(overlong) / sizeof(overlong[0]); ++i) {
        reset_fixture();
        memset(s_pending, 0xA5, sizeof(s_pending));
        memset(s_tx, 0x5A, sizeof(s_tx));
        transaction(NULL, overlong[i]);
        assert(s_diagnostics.last_length == 537u && s_diagnostics.rx_overflow == 1u);
        assert(s_diagnostics.faults == 1u && s_diagnostics.requests == 0u);
        assert(SPI_LinkReceive(received, sizeof(received)) == 0u);
        assert(s_pending[0] == 0xA5u && s_pending[535] == 0xA5u);
        assert(s_tx[0][0] == 0x5Au && s_tx[1][535] == 0x5Au);
        transaction(request, sizeof(request));
        assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
    }
}
static void response_staging_ack_timeout_and_reread_are_preserved(void)
{
    uint8_t response[24], next[24], saved[536];
    reset_fixture(); make_frame(response, sizeof(response), 0x51u);
    assert(SPI_LinkSend(response, sizeof(response), 20u));
    assert(idle_calls == 2u && watchdog_calls == 2u && s_diagnostics.acknowledged == 1u);
    assert(s_response_available && !SPI_LinkResponsePending());
    transaction(NULL, 536u); assert(s_diagnostics.acknowledged == 1u);
    memcpy(saved, s_tx[s_active_tx], sizeof(saved));
    start_cs(); make_frame(next, sizeof(next), 0x52u);
    assert(SPI_LinkQueue(next, sizeof(next)));
    feed_bytes(NULL, 536u); end_cs(); /* Old in-flight response cannot ACK the new stage. */
    assert(s_response_ready && s_diagnostics.acknowledged == 1u);
    assert(memcmp(s_tx[s_active_tx], next, sizeof(next)) == 0);
    assert(memcmp(saved, next, sizeof(next)) != 0);
    transaction(NULL, 23u); assert(s_response_ready);
    transaction(NULL, 537u); assert(s_response_ready);
    transaction(NULL, 24u); assert(!s_response_ready && s_response_available);
    assert(s_diagnostics.acknowledged == 2u);
    SPI_LinkInit(NULL); assert(SPI_LinkQueue(next, sizeof(next)));
    assert(!SPI_LinkSend(response, sizeof(response), 5u));
    assert(s_stage_ready && s_response_ready); /* Queue/tick-free contract remains. */
}
static void stuck_fifo_and_dma_stop_fail_closed_then_recover(void)
{
    uint8_t response[24], request[16], received[536];
    unsigned before;
    reset_fixture(); make_frame(response, sizeof(response), 0x61u);
    assert(SPI_LinkQueue(response, sizeof(response))); transaction(NULL, 16u);
    start_cs(); stuck_rxne = true; SPI2->SR = SPI_SR_RXNE;
    before = reads; SPI2_IRQHandler();
    assert(reads - before == 4u && SPI2->CR1 == 0u && s_resync);
    assert((SPI2->CR2 & SPI_CR2_RXNEIE) == 0u && s_response_ready);
    before = reads; SPI2_IRQHandler(); assert(reads == before);
    stuck_rxne = false; SPI2->SR = 0u; end_cs();
    assert(!s_resync && s_response_ready); /* Recovery boundary cannot ACK. */
    transaction(NULL, 536u); assert(!s_response_ready);
    reset_fixture(); make_frame(request, sizeof(request), 0x62u);
    start_cs(); feed_bytes(request, sizeof(request)); stuck_tx = true;
    before = barriers; end_cs();
    assert(barriers - before <= DMA_STOP_POLL_LIMIT + 1u);
    assert(s_resync && SPI2->CR1 == 0u && s_pending_length == 0u);
    stuck_tx = false; end_cs();
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
}
static void overrun_and_short_cs_do_not_publish_or_ack(void)
{
    uint8_t request[16], received[536];
    reset_fixture(); make_frame(request, sizeof(request), 0x71u);
    start_cs(); feed_bytes(request, 12u);
    SPI2->SR |= SPI_SR_OVR; feed_fifo(request + 12u, 4u, true); end_cs();
    assert(s_diagnostics.faults == 1u && SPI_LinkReceive(received, sizeof(received)) == 0u);
    start_cs(); feed_bytes(request, sizeof(request));
    EXTI->RPR1 = CS_PIN; SPI_LinkCsIRQ(); /* CS already low again: coalesced edges. */
    assert(s_resync && SPI2->CR1 == 0u && s_pending_length == 0u);
    end_cs(); transaction(request, sizeof(request));
    assert(SPI_LinkReceive(received, sizeof(received)) == sizeof(request));
}
static void diagnostic_reads_restore_rtc_clock_and_deinit_masks_irq(void)
{
    SPI_LinkDiagnostics d;
    uint32_t apb;
    unsigned before;
    reset_fixture();
    GPIOB->MODER = 0xFFFFA57Du; GPIOB->AFR[0] = 0x01234567u; GPIOB->AFR[1] = 0x89ABCDEFu;
    RTC->CR = 0x81234567u; TAMP->CR1 = 0x10203040u; RCC->BDCR = 0x12340000u;
    apb = RCC->APBENR1; before = rtc_clock_seen;
    SPI_LinkGetDiagnostics(&d);
    assert(d.rtc_cr == 0x81234567u && d.tamp_cr1 == 0x10203040u && d.rcc_bdcr == 0x12340000u);
    assert(RCC->APBENR1 == apb && rtc_clock_seen > before && RTC->CR == 0x81234567u);
    assert(TAMP->CR1 == 0x10203040u && RCC->BDCR == 0x12340000u);
    assert(d.gpio_b_moder == GPIOB->MODER && d.gpio_b_afr0 == GPIOB->AFR[0]);
    RCC->APBENR1 |= RCC_APBENR1_RTCAPBEN; apb = RCC->APBENR1;
    primask = 1u; SPI_LinkGetDiagnostics(&d); assert(primask == 1u); primask = 0u;
    assert(RCC->APBENR1 == apb);
    SPI_LinkDeinit();
    assert(enabled_irqs == 0u && SPI2->CR2 == 0u && !s_initialized);
    before = reads; SPI2->SR = SPI_SR_RXNE; SPI2_IRQHandler(); assert(reads == before);
}

int main(void)
{
    rxne_configuration_and_16_byte_request();
    rx_irq_can_precede_cs_falling_and_tail_can_follow_irq();
    full_frame_and_overflow_are_bounded();
    response_staging_ack_timeout_and_reread_are_preserved();
    stuck_fifo_and_dma_stop_fail_closed_then_recover();
    overrun_and_short_cs_do_not_publish_or_ack();
    diagnostic_reads_restore_rtc_clock_and_deinit_masks_irq();
    puts("PASS: 7 production SPI RXNE backend/FIFO/boundary/overflow/ACK/readback cases");
    return 0;
}

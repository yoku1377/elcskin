/* Register/CS fault injection against production spi_link.c. No fake driver. */
#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#include "../../firmware/shared/spi_link.c"

test_rcc_t test_rcc;
test_gpio_t test_gpio;
test_spi_t test_spi;
test_dma_t test_dma;
test_dma_channel_t test_dma_rx, test_dma_tx;
test_dmamux_t test_dmamux_rx, test_dmamux_tx;
test_exti_t test_exti;
static uint32_t primask, tick;
static unsigned watchdog_count, idle_count, irq_enable_count, irq_disable_count;
static unsigned plan, phase;
static bool inject_falling_after_arm;
static unsigned barrier_count;
static uint8_t stuck_dma_channels;
static bool stuck_rxne;
static uint8_t old_active_buffer[536];

uint32_t __get_PRIMASK(void) { return primask; }
void __disable_irq(void) { primask = 1u; }
void __set_PRIMASK(uint32_t value) { primask = value; }
void test_link_barrier(void)
{
    /* Model W1C completion plus hardware faults at real production barriers:
     * EN can reject a stop write, and RXNE can remain set after every DR read. */
    barrier_count++;
    EXTI->RPR1 = 0u; EXTI->FPR1 = 0u;
    if ((stuck_dma_channels & 1u) != 0u) DMA1_Channel1->CCR |= DMA_CCR_EN;
    if ((stuck_dma_channels & 2u) != 0u) DMA1_Channel2->CCR |= DMA_CCR_EN;
    if (stuck_rxne) SPI2->SR |= SPI_SR_RXNE;
    if (inject_falling_after_arm && SPI2->CR1 == SPI_CR1_SPE &&
        (DMA1_Channel1->CCR & DMA_CCR_EN) != 0u &&
        (SPI2->CR2 & SPI_CR2_RXDMAEN) != 0u && DMA1_Channel1->CNDTR == RX_SIZE) {
        inject_falling_after_arm = false;
        GPIOA->IDR &= ~CS_PIN;
        EXTI->FPR1 = CS_PIN;
    }
}
void NVIC_ClearPendingIRQ(int irq)
{ assert(irq == EXTI4_15_IRQn || irq == DMA1_Channel1_IRQn || irq == DMA1_Channel2_3_IRQn); }
void NVIC_SetPriority(int irq, unsigned priority)
{ assert(irq == EXTI4_15_IRQn && priority == 1u); }
void NVIC_EnableIRQ(int irq) { assert(irq == EXTI4_15_IRQn); irq_enable_count++; }
void NVIC_DisableIRQ(int irq) { assert(irq == EXTI4_15_IRQn); irq_disable_count++; }
static uint32_t tick_ms(void) { return tick++; }
static void watchdog(void) { watchdog_count++; }
static void cs_start(void)
{
    GPIOA->IDR &= ~CS_PIN;
    EXTI->FPR1 = CS_PIN; EXTI->RPR1 = 0u;
    SPI_LinkCsIRQ();
    EXTI->FPR1 = 0u;
    assert(s_cs_active == 1u);
}
static void cs_end(const uint8_t *bytes, uint16_t length, bool high)
{
    assert(length <= RX_SIZE);
    memset(s_rx, 0xFF, sizeof(s_rx));
    if (bytes != NULL) memcpy(s_rx, bytes, length);
    DMA1_Channel1->CNDTR = RX_SIZE - length;
    if (high) GPIOA->IDR |= CS_PIN;
    else GPIOA->IDR &= ~CS_PIN;
    EXTI->FPR1 = 0u; EXTI->RPR1 = CS_PIN;
    SPI_LinkCsIRQ();
    EXTI->RPR1 = EXTI->FPR1 = 0u;
    SPI2->SR = DMA1->ISR = 0u;
}
static void transaction(const uint8_t *bytes, uint16_t length)
{ cs_start(); cs_end(bytes, length, true); }
static void idle_callback(void)
{
    idle_count++;
    if (plan == 1u) {
        if (phase == 0u) {
            /* First dummy CS transaction installs the fully staged frame. */
            assert(s_stage_ready && !s_response_available);
            assert(!(DMA1_Channel2->CCR & DMA_CCR_MINC));
            transaction(NULL, 536u); phase++;
            assert(s_response_ready && !s_stage_ready && s_response_available);
        } else if (phase == 1u) {
            transaction(NULL, (uint16_t)(s_response_length - 1u)); phase++;
            assert(s_response_ready); /* Short reads must retain the ACK. */
        } else if (phase == 2u) {
            transaction(NULL, 536u); phase++;
            assert(!s_response_ready && s_response_available);
        }
    } else if (plan == 2u) {
        if (phase == 0u) {
            assert(s_cs_active && s_stage_ready);
            assert(memcmp(old_active_buffer, s_tx[s_active_tx], 536u) == 0);
            cs_end(NULL, 536u, true); phase++;
            /* Completing an old response must not acknowledge the new stage. */
            assert(s_response_ready && !s_stage_ready);
        } else if (phase == 1u) {
            transaction(NULL, 536u); phase++;
            assert(!s_response_ready);
        }
    }
}
static void reset_fixture(void)
{
    SPI_LinkCallbacks callbacks = {tick_ms, idle_callback, watchdog};
    memset(&test_rcc, 0, sizeof(test_rcc)); memset(&test_gpio, 0, sizeof(test_gpio));
    memset(&test_spi, 0, sizeof(test_spi)); memset(&test_dma, 0, sizeof(test_dma));
    memset(&test_dma_rx, 0, sizeof(test_dma_rx)); memset(&test_dma_tx, 0, sizeof(test_dma_tx));
    memset(&test_dmamux_rx, 0, sizeof(test_dmamux_rx)); memset(&test_dmamux_tx, 0, sizeof(test_dmamux_tx));
    memset(&test_exti, 0, sizeof(test_exti));
    memset(s_tx, 0, sizeof(s_tx));
    primask = tick = watchdog_count = idle_count = irq_enable_count = irq_disable_count = plan = phase = 0u;
    inject_falling_after_arm = false;
    barrier_count = 0u; stuck_dma_channels = 0u; stuck_rxne = false;
    GPIOA->IDR = CS_PIN;
    SPI_LinkInit(&callbacks);
}
static void make_response(uint8_t *bytes, uint16_t length, uint8_t tag)
{ memset(bytes, tag, length); memcpy(bytes, "TAI3", 4u); }
static void init_arms_constant_idle_and_restores_irq(void)
{
    reset_fixture();
    assert(s_initialized && primask == 0u && irq_enable_count == 1u);
    assert(RCC->IOPENR & RCC_IOPENR_GPIOAEN);
    assert(RCC->AHBENR & RCC_AHBENR_DMA1EN);
    assert(RCC->APBENR1 & RCC_APBENR1_SPI2EN);
    assert(DMA1_Channel1->CNDTR == 537u && DMA1_Channel2->CNDTR == 536u);
    assert(DMA1_Channel1->CCR & DMA_CCR_MINC);
    assert(!(DMA1_Channel2->CCR & DMA_CCR_MINC));
    assert(DMA1_Channel2->CMAR == (uint32_t)(uintptr_t)&s_idle_ff);
    assert(s_idle_ff == 0xFFu && !SPI_LinkResponsePending());
}
static void request_is_owned_once_and_retained_for_capacity(void)
{
    uint8_t request[24], output[536], replacement[24];
    reset_fixture(); make_response(request, 24u, 0xA1u); make_response(replacement, 24u, 0xB2u);
    cs_start(); memcpy(s_rx, request, 24u); DMA1_Channel1->CNDTR = RX_SIZE - 24u;
    assert(SPI_LinkReceive(output, 536u) == 0u); /* Until CS high, nothing is published. */
    cs_end(request, 24u, true);
    assert(s_pending_length == 24u);
    transaction(replacement, 24u);
    assert(SPI_LinkReceive(output, 535u) == 0u && s_pending_length == 24u);
    assert(SPI_LinkReceive(NULL, 536u) == 0u && s_pending_length == 24u);
    assert(SPI_LinkReceive(output, 536u) == 24u && memcmp(output, request, 24u) == 0);
    assert(SPI_LinkReceive(output, 536u) == 0u && primask == 0u);
}
static void malformed_faulted_and_oversized_transactions_are_discarded(void)
{
    uint8_t request[537];
    reset_fixture(); make_response(request, 537u, 0x12u);
    transaction(request, 15u); assert(s_pending_length == 0u);
    transaction(NULL, 536u); assert(s_pending_length == 0u);
    transaction(request, 537u); assert(s_pending_length == 0u);
    cs_start(); DMA1->ISR = DMA_ISR_TEIF1; cs_end(request, 24u, true); assert(s_pending_length == 0u);
    cs_start(); DMA1->ISR = DMA_ISR_TEIF2; cs_end(request, 24u, true); assert(s_pending_length == 0u);
    cs_start(); SPI2->SR = SPI_SR_OVR; cs_end(request, 24u, true); assert(s_pending_length == 0u);
}
static void coalesced_cs_fails_closed_and_resynchronises(void)
{
    uint8_t request[24], output[536];
    reset_fixture(); make_response(request, 24u, 0x12u);
    cs_start(); cs_end(request, 24u, false);
    assert(s_resync && !s_cs_active && SPI2->CR1 == 0u && s_pending_length == 0u);
    cs_end(request, 24u, true);
    assert(!s_resync && s_pending_length == 0u);
    transaction(request, 24u);
    assert(SPI_LinkReceive(output, 536u) == 24u);
    reset_fixture(); inject_falling_after_arm = true;
    transaction(NULL, 536u); /* Falling edge arrives during the preceding rising ISR's register reload. */
    assert(s_resync && !(GPIOA->IDR & CS_PIN) && s_pending_length == 0u);
    cs_end(request, 24u, true);
    assert(!s_resync && s_pending_length == 0u);
    transaction(request, 24u);
    assert(SPI_LinkReceive(output, 536u) == 24u);
}
static void staged_send_short_read_and_reread_preserve_ack(void)
{
    uint8_t response[24], request[16], received[536], snapshot[536], active;
    unsigned i;
    reset_fixture(); make_response(response, 24u, 0x33u); plan = 1u;
    assert(SPI_LinkSend(response, 24u, 50u));
    assert(phase == 3u && watchdog_count == 3u && idle_count == 3u);
    assert(!SPI_LinkResponsePending() && s_response_available);
    assert(memcmp(s_tx[s_active_tx], response, 24u) == 0);
    for (i = 24u; i < 536u; i++) assert(s_tx[s_active_tx][i] == 0xFFu);
    memcpy(snapshot, s_tx[s_active_tx], 536u); active = s_active_tx;
    transaction(NULL, 536u); /* A corrupt host read may re-read this same ACK. */
    assert(s_response_available && !SPI_LinkResponsePending());
    assert(s_active_tx == active && memcmp(snapshot, s_tx[s_active_tx], 536u) == 0);
    make_response(request, 16u, 0x20u); cs_start();
    assert(s_cs_response && s_response_available);
    cs_end(request, 16u, true);
    assert(!s_response_available && SPI_LinkReceive(received, 536u) == 16u);
    assert(memcmp(received, request, 16u) == 0);
    assert(!(DMA1_Channel2->CCR & DMA_CCR_MINC));
}
static void timeout_keeps_stage_and_blocks_overwrite(void)
{
    uint8_t response[24], replacement[24], staged[536], contaminated[24];
    reset_fixture(); make_response(response, 24u, 0x44u); make_response(replacement, 24u, 0x55u);
    assert(!SPI_LinkSend(response, 24u, 5u));
    assert(SPI_LinkResponsePending() && s_stage_ready && s_response_ready);
    memcpy(staged, s_tx[s_stage_tx], 536u);
    assert(!SPI_LinkSend(replacement, 24u, 5u));
    assert(memcmp(staged, s_tx[s_stage_tx], 536u) == 0);
    transaction(NULL, 536u); assert(s_response_available && SPI_LinkResponsePending());
    transaction(NULL, 23u); assert(SPI_LinkResponsePending());
    transaction(NULL, 537u); assert(SPI_LinkResponsePending());
    memset(contaminated, 0xFF, sizeof(contaminated)); contaminated[4] = 0u;
    transaction(contaminated, 24u); assert(SPI_LinkResponsePending());
    transaction(NULL, 24u); assert(!SPI_LinkResponsePending() && s_response_available);
}
static void new_stage_cannot_be_acknowledged_by_old_inflight_read(void)
{
    uint8_t previous[24], next[24];
    reset_fixture(); make_response(previous, 24u, 0x66u); plan = 1u;
    assert(SPI_LinkSend(previous, 24u, 50u));
    assert(s_response_available && !s_response_ready);
    memcpy(old_active_buffer, s_tx[s_active_tx], 536u);
    cs_start(); /* This transfer was armed with the old, already acknowledged frame. */
    make_response(next, 24u, 0x77u); plan = 2u; phase = 0u;
    assert(SPI_LinkSend(next, 24u, 50u));
    assert(phase == 2u && !SPI_LinkResponsePending());
    assert(memcmp(s_tx[s_active_tx], next, 24u) == 0);
}
static void masked_send_and_deinit_are_safe(void)
{
    uint8_t response[24], output[536];
    reset_fixture(); make_response(response, 24u, 0x88u);
    primask = 1u; assert(!SPI_LinkSend(response, 24u, 5u)); primask = 0u;
    assert(!SPI_LinkSend(NULL, 24u, 5u));
    assert(!SPI_LinkSend(response, 15u, 5u));
    assert(!SPI_LinkSend(response, 537u, 5u));
    assert(!SPI_LinkResponsePending());
    assert(!SPI_LinkSend(response, 24u, 5u));
    SPI_LinkDeinit();
    assert(!s_initialized && !SPI_LinkResponsePending() && irq_disable_count == 1u);
    assert(SPI_LinkReceive(output, 536u) == 0u && !SPI_LinkSend(response, 24u, 5u));
    assert(SPI2->CR1 == 0u && SPI2->CR2 == 0u && DMA1_Channel1->CCR == 0u && DMA1_Channel2->CCR == 0u);
}
static void queue_is_nonblocking_tick_independent_and_preserves_unread_stage(void)
{
    uint8_t response[24], replacement[24], staged[536];
    reset_fixture(); make_response(response, 24u, 0x91u); make_response(replacement, 24u, 0x92u);
    SPI_LinkInit(NULL); /* Queue does not require or call a tick callback. */
    assert(SPI_LinkQueue(response, sizeof(response)));
    assert(tick == 0u && idle_count == 0u && watchdog_count == 0u);
    memcpy(staged, s_tx[s_stage_tx], sizeof(staged));
    assert(!SPI_LinkQueue(replacement, sizeof(replacement)));
    assert(memcmp(staged, s_tx[s_stage_tx], sizeof(staged)) == 0);
    assert(!SPI_LinkSend(replacement, sizeof(replacement), 5u));
    transaction(NULL, 536u); assert(s_response_available && s_response_ready);
    assert(!SPI_LinkQueue(replacement, sizeof(replacement)));
    transaction(NULL, 536u); assert(!SPI_LinkResponsePending());
    assert(SPI_LinkQueue(replacement, sizeof(replacement)));
    assert(memcmp(s_tx[s_stage_tx], replacement, sizeof(replacement)) == 0);
    assert(tick == 0u && idle_count == 0u && watchdog_count == 0u);
    primask = 1u; assert(!SPI_LinkQueue(response, sizeof(response))); primask = 0u;
    assert(!SPI_LinkQueue(NULL, sizeof(response)));
    assert(!SPI_LinkQueue(response, 15u) && !SPI_LinkQueue(response, 537u));
}
static void stuck_dma_stop_discards_transaction_without_rewriting_active_channels(void)
{
    uint8_t request[24], output[536];
    uint32_t cpar, cmar, left;
    unsigned mask, before;
    for (mask = 1u; mask <= 3u; ++mask) {
        reset_fixture(); make_response(request, sizeof(request), 0x93u);
        cs_start();
        cpar = DMA1_Channel1->CPAR; cmar = DMA1_Channel1->CMAR;
        stuck_dma_channels = (uint8_t)mask;
        before = barrier_count;
        cs_end(request, sizeof(request), true);
        assert(barrier_count - before <= DMA_STOP_POLL_LIMIT + 1u);
        assert(SPI2->CR1 == 0u &&
               (SPI2->CR2 & (SPI_CR2_RXDMAEN | SPI_CR2_TXDMAEN)) == 0u);
        assert(s_resync && !s_cs_active && s_pending_length == 0u);
        assert(DMA1_Channel1->CPAR == cpar && DMA1_Channel1->CMAR == cmar);
        left = DMA1_Channel1->CNDTR;
        assert(left == RX_SIZE - sizeof(request));
        assert(!arm_transaction()); /* No active-channel CNDTR/address rewrite. */
        assert(DMA1_Channel1->CNDTR == left && DMA1_Channel1->CMAR == cmar);
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
        assert(s_diagnostics.faults == 2u);
#endif
        stuck_dma_channels = 0u;
        cs_end(request, sizeof(request), true); /* Recovery boundary discards bytes. */
        assert(!s_resync && SPI2->CR1 == SPI_CR1_SPE && s_pending_length == 0u);
        transaction(request, sizeof(request));
        assert(SPI_LinkReceive(output, sizeof(output)) == sizeof(request));
    }
}
static void stuck_rxne_is_bounded_and_retains_response_until_recovery_read(void)
{
    uint8_t response[24], snapshot[536];
    unsigned before;
    reset_fixture(); make_response(response, sizeof(response), 0x94u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    transaction(NULL, 536u); /* Commit the response before the failed read. */
    memcpy(snapshot, s_tx[s_active_tx], sizeof(snapshot));
    cs_start(); stuck_rxne = true; SPI2->DR = 0xFFu;
    before = barrier_count;
    cs_end(NULL, 536u, true);
    assert(barrier_count - before <= RX_FIFO_DRAIN_LIMIT + 2u);
    assert(SPI2->CR1 == 0u && s_resync && s_response_ready && s_response_available);
    assert(memcmp(snapshot, s_tx[s_active_tx], sizeof(snapshot)) == 0);
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
    assert(s_diagnostics.faults == 1u && s_diagnostics.acknowledged == 0u);
#endif
    stuck_rxne = false;
    cs_end(NULL, 536u, true); /* Disabled SPI cannot acknowledge this recovery transaction. */
    assert(!s_resync && s_response_ready && SPI2->CR1 == SPI_CR1_SPE);
    transaction(NULL, 536u);
    assert(!SPI_LinkResponsePending() && s_response_available);
    assert(memcmp(snapshot, s_tx[s_active_tx], sizeof(snapshot)) == 0);
}
static void init_and_deinit_do_not_reconfigure_dma_that_refuses_to_stop(void)
{
    SPI_LinkCallbacks callbacks = {tick_ms, idle_callback, watchdog};
    uint32_t cpar, cmar, left;
    unsigned before;
    reset_fixture();
    cpar = DMA1_Channel1->CPAR; cmar = DMA1_Channel1->CMAR;
    DMA1_Channel1->CNDTR = left = 123u;
    stuck_dma_channels = 1u;
    before = barrier_count;
    SPI_LinkInit(&callbacks);
    assert(barrier_count - before <= DMA_STOP_POLL_LIMIT + 1u);
    assert(s_initialized && s_resync && SPI2->CR1 == 0u && primask == 0u);
    assert(DMA1_Channel1->CNDTR == left && DMA1_Channel1->CPAR == cpar && DMA1_Channel1->CMAR == cmar);
    stuck_dma_channels = 0u;
    cs_end(NULL, 0u, true);
    assert(!s_resync && SPI2->CR1 == SPI_CR1_SPE);
    stuck_dma_channels = 2u;
    DMA1->IFCR = 0xC0FFEEu;
    before = barrier_count;
    SPI_LinkDeinit();
    assert(barrier_count - before <= DMA_STOP_POLL_LIMIT);
    assert(!s_initialized && SPI2->CR1 == 0u && SPI2->CR2 == 0u && primask == 0u);
    assert((EXTI->IMR1 & CS_PIN) == 0u && irq_disable_count == 1u);
    assert(DMAMUX1_Channel1->CCR == SPI2_DMA_TX_REQUEST && DMA1->IFCR == 0xC0FFEEu);
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
    assert(s_diagnostics.faults == 2u);
#endif
    stuck_dma_channels = 0u;
}
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
static void diagnostic_evidence_survives_idle_polls_and_preserves_state(void)
{
    SPI_LinkDiagnostics d;
    uint8_t request[16], response[24], received[536];
    reset_fixture();
    SPI_LinkGetDiagnostics(NULL);
    SPI_LinkGetDiagnostics(&d);
    assert(d.flags == 1u && d.falling == 0u && d.rising == 0u);
    assert(d.rx_left == 537u && d.tx_left == 536u && d.spi_cr1 == SPI_CR1_SPE);
    cs_start(); cs_end(NULL, 0u, true);
    SPI_LinkGetDiagnostics(&d);
    assert(d.falling == 1u && d.rising == 1u && d.last_length == 0u && d.requests == 0u);
    make_response(request, 16u, 0x20u);
    transaction(request, 16u);
    transaction(NULL, 536u);
    SPI_LinkGetDiagnostics(&d);
    assert(d.transactions == 3u && d.requests == 1u && d.last_length == 536u);
    assert(d.data_length == 16u && d.data_head == 0x54414933u);
    assert(s_pending_length == 16u); /* Getter does not consume the request. */
    assert(SPI_LinkReceive(received, sizeof(received)) == 16u);
    cs_start(); DMA1->ISR = DMA_ISR_TEIF1; cs_end(NULL, 536u, true);
    SPI_LinkGetDiagnostics(&d); assert(d.faults == 1u);
    make_response(response, 24u, 0xABu); plan = 1u;
    assert(SPI_LinkSend(response, sizeof(response), 50u));
    transaction(NULL, 536u); /* A reread must not count a second ACK. */
    SPI_LinkGetDiagnostics(&d);
    assert(d.staged == 1u && d.committed == 1u && d.acknowledged == 1u);
    assert(d.requests == 1u && d.data_head == 0x54414933u);
    primask = 1u; SPI_LinkGetDiagnostics(&d); assert(primask == 1u); primask = 0u;
    SPI_LinkDeinit(); SPI_LinkGetDiagnostics(&d);
    assert(d.flags == 0u && d.transactions != 0u);
}
#endif

int main(void)
{
    init_arms_constant_idle_and_restores_irq(); request_is_owned_once_and_retained_for_capacity();
    malformed_faulted_and_oversized_transactions_are_discarded(); coalesced_cs_fails_closed_and_resynchronises();
    staged_send_short_read_and_reread_preserve_ack(); timeout_keeps_stage_and_blocks_overwrite();
    new_stage_cannot_be_acknowledged_by_old_inflight_read(); masked_send_and_deinit_are_safe();
    queue_is_nonblocking_tick_independent_and_preserves_unread_stage();
    stuck_dma_stop_discards_transaction_without_rewriting_active_channels();
    stuck_rxne_is_bounded_and_retains_response_until_recovery_read();
    init_and_deinit_do_not_reconfigure_dma_that_refuses_to_stop();
#if defined(SPI_LINK_DIAGNOSTICS) && SPI_LINK_DIAGNOSTICS
    diagnostic_evidence_survives_idle_polls_and_preserves_state();
    puts("PASS: 13 production SPI link diagnostic/CS/staging/fault/ACK cases");
#else
    puts("PASS: 12 production SPI link CS/staging/fault/ACK cases");
#endif
    return 0;
}

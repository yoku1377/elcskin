/* Reproduce host-visible valid old snapshots after an unconsumed response.
 * Reuse the register/FIFO model, but execute the production spi_link.c state
 * machine. No production firmware, USB, GPIO or flash is modified here. */
#define main existing_rxirq_harness_main
#include "spi_link_rxirq_harness.c"
#undef main

static uint32_t frame_crc32(const uint8_t *data, unsigned length)
{
    uint32_t crc = 0xFFFFFFFFu;
    unsigned bit;
    while (length-- != 0u) {
        crc ^= *data++;
        for (bit = 0u; bit < 8u; ++bit)
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static void put_crc32(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16); p[3] = (uint8_t)(value >> 24);
}

static void snapshot_request(uint8_t *frame, uint16_t sequence)
{
    memset(frame, 0, 16u); memcpy(frame, "TAI3", 4u);
    frame[4] = 0x20u;
    frame[6] = (uint8_t)sequence; frame[7] = (uint8_t)(sequence >> 8);
    put_crc32(frame + 12u, frame_crc32(frame, 12u));
}

static void snapshot_response(uint8_t *frame, uint16_t sequence)
{
    memset(frame, 0, 180u); memcpy(frame, "TAI3", 4u);
    frame[4] = 0xA0u;
    frame[6] = (uint8_t)sequence; frame[7] = (uint8_t)(sequence >> 8);
    frame[8] = 164u; frame[12] = 1u; /* Snapshot version. */
    put_crc32(frame + 176u, frame_crc32(frame, 176u));
}

static void assert_snapshot_wire(const uint8_t *frame, uint16_t sequence)
{
    uint32_t stored = frame[176] | ((uint32_t)frame[177] << 8) |
        ((uint32_t)frame[178] << 16) | ((uint32_t)frame[179] << 24);
    assert(memcmp(frame, "TAI3", 4u) == 0 && frame[4] == 0xA0u);
    assert(frame[6] == (uint8_t)sequence && frame[7] == (uint8_t)(sequence >> 8));
    assert(frame[8] == 164u && frame[9] == 0u);
    assert(frame_crc32(frame, 176u) == stored);
}

/* TX bytes are captured independently from MOSI RX status, as on real SPI.
 * A receive-side overrun/corrupted dummy byte does not invalidate MISO CRC. */
static void capture_poll(uint8_t *miso, unsigned fault_mode)
{
    uint8_t dummy[536];
    memset(dummy, 0xFF, sizeof(dummy));
    if (fault_mode == 2u) dummy[200] = 0x00u;
    if (s_response_available) memcpy(miso, s_tx[s_active_tx], 536u);
    else memset(miso, 0xFF, 536u);
    start_cs(); feed_bytes(dummy, sizeof(dummy));
    if (fault_mode == 1u) SPI2->SR |= SPI_SR_OVR;
    end_cs();
}

static void old_frame_persists_until_read_only_request_is_reissued(unsigned fault_mode)
{
    uint8_t request[16], pending[536], response[180], miso[536];
    unsigned poll;
    reset_fixture();
    snapshot_request(request, 4152u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == sizeof(request));
    snapshot_response(response, 4152u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u); /* Staged response commits after this CS. */
    assert(miso[0] == 0xFFu && s_response_ready);
    capture_poll(miso, fault_mode);
    assert_snapshot_wire(miso, 4152u); /* Host accepts a complete valid ACK. */
    assert(s_response_ready && SPI_LinkResponsePending());

    snapshot_request(request, 4153u);
    transaction(request, sizeof(request)); /* Firmware still awaits old ACK. */
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 0u);
    assert(s_diagnostics.requests == 1u);
    for (poll = 0u; poll < 39u; ++poll) {
        capture_poll(miso, 0u);
        assert_snapshot_wire(miso, 4152u);
        assert(!s_response_ready && s_response_available);
        assert(SPI_LinkReceive(pending, sizeof(pending)) == 0u);
    }
    assert(s_diagnostics.acknowledged == 1u);

    /* Snapshot 0x20 is read-only. Reissue after observing stale responses;
     * the production transport must not generalize this to flash writes. */
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == sizeof(request));
    assert(memcmp(pending, request, sizeof(request)) == 0);
    assert(s_diagnostics.requests == 2u && !s_response_available);
    snapshot_response(response, 4153u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u); assert(miso[0] == 0xFFu);
    capture_poll(miso, 0u); assert_snapshot_wire(miso, 4153u);
    assert(s_diagnostics.acknowledged == 2u && !SPI_LinkResponsePending());
}

static void faulty_new_request_also_leaves_valid_cached_old_frame(void)
{
    uint8_t request[16], pending[536], response[180], miso[536];
    unsigned poll;
    reset_fixture();
    snapshot_request(request, 4152u); transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == sizeof(request));
    snapshot_response(response, 4152u); assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u); capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 4152u); assert(!s_response_ready);
    snapshot_request(request, 4153u);
    start_cs(); feed_bytes(request, sizeof(request));
    SPI2->SR |= SPI_SR_OVR; end_cs();
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 0u);
    for (poll = 0u; poll < 39u; ++poll) {
        capture_poll(miso, 0u); assert_snapshot_wire(miso, 4152u);
    }
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == sizeof(request));
    snapshot_response(response, 4153u); assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u); capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 4153u);
}

static void guard_seed_response(unsigned receive_fault)
{
    uint8_t request[16], pending[536], response[180], miso[536];
    reset_fixture();
    snapshot_request(request, 500u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 16u);
    snapshot_response(response, 500u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u); /* Staging boundary. */
    capture_poll(miso, receive_fault); /* MISO is valid despite injected RX fault. */
    assert_snapshot_wire(miso, 500u);
    assert(s_response_ready == (receive_fault != 0u));
}

static void guard_accepted_short_request_is_not_overwritten_by_polls(void)
{
    uint8_t request[16], pending[536], response[180], miso[536];
    unsigned index;
    reset_fixture();
    snapshot_request(request, 501u);
    transaction(request, sizeof(request));
    assert(s_pending_length == 16u);
    for (index = 0u; index < 100u; ++index) {
        capture_poll(miso, 0u);
        assert(s_pending_length == 16u);
        assert(memcmp(s_pending, request, 16u) == 0);
    }
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 16u);
    assert(memcmp(pending, request, 16u) == 0);
    snapshot_response(response, 501u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u);
    capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 501u);
    puts("PASS mailbox: accepted 16-byte request survives100 later536-byte polls without main-loop service");
}

static void guard_one_clean_extra_poll_unblocks_next_snapshot(unsigned fault)
{
    uint8_t request[16], pending[536], response[180], miso[536];
    guard_seed_response(fault);
    capture_poll(miso, 0u); /* Additional clocks after successful host ACK. */
    assert_snapshot_wire(miso, 500u);
    assert(!SPI_LinkResponsePending());
    snapshot_request(request, 501u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 16u);
    assert(memcmp(pending, request, 16u) == 0);
    snapshot_response(response, 501u);
    assert(SPI_LinkQueue(response, sizeof(response)));
    capture_poll(miso, 0u);
    capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 501u);
    printf("PASS preclear: receive_fault=%u, one clean extraFF poll prevents next-request loss\n", fault);
}

static void guard_faulty_extra_poll_is_not_a_proof_of_ack_consumption(unsigned fault)
{
    uint8_t request[16], pending[536], miso[536];
    guard_seed_response(1u);
    capture_poll(miso, fault);
    assert_snapshot_wire(miso, 500u); /* Host cannot see the MOSI receive fault. */
    assert(SPI_LinkResponsePending());
    snapshot_request(request, 501u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 0u);
    capture_poll(miso, 0u); /* Clears pending, but501 was already dropped. */
    assert(!SPI_LinkResponsePending());
    assert_snapshot_wire(miso, 500u);
    snapshot_request(request, 502u);
    transaction(request, sizeof(request));
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 16u);
    printf("PASS limitation: receive_fault=%u on extra poll still leaves valid oldACK and drops501; clean poll plus502 recovers\n",fault);
}

static void guard_healthy_extra_poll_is_only_a_cached_read(void)
{
    uint8_t miso[536];
    guard_seed_response(0u);
    assert(!SPI_LinkResponsePending());
    assert(s_diagnostics.acknowledged == 1u);
    capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 500u);
    assert(s_diagnostics.acknowledged == 1u && s_diagnostics.requests == 1u);
    puts("PASS healthy: extraFF clocks do not create a request, new sample, or second ACK");
}

static void guard_request_rx_fault_is_not_repaired_by_a_preclear(void)
{
    uint8_t request[16], pending[536], miso[536];
    guard_seed_response(0u);
    capture_poll(miso, 0u);
    snapshot_request(request, 501u);
    start_cs(); feed_bytes(request, sizeof(request));
    SPI2->SR |= SPI_SR_OVR; end_cs();
    assert(SPI_LinkReceive(pending, sizeof(pending)) == 0u);
    capture_poll(miso, 0u);
    assert_snapshot_wire(miso, 500u);
    puts("PASS limitation: receive overrun on the new16-byte request still needs bounded read-only reissue");
}

int main(void)
{
    old_frame_persists_until_read_only_request_is_reissued(1u);
    old_frame_persists_until_read_only_request_is_reissued(2u);
    faulty_new_request_also_leaves_valid_cached_old_frame();
    puts("PASS: 3 production RXIRQ stale snapshot/reissue recovery cases");
    guard_accepted_short_request_is_not_overwritten_by_polls();
    guard_one_clean_extra_poll_unblocks_next_snapshot(1u);
    guard_one_clean_extra_poll_unblocks_next_snapshot(2u);
    guard_faulty_extra_poll_is_not_a_proof_of_ack_consumption(1u);
    guard_faulty_extra_poll_is_not_a_proof_of_ack_consumption(2u);
    guard_healthy_extra_poll_is_only_a_cached_read();
    guard_request_rx_fault_is_not_repaired_by_a_preclear();
    puts("PASS: 7 production RXIRQ/mailbox/snapshot ACK-drain guard cases");
    return 0;
}

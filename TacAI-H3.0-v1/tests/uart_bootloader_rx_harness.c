/* Deterministic UART/tick injection into the production bootloader parser. */
#include <stdint.h>
#include <stdio.h>
#include <string.h>

static int injected_read_byte(uint8_t *value, uint32_t timeout_ms);
#define BOOTLOADER_UART_BYTE_TEST injected_read_byte
#define main bootloader_unused_main
#ifndef UART_BOOTLOADER_SOURCE
#define UART_BOOTLOADER_SOURCE "../firmware/bootloader/src/main.c"
#endif
#include UART_BOOTLOADER_SOURCE
#undef main

test_rcc_t test_rcc;
test_gpio_t test_gpiob;
test_usart_t test_usart;
test_iwdg_t test_iwdg;
test_flash_t test_flash;
test_systick_t test_systick;
test_scb_t test_scb;
uint32_t SystemCoreClock;

static uint8_t wire[1024];
static uint64_t arrival_us[1024], now_us;
static unsigned wire_length, wire_position, byte_reads;

static void reset_input(void)
{
    memset(wire, 0, sizeof(wire));
    memset(arrival_us, 0, sizeof(arrival_us));
    now_us = 0u;
    g_ms = 0u;
    wire_length = wire_position = byte_reads = 0u;
}

static void append_byte(uint8_t value, uint64_t at_us)
{
    wire[wire_length] = value;
    arrival_us[wire_length++] = at_us;
}

static int injected_read_byte(uint8_t *value, uint32_t timeout_ms)
{
    uint64_t deadline = ((uint64_t)g_ms + timeout_ms) * 1000u;
    ++byte_reads;
    /* Like the hardware loop, already available RXNE wins over a timeout. */
    if (wire_position < wire_length && arrival_us[wire_position] <= now_us) {
        *value = wire[wire_position++];
        return 1;
    }
    if (wire_position < wire_length &&
        (timeout_ms == 0xFFFFFFFFu || arrival_us[wire_position] < deadline)) {
        now_us = arrival_us[wire_position];
        g_ms = (uint32_t)(now_us / 1000u);
        *value = wire[wire_position++];
        return 1;
    }
    if (timeout_ms == 0xFFFFFFFFu) {
        fprintf(stderr, "invalid test: unbounded read with no queued byte\n");
        return 0;
    }
    now_us = deadline;
    g_ms = (uint32_t)(now_us / 1000u);
    return 0;
}

static uint32_t reference_crc(const uint8_t *data, unsigned length)
{
    uint32_t crc = 0xFFFFFFFFu;
    unsigned bit;
    while (length-- != 0u) {
        crc ^= *data++;
        for (bit = 0; bit < 8u; ++bit)
            crc = (crc >> 1u) ^ ((crc & 1u) ? 0xEDB88320u : 0u);
    }
    return ~crc;
}

static void append_frame(uint64_t first_byte_us, uint8_t command,
                         uint16_t sequence, unsigned payload_length)
{
    uint8_t bytes[536];
    uint32_t crc;
    unsigned i, length = 12u + payload_length;
    memset(bytes, 0, sizeof(bytes));
    memcpy(bytes, "TAI3", 4u);
    bytes[4] = command;
    bytes[6] = (uint8_t)sequence;
    bytes[7] = (uint8_t)(sequence >> 8u);
    bytes[8] = (uint8_t)payload_length;
    bytes[9] = (uint8_t)(payload_length >> 8u);
    /* DATA offset 0x800 and 512 bytes, as in the reported failure. */
    if (payload_length == 516u) {
        bytes[13] = 8u;
        for (i = 16u; i < length; ++i) bytes[i] = (uint8_t)(i * 13u);
    }
    crc = reference_crc(bytes, length);
    for (i = 0u; i < 4u; ++i) bytes[length + i] = (uint8_t)(crc >> (8u * i));
    for (i = 0u; i < length + 4u; ++i) {
        /* One 8N1 byte every 86.806 us at 115200 baud (integer ceil). */
        append_byte(bytes[i], first_byte_us + ((uint64_t)i * 10000000u + 115199u) / 115200u);
    }
}

#define CHECK(condition, message) do { \
    if (!(condition)) { fprintf(stderr, "FAIL: %s (line %d)\n", message, __LINE__); return 1; } \
} while (0)

static int phase_boundary_data(void)
{
    frame_t frame;
    reset_input();
    append_frame(1800u, CMD_DATA, 7u, 516u);
    CHECK(receive_frame(&frame, 2u) == 1, "magic-window boundary must retain TAI prefix");
    CHECK(frame.header.command == CMD_DATA && frame.header.sequence == 7u,
          "DATA command and sequence retained");
    CHECK(frame.header.length == 516u && frame.payload[1] == 8u,
          "DATA offset 0x800 and 512-byte payload retained");
    CHECK(wire_position == 532u, "complete CRC-verified DATA received");
    return 0;
}

int main(int argc, char **argv)
{
    frame_t frame;
    unsigned phase, i;
    if (phase_boundary_data() != 0) return 1;
    if (argc > 1 && strcmp(argv[1], "--boundary-only") == 0) {
        puts("PASS: production DATA magic-window boundary");
        return 0;
    }
    /* Sweep the whole poll interval, including first T at 1999 us. */
    for (phase = 1u; phase < 2000u; phase += 7u) {
        reset_input();
        append_frame(phase, CMD_DATA, 7u, 516u);
        CHECK(receive_frame(&frame, 2u) == 1, "phase sweep DATA reception");
    }
    reset_input();
    append_frame(1999u, CMD_HELLO, 1u, 0u);
    CHECK(receive_frame(&frame, 2u) == 1, "first magic byte at final microsecond");
    reset_input();
    CHECK(receive_frame(&frame, 2u) == 0 && now_us == 2000u,
          "empty input retains bounded 2 ms SPI polling budget");
    reset_input();
    append_byte('T', 1800u);
    CHECK(receive_frame(&frame, 2u) == 0 && now_us == 501000u,
          "partial magic retains bounded 500 ms inter-byte timeout");
    reset_input();
    append_byte('T', 100u);
    append_byte('x', 200u);
    CHECK(receive_frame(&frame, 2u) == 0 && now_us == 2000u,
          "mismatch resumes original first-byte budget");
    reset_input();
    append_frame(1500u, CMD_DATA, 7u, 516u);
    wire[wire_length - 1u] ^= 1u;
    CHECK(receive_frame(&frame, 2u) == -1, "bad CRC remains rejected");
    reset_input();
    append_frame(1000u, CMD_HELLO, 1u, 0u);
    wire[8] = 9u; wire[9] = 2u; /* 521 > maximum 520 */
    CHECK(receive_frame(&frame, 2u) == -1, "oversized header remains rejected");
    reset_input();
    append_frame(1000u, CMD_HELLO, 1u, 0u);
    wire[10] = 1u;
    CHECK(receive_frame(&frame, 2u) == -1, "reserved header remains rejected");
    reset_input();
    append_frame(12000u, CMD_HELLO, 1u, 0u);
    CHECK(receive_frame(&frame, 0xFFFFFFFFu) == 1,
          "legacy UART-only infinite first-byte wait retained");
    reset_input();
    for (i = 0u; i < 8u; ++i) append_byte((uint8_t)'x', 100u + i * 100u);
    append_byte('T', 900u); append_byte('x', 1000u);
    append_frame(1800u, CMD_HELLO, 3u, 0u);
    CHECK(receive_frame(&frame, 2u) == 1 && frame.header.sequence == 3u,
          "noise and false magic prefix resynchronize");
    reset_input();
    append_byte('T', 1800u); append_byte('T', 1900u);
    append_frame(1950u, CMD_HELLO, 4u, 0u);
    CHECK(receive_frame(&frame, 2u) == 1 && frame.header.sequence == 4u,
          "overlapping T prefixes retain resynchronization");
    printf("PASS: production UART bootloader RX; 286 phase cases and 11 boundary/fault cases\n");
    return 0;
}

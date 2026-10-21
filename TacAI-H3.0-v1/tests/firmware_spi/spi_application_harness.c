/* Execute production app_spi.c, mocking only hardware and the link boundary. */
#ifndef _WIN32
#define _GNU_SOURCE
#endif
#include <assert.h>
#include <stddef.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
#ifdef _WIN32
#include <windows.h>
#else
#include <sys/mman.h>
#endif

#define APP_ENABLE_SPI 1
#include "../../firmware/application/Core/Src/app_spi.c"

IWDG_HandleTypeDef hiwdg;
test_matrix_sample_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
static matrix_diagnostics_t test_diagnostics;
static uint32_t test_tick;
static unsigned tick_calls, diagnostic_calls, watchdog_calls;
static unsigned response_calls, reset_calls, barrier_calls;
static uint8_t test_request[536], test_response[536];
static uint16_t request_length, response_length, master_read_length;
static bool link_response_pending, reject_send_without_pending;
static SPI_LinkCallbacks link_callbacks;
static volatile uint32_t *const marker = (volatile uint32_t *)(uintptr_t)0x20001FF0u;
static void *mapped_sram;

uint32_t HAL_GetTick(void) { tick_calls++; return test_tick; }
HAL_StatusTypeDef HAL_IWDG_Refresh(IWDG_HandleTypeDef *handle)
{ assert(handle == &hiwdg); watchdog_calls++; return HAL_OK; }
void MATRIX_GetDiagnostics(matrix_diagnostics_t *out)
{ diagnostic_calls++; *out = test_diagnostics; }
void test_memory_barrier(void)
{ assert(*marker == SPI_IAP_MAGIC); barrier_calls++; }
void NVIC_SystemReset(void)
{
    assert(master_read_length >= response_length);
    assert(*marker == SPI_IAP_MAGIC && barrier_calls == 1u);
    reset_calls++;
}
void SPI_LinkInit(const SPI_LinkCallbacks *callbacks)
{ assert(callbacks != NULL); link_callbacks = *callbacks; link_response_pending = false; }
bool SPI_LinkResponsePending(void) { return link_response_pending; }
uint16_t SPI_LinkReceive(uint8_t *buffer, uint16_t capacity)
{
    uint16_t length = request_length;
    assert(length <= capacity);
    memcpy(buffer, test_request, length);
    request_length = 0u;
    return length;
}
bool SPI_LinkQueue(const uint8_t *buffer, uint16_t length)
{
    assert(length <= sizeof(test_response));
    memcpy(test_response, buffer, length);
    response_length = length;
    response_calls++;
    /* Queue succeeds before a separate CS read, without waiting for the host. */
    link_response_pending = !reject_send_without_pending && master_read_length < length;
    return !reject_send_without_pending;
}
bool SPI_LinkSend(const uint8_t *buffer, uint16_t length, uint32_t timeout_ms)
{
    (void)buffer; (void)length; (void)timeout_ms;
    assert(!"Application must not block sampling while waiting for an SPI read");
    return false;
}

/* An independent table-form reference plus its standard known-answer vector. */
static uint32_t reference_crc(const uint8_t *bytes, size_t length)
{
    uint32_t table[256], crc = 0xFFFFFFFFu;
    unsigned entry, bit;
    for (entry = 0; entry < 256u; entry++) {
        uint32_t value = entry;
        for (bit = 0; bit < 8u; bit++)
            value = value & 1u ? (value >> 1) ^ 0xEDB88320u : value >> 1;
        table[entry] = value;
    }
    while (length-- != 0u) crc = table[(crc ^ *bytes++) & 0xFFu] ^ (crc >> 8);
    return crc ^ 0xFFFFFFFFu;
}
static void encode16(uint8_t *bytes, uint16_t value)
{ bytes[0] = (uint8_t)value; bytes[1] = (uint8_t)(value >> 8); }
static void encode32(uint8_t *bytes, uint32_t value)
{
    unsigned i;
    for (i = 0; i < 4u; i++) bytes[i] = (uint8_t)(value >> (8u * i));
}
static uint32_t decode32(const uint8_t *bytes)
{
    return (uint32_t)bytes[0] | ((uint32_t)bytes[1] << 8) |
           ((uint32_t)bytes[2] << 16) | ((uint32_t)bytes[3] << 24);
}
static float decode_float(const uint8_t *bytes)
{ float value; memcpy(&value, bytes, sizeof(value)); return value; }
static void resign_request(void)
{ encode32(test_request + request_length - 4u, reference_crc(test_request, request_length - 4u)); }
static void queue_request(uint8_t command, uint16_t sequence, const void *payload, uint16_t length)
{
    assert(length <= 520u);
    memset(test_request, 0, sizeof(test_request));
    memcpy(test_request, "TAI3", 4u);
    test_request[4] = command;
    encode16(test_request + 6u, sequence);
    encode16(test_request + 8u, length);
    if (length != 0u) memcpy(test_request + 12u, payload, length);
    request_length = (uint16_t)(16u + length);
    resign_request();
}
static void check_response(uint8_t command, uint16_t sequence, uint8_t status, uint16_t length)
{
    assert(response_length == 16u + length);
    assert(memcmp(test_response, "TAI3", 4u) == 0);
    assert(test_response[4] == (uint8_t)(command | 0x80u));
    assert(test_response[5] == status);
    assert(test_response[6] == (uint8_t)sequence && test_response[7] == (uint8_t)(sequence >> 8));
    assert(test_response[8] == (uint8_t)length && test_response[9] == (uint8_t)(length >> 8));
    assert(test_response[10] == 0u && test_response[11] == 0u);
    assert(decode32(test_response + 12u + length) == reference_crc(test_response, 12u + length));
}
static void reset_fixture(void)
{
    memset(g_data, 0, sizeof(g_data));
    memset(&test_diagnostics, 0, sizeof(test_diagnostics));
    memset(test_request, 0, sizeof(test_request));
    memset(test_response, 0, sizeof(test_response));
    test_tick = 0u;
    tick_calls = diagnostic_calls = watchdog_calls = 0u;
    response_calls = reset_calls = barrier_calls = 0u;
    request_length = response_length = 0u;
    master_read_length = 536u;
    reject_send_without_pending = false;
    s_frame_counter = 0u;
    *marker = 0xA55AA55Au;
    APP_SPI_Init();
}
static void fill_measurement(void)
{
    unsigned col, row;
    for (col = 0; col < 4u; col++) for (row = 0; row < 2u; row++) {
        float channel = (float)(col * 2u + row);
        g_data[col][row].x = 100.25f + channel;
        g_data[col][row].y = -200.5f - channel;
        g_data[col][row].z = 300.75f + channel;
    }
}
static void initialise_and_check_wire_layout(void)
{
    reset_fixture();
    assert(sizeof(spi_snapshot_t) == 164u);
    assert(offsetof(spi_snapshot_t, version) == 0u && offsetof(spi_snapshot_t, status) == 2u);
    assert(offsetof(spi_snapshot_t, device_ms) == 4u && offsetof(spi_snapshot_t, frame_counter) == 8u);
    assert(offsetof(spi_snapshot_t, error_count) == 12u && offsetof(spi_snapshot_t, baseline_epoch) == 16u);
    assert(offsetof(spi_snapshot_t, valid_mask) == 20u && offsetof(spi_snapshot_t, firmware_id) == 24u);
    assert(offsetof(spi_snapshot_t, raw) == 56u && offsetof(spi_snapshot_t, force) == 152u);
    assert(s_snapshot.version == 1u && s_snapshot.valid_mask == 0u);
    assert(strcmp(s_snapshot.firmware_id, "h3_spi_20261014_v1") == 0);
    assert(link_callbacks.tick_ms == HAL_GetTick && link_callbacks.idle == NULL && link_callbacks.watchdog != NULL);
    link_callbacks.watchdog();
    assert(watchdog_calls == 1u);
    queue_request(0x20u, 0xBEEFu, NULL, 0u);
    APP_SPI_Poll();
    check_response(0x20u, 0xBEEFu, 0u, 164u);
    assert(test_response[12] == 1u && test_response[13] == 0u);
}
static void publish_snapshot_is_coherent_and_immutable(void)
{
    MagForce_Vec3_t force = {1.25f, -2.5f, 3.75f};
    unsigned channel, axis;
    reset_fixture(); fill_measurement();
    test_tick = 0x12345678u;
    test_diagnostics.valid_mask = 0xFFu;
    test_diagnostics.error_count = 0x04030201u;
    test_diagnostics.baseline_epoch = 7u;
    APP_SPI_Publish(true, &force);
    assert(tick_calls == 1u && diagnostic_calls == 1u);
    /* Subsequent source changes must not alter the published payload. */
    memset(g_data, 0, sizeof(g_data)); memset(&force, 0, sizeof(force));
    test_tick++; test_diagnostics.error_count++;
    queue_request(0x20u, 0x1234u, NULL, 0u); APP_SPI_Poll();
    check_response(0x20u, 0x1234u, 0u, 164u);
    assert(test_response[14] == 3u && test_response[15] == 0u);
    assert(decode32(test_response + 16u) == 0x12345678u);
    assert(decode32(test_response + 20u) == 1u);
    assert(decode32(test_response + 24u) == 0x04030201u);
    assert(decode32(test_response + 28u) == 7u && test_response[32u] == 0xFFu);
    assert(test_response[33u] == 0u && test_response[34u] == 0u && test_response[35u] == 0u);
    assert(strcmp((char *)test_response + 36u, "h3_spi_20261014_v1") == 0);
    for (channel = 0u; channel < 8u; channel++) for (axis = 0u; axis < 3u; axis++) {
        float expected = axis == 0u ? 100.25f + (float)channel :
                         axis == 1u ? -200.5f - (float)channel : 300.75f + (float)channel;
        assert(decode_float(test_response + 68u + 4u * (channel * 3u + axis)) == expected);
    }
    assert(decode_float(test_response + 164u) == 1.25f);
    assert(decode_float(test_response + 168u) == -2.5f);
    assert(decode_float(test_response + 172u) == 3.75f);
    assert(tick_calls == 1u && diagnostic_calls == 1u);
}
static void failed_capture_never_relabels_old_data(void)
{
    MagForce_Vec3_t force = {9.0f, 8.0f, 7.0f};
    unsigned i;
    reset_fixture(); fill_measurement();
    test_diagnostics.valid_mask = 0xFFu; test_diagnostics.baseline_epoch = 4u;
    APP_SPI_Publish(true, &force); assert(s_snapshot.frame_counter == 1u);
    test_tick = 200u; test_diagnostics.valid_mask = 0xFEu; test_diagnostics.error_count = 1u;
    APP_SPI_Publish(true, NULL); /* A failed acquisition must not dereference stale force. */
    assert(s_snapshot.status == 18u && s_snapshot.valid_mask == 0u);
    assert(s_snapshot.device_ms == 200u && s_snapshot.error_count == 1u && s_snapshot.frame_counter == 1u);
    for (i = 0u; i < 24u; i++) assert(s_snapshot.raw[i] == 0.0f);
    for (i = 0u; i < 3u; i++) assert(s_snapshot.force[i] == 0.0f);
    test_diagnostics.valid_mask = 0xFFu; APP_SPI_Publish(false, NULL);
    assert(s_snapshot.status == 18u && s_snapshot.valid_mask == 0u && s_snapshot.frame_counter == 1u);
    test_diagnostics.baseline_epoch = 0u; APP_SPI_Publish(true, NULL);
    assert(s_snapshot.status == 16u && s_snapshot.valid_mask == 0u && s_snapshot.frame_counter == 1u);
    test_diagnostics.baseline_epoch = 5u; APP_SPI_Publish(true, &force);
    assert(s_snapshot.status == 3u && s_snapshot.valid_mask == 0xFFu && s_snapshot.frame_counter == 2u);
}
static void malformed_get_request_is_rejected(void)
{
    unsigned variant;
    reset_fixture();
    for (variant = 0u; variant < 5u; variant++) {
        queue_request(0x20u, 0xFFE0u + (uint16_t)variant, NULL, 0u);
        if (variant == 0u) test_request[12] ^= 1u; /* Bad CRC. */
        if (variant == 1u) encode16(test_request + 8u, 1u); /* Missing payload. */
        if (variant == 2u) { test_request[10] = 1u; resign_request(); }
        if (variant == 3u) { test_request[5] = 1u; resign_request(); }
        if (variant == 4u) encode16(test_request + 8u, 521u); /* Oversized declaration. */
        APP_SPI_Poll(); check_response(0x20u, 0xFFE0u + (uint16_t)variant, 1u, 0u);
    }
    assert(reset_calls == 0u && *marker == 0xA55AA55Au);
}
static void incomplete_and_foreign_frames_are_ignored(void)
{
    reset_fixture();
    queue_request(0x21u, 7u, "H3G031G8", 8u); request_length = 15u; APP_SPI_Poll();
    assert(response_calls == 0u && reset_calls == 0u);
    queue_request(0x21u, 7u, "H3G031G8", 8u); test_request[0] = 'X'; APP_SPI_Poll();
    assert(response_calls == 0u && reset_calls == 0u && *marker == 0xA55AA55Au);
}
static void wrong_model_or_corrupt_upgrade_cannot_reset(void)
{
    reset_fixture();
    queue_request(0x21u, 0x0102u, "H4G031G8", 8u); APP_SPI_Poll();
    check_response(0x21u, 0x0102u, 3u, 0u);
    queue_request(0x21u, 0x0304u, "H3G031G", 7u); APP_SPI_Poll();
    check_response(0x21u, 0x0304u, 3u, 0u);
    queue_request(0x21u, 0x0506u, "H3G031G8", 8u); test_request[20] ^= 1u; APP_SPI_Poll();
    check_response(0x21u, 0x0506u, 1u, 0u);
    assert(reset_calls == 0u && barrier_calls == 0u && *marker == 0xA55AA55Au);
}
static void reset_requires_complete_upgrade_ack(void)
{
    reset_fixture(); master_read_length = 23u;
    queue_request(0x21u, 0xCAFEu, "H3G031G8", 8u); APP_SPI_Poll();
    check_response(0x21u, 0xCAFEu, 0u, 8u);
    assert(memcmp(test_response + 12u, "H3G031G8", 8u) == 0);
    assert(reset_calls == 0u && barrier_calls == 0u && *marker == 0xA55AA55Au);
    assert(link_response_pending);
    APP_SPI_Poll(); /* An incomplete ACK remains pending across later main-loop polls. */
    assert(reset_calls == 0u && response_calls == 1u && *marker == 0xA55AA55Au);
    master_read_length = 24u;
    link_response_pending = false; /* A later physical CS read completed the queued ACK. */
    APP_SPI_Poll();
    assert(reset_calls == 1u && barrier_calls == 1u && *marker == SPI_IAP_MAGIC);
    reset_fixture(); master_read_length = 24u;
    queue_request(0x21u, 0xBEEFu, "H3G031G8", 8u); APP_SPI_Poll();
    check_response(0x21u, 0xBEEFu, 0u, 8u);
    assert(reset_calls == 0u); /* Poll checks ACK consumption after queuing. */
    APP_SPI_Poll();
    assert(reset_calls == 1u && barrier_calls == 1u && *marker == SPI_IAP_MAGIC);
    reset_fixture(); reject_send_without_pending = true;
    queue_request(0x21u, 0x5555u, "H3G031G8", 8u); APP_SPI_Poll();
    assert(!link_response_pending && reset_calls == 0u);
    APP_SPI_Poll();
    assert(reset_calls == 0u && *marker == 0xA55AA55Au);
}
static void unsupported_command_and_get_payload_are_rejected(void)
{
    const uint8_t byte = 0xAAu;
    reset_fixture();
    queue_request(0x20u, 0x1111u, &byte, 1u); APP_SPI_Poll();
    check_response(0x20u, 0x1111u, 3u, 0u);
    queue_request(0x25u, 0x2222u, NULL, 0u); APP_SPI_Poll();
    check_response(0x25u, 0x2222u, 3u, 0u);
    assert(reset_calls == 0u && *marker == 0xA55AA55Au);
}
static void paused_master_does_not_pause_fresh_acquisition(void)
{
    MagForce_Vec3_t force = {1.0f, 2.0f, 3.0f};
    uint8_t queued_response[536];
    reset_fixture(); fill_measurement();
    test_diagnostics.valid_mask = 0xFFu; test_diagnostics.baseline_epoch = 1u;
    APP_SPI_Publish(true, &force);
    master_read_length = 0u;
    queue_request(0x20u, 42u, NULL, 0u); APP_SPI_Poll();
    assert(link_response_pending && response_calls == 1u);
    check_response(0x20u, 42u, 0u, 164u);
    memcpy(queued_response, test_response, response_length);
    test_tick = 500u;
    g_data[0][0].x += 10.0f;
    APP_SPI_Publish(true, &force);
    APP_SPI_Poll();
    assert(s_snapshot.frame_counter == 2u && s_snapshot.device_ms == 500u);
    assert(link_response_pending && response_calls == 1u);
    assert(memcmp(queued_response, test_response, response_length) == 0);
    assert(reset_calls == 0u);
}
int main(void)
{
    assert(reference_crc((const uint8_t *)"123456789", 9u) == 0xCBF43926u);
#ifdef _WIN32
    mapped_sram = VirtualAlloc((void *)(uintptr_t)0x20000000u, 65536u, MEM_RESERVE | MEM_COMMIT, PAGE_READWRITE);
    assert(mapped_sram == (void *)(uintptr_t)0x20000000u);
#else
    mapped_sram = mmap((void *)(uintptr_t)0x20000000u, 65536u, PROT_READ | PROT_WRITE,
                       MAP_PRIVATE | MAP_ANONYMOUS | MAP_FIXED_NOREPLACE, -1, 0);
    assert(mapped_sram == (void *)(uintptr_t)0x20000000u);
#endif
    initialise_and_check_wire_layout(); publish_snapshot_is_coherent_and_immutable();
    failed_capture_never_relabels_old_data(); malformed_get_request_is_rejected();
    incomplete_and_foreign_frames_are_ignored(); wrong_model_or_corrupt_upgrade_cannot_reset();
    reset_requires_complete_upgrade_ack(); unsupported_command_and_get_payload_are_rejected();
    paused_master_does_not_pause_fresh_acquisition();
#ifdef _WIN32
    assert(VirtualFree(mapped_sram, 0u, MEM_RELEASE));
#else
    assert(munmap(mapped_sram, 65536u) == 0);
#endif
    puts("PASS: 9 production SPI application snapshot/protocol/reset/nonblocking cases");
    return 0;
}

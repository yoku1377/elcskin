#include "app_spi.h"
#if APP_ENABLE_SPI
#include <stdint.h>
#include <string.h>
#include "main.h"
#include "matrix.h"
#include "iwdg.h"
#include "spi_link.h"

#define SPI_FRAME_MAX 536u
#define SPI_IAP_MAGIC 0x33535049u
#define SPI_FW_ID APP_SPI_FIRMWARE_ID

#if APP_SPI_DIAGNOSTICS
#if !defined(SPI_LINK_DIAGNOSTICS) || !SPI_LINK_DIAGNOSTICS
#error SPI diagnostics application requires SPI_LINK_DIAGNOSTICS
#endif
#include <stdio.h>
static uint32_t s_diag_last_ms, s_diag_commands, s_diag_bad_frames;
static void print_link_diagnostics(void)
{
    SPI_LinkDiagnostics d;
    uint32_t now = HAL_GetTick();
    if ((uint32_t)(now - s_diag_last_ms) < 1000u) return;
    s_diag_last_ms = now;
    SPI_LinkGetDiagnostics(&d);
    printf("SPI_DIAG,%lu,fall=%lu,rise=%lu,rx=%lu,data=%lu,head=%08lX,req=%lu,"
           "cmd=%lu,bad=%lu,stage=%lu,commit=%lu,ack=%lu,fault=%lu,resync=%lu,"
           "nss=%lu,cr1=%08lX,cr2=%08lX,sr=%08lX,rxleft=%lu,txleft=%lu,flags=%02lX\r\n",
           (unsigned long)now, (unsigned long)d.falling, (unsigned long)d.rising,
           (unsigned long)d.last_length, (unsigned long)d.data_length,
           (unsigned long)d.data_head, (unsigned long)d.requests,
           (unsigned long)s_diag_commands, (unsigned long)s_diag_bad_frames,
           (unsigned long)d.staged, (unsigned long)d.committed,
           (unsigned long)d.acknowledged, (unsigned long)d.faults,
           (unsigned long)d.resyncs, (unsigned long)((d.gpio_idr >> 8) & 1u),
           (unsigned long)d.spi_cr1, (unsigned long)d.spi_cr2,
           (unsigned long)d.spi_sr, (unsigned long)d.rx_left,
           (unsigned long)d.tx_left, (unsigned long)d.flags);
#if defined(SPI_LINK_RX_IRQ) && SPI_LINK_RX_IRQ
    printf("SPI_RXIRQ_STATE,%lu,rxmode=%lu,amoder=%08lX,aafr0=%08lX,aafr1=%08lX,"
           "bmoder=%08lX,bafr0=%08lX,bafr1=%08lX,rxccr=%08lX,rxpar=%08lX,"
           "rxmar=%08lX,rxmux=%08lX,txccr=%08lX,last_head=%08lX,"
           "sticky_head=%08lX,sticky_len=%lu,irq=%lu,overflow=%lu,"
           "rtc_cr=%08lX,bdcr=%08lX,tamp_cr1=%08lX\r\n",
           (unsigned long)now, (unsigned long)d.rx_mode,
           (unsigned long)d.gpio_moder, (unsigned long)d.gpio_afr0,
           (unsigned long)d.gpio_afr1, (unsigned long)d.gpio_b_moder,
           (unsigned long)d.gpio_b_afr0, (unsigned long)d.gpio_b_afr1,
           (unsigned long)d.rx_dma_ccr, (unsigned long)d.rx_dma_cpar,
           (unsigned long)d.rx_dma_cmar, (unsigned long)d.rx_dmamux,
           (unsigned long)d.tx_dma_ccr, (unsigned long)d.last_rx_head,
           (unsigned long)d.data_head, (unsigned long)d.data_length,
           (unsigned long)d.rx_irq_calls, (unsigned long)d.rx_overflow,
           (unsigned long)d.rtc_cr, (unsigned long)d.rcc_bdcr,
           (unsigned long)d.tamp_cr1);
#endif
}
#endif

typedef struct __attribute__((packed)) {
    uint16_t version;
    uint16_t status;
    uint32_t device_ms;
    uint32_t frame_counter;
    uint32_t error_count;
    uint32_t baseline_epoch;
    uint8_t valid_mask;
    uint8_t reserved[3];
    char firmware_id[32];
    float raw[24];
    float force[3];
} spi_snapshot_t;

_Static_assert(sizeof(spi_snapshot_t) == 164u, "SPI snapshot wire layout changed");
static spi_snapshot_t s_snapshot;
static uint8_t s_request[SPI_FRAME_MAX];
static uint32_t s_frame_counter;
static bool s_enter_waiting;

static uint32_t crc32(const uint8_t *data, uint16_t length)
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

static uint16_t get16(const uint8_t *p)
{
    return (uint16_t)(p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t get32(const uint8_t *p)
{
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static void put32(uint8_t *p, uint32_t value)
{
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16); p[3] = (uint8_t)(value >> 24);
}

static bool respond(uint8_t command, uint16_t sequence, uint8_t status,
                    const void *payload, uint16_t length)
{
    uint8_t response[16u + sizeof(spi_snapshot_t)];
    memcpy(response, "TAI3", 4u);
    response[4] = command | 0x80u; response[5] = status;
    response[6] = (uint8_t)sequence; response[7] = (uint8_t)(sequence >> 8);
    response[8] = (uint8_t)length; response[9] = (uint8_t)(length >> 8);
    response[10] = 0u; response[11] = 0u;
    if (length != 0u) memcpy(response + 12u, payload, length);
    put32(response + 12u + length, crc32(response, 12u + length));
    /* Copy the complete response, then let CS/DMA finish it asynchronously.
     * A master that pauses between write and read must not pause sampling,
     * UART diagnostics or the UART IAP command parser with it. */
    return SPI_LinkQueue(response, 16u + length);
}

static void refresh_watchdog(void)
{
    (void)HAL_IWDG_Refresh(&hiwdg);
}

void APP_SPI_Init(void)
{
    const SPI_LinkCallbacks callbacks = {HAL_GetTick, 0, refresh_watchdog};
    memset(&s_snapshot, 0, sizeof(s_snapshot));
    s_enter_waiting = false;
    s_frame_counter = 0u;
    s_snapshot.version = 1u;
    s_snapshot.status = 16u; /* Not a valid sample until acquisition succeeds. */
    memcpy(s_snapshot.firmware_id, SPI_FW_ID, sizeof(SPI_FW_ID));
    SPI_LinkInit(&callbacks);
#if APP_SPI_DIAGNOSTICS
    {
        SPI_LinkDiagnostics d;
        s_diag_last_ms = HAL_GetTick();
        s_diag_commands = s_diag_bad_frames = 0u;
        SPI_LinkGetDiagnostics(&d);
        printf("SPI_READY,%s,PA0=SCK,PA3=MISO,PA4=MOSI,PA8=NSS,"
               "moder=%08lX,afr0=%08lX,afr1=%08lX,cr1=%08lX,cr2=%08lX\r\n",
               SPI_FW_ID, (unsigned long)d.gpio_moder, (unsigned long)d.gpio_afr0,
               (unsigned long)d.gpio_afr1, (unsigned long)d.spi_cr1,
               (unsigned long)d.spi_cr2);
    }
#endif
}

void APP_SPI_Publish(bool valid, const MagForce_Vec3_t *force)
{
    matrix_diagnostics_t diagnostics;
    uint8_t col, row, index = 0u;
    MATRIX_GetDiagnostics(&diagnostics);
    s_snapshot.device_ms = HAL_GetTick();
    s_snapshot.error_count = diagnostics.error_count;
    s_snapshot.baseline_epoch = diagnostics.baseline_epoch;
    s_snapshot.valid_mask = diagnostics.valid_mask;
    valid = valid && diagnostics.valid_mask == MATRIX_VALID_MASK_ALL &&
            diagnostics.baseline_epoch != 0u;
    s_snapshot.status = (diagnostics.baseline_epoch != 0u ? 2u : 0u) |
                        (valid ? 1u : 16u);
    if (!valid) {
        /* Publish diagnostics, never relabel an older RAW/force as fresh. */
        memset(s_snapshot.raw, 0, sizeof(s_snapshot.raw));
        memset(s_snapshot.force, 0, sizeof(s_snapshot.force));
        s_snapshot.valid_mask = 0u;
        return;
    }
    s_snapshot.frame_counter = ++s_frame_counter;
    for (col = 0u; col < MATRIX_SCL_COL_NUM; ++col) {
        for (row = 0u; row < MATRIX_SDA_ROW_NUM; ++row) {
            s_snapshot.raw[index++] = g_data[col][row].x;
            s_snapshot.raw[index++] = g_data[col][row].y;
            s_snapshot.raw[index++] = g_data[col][row].z;
        }
    }
    s_snapshot.force[0] = force->x;
    s_snapshot.force[1] = force->y;
    s_snapshot.force[2] = force->z;
}

void APP_SPI_Poll(void)
{
    static const uint8_t model[] = "H3G031G8";
    uint16_t received;
    uint16_t length, sequence;
    uint8_t command;
#if APP_SPI_DIAGNOSTICS
    print_link_diagnostics();
#endif
    if (s_enter_waiting) {
        if (!SPI_LinkResponsePending()) {
            s_enter_waiting = false;
            *(volatile uint32_t *)0x20001FF0u = SPI_IAP_MAGIC;
            __DSB();
            NVIC_SystemReset();
        }
        return;
    }
    received = SPI_LinkReceive(s_request, sizeof(s_request));
    if (received < 16u || memcmp(s_request, "TAI3", 4u) != 0) return;
    command = s_request[4]; sequence = get16(s_request + 6u);
    length = get16(s_request + 8u);
    if (length > 520u || received != 16u + length ||
        get16(s_request + 10u) != 0u || s_request[5] != 0u ||
        crc32(s_request, 12u + length) != get32(s_request + 12u + length)) {
#if APP_SPI_DIAGNOSTICS
        s_diag_bad_frames++;
#endif
        (void)respond(command, sequence, 1u, 0, 0u);
        return;
    }
#if APP_SPI_DIAGNOSTICS
    s_diag_commands++;
#endif
    if (command == 0x20u && length == 0u) {
        (void)respond(command, sequence, 0u, &s_snapshot, sizeof(s_snapshot));
    } else if (command == 0x21u && length == sizeof(model) - 1u &&
               memcmp(s_request + 12u, model, sizeof(model) - 1u) == 0) {
        /* Defer the reset until the master has clocked the complete ACK. */
        if (respond(command, sequence, 0u, model, sizeof(model) - 1u)) {
            s_enter_waiting = true;
        }
    } else {
        (void)respond(command, sequence, 3u, 0, 0u);
    }
}
#endif

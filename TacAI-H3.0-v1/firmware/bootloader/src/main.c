#include <stdint.h>
#include "stm32g031xx.h"
#include "system_stm32g0xx.h"
#include "iap_layout.h"
#ifdef TACAI_ENABLE_SPI
#include "spi_link.h"
#endif

#define FRAME_MAGIC 0x33494154u /* bytes: TAI3 */
#define FRAME_MAX_PAYLOAD 520u
#define FRAME_HEADER_SIZE 12u
#define PROTOCOL_VERSION 1u

#define CMD_HELLO 0x01u
#define CMD_BEGIN 0x02u
#define CMD_DATA  0x03u
#define CMD_END   0x04u
#define CMD_BOOT  0x05u
#define CMD_ABORT 0x06u

#define STATUS_OK             0u
#define STATUS_BAD_FRAME      1u
#define STATUS_BAD_MANIFEST   2u
#define STATUS_NOT_READY      3u
#define STATUS_BAD_SEQUENCE   4u
#define STATUS_FLASH_ERROR    5u
#define STATUS_CRC_MISMATCH   6u
#define STATUS_INVALID_VECTOR 7u

#define META_MAGIC      0x3341544Du /* MTA3 */
#define META_DOWNLOADING 0x444F574Eu /* DOWN */
#define META_VALID       0x56414C44u /* VALD */
#define FLASH_ERRORS (FLASH_SR_OPERR | FLASH_SR_PROGERR | FLASH_SR_WRPERR | \
                      FLASH_SR_PGAERR | FLASH_SR_SIZERR | FLASH_SR_PGSERR)

typedef struct __attribute__((packed)) {
    uint32_t magic;
    uint8_t command;
    uint8_t flags;
    uint16_t sequence;
    uint16_t length;
    uint16_t reserved;
} frame_header_t;

typedef struct __attribute__((packed)) {
    char model[TACAI_MODEL_ID_SIZE];
    uint32_t app_start;
    uint32_t image_length;
    uint32_t image_crc32;
    uint32_t version;
    uint32_t confirm_token;
} image_manifest_t;

typedef struct __attribute__((packed, aligned(8))) {
    uint32_t magic;
    uint32_t state;
    uint32_t app_start;
    uint32_t image_length;
    uint32_t image_crc32;
    uint32_t version;
    char model[TACAI_MODEL_ID_SIZE];
    uint32_t record_crc32;
} image_metadata_t;

typedef struct {
    frame_header_t header;
    uint8_t payload[FRAME_MAX_PAYLOAD];
} frame_t;

static volatile uint32_t g_ms;
static uint32_t g_expected_offset;
static image_manifest_t g_manifest;
static uint8_t g_download_active;

#ifdef TACAI_ENABLE_SPI
typedef enum { TRANSPORT_NONE, TRANSPORT_UART, TRANSPORT_SPI } transport_t;
static transport_t g_transport;
static transport_t g_reply_transport;
static uint8_t g_spi_wire[SPI_LINK_FRAME_MAX];
static uint8_t g_boot_waiting;
#endif

static void boot_watchdog(void)
{
    /* A reload is harmless when software IWDG is disabled; it also supports
     * boards whose option bytes start the watchdog in hardware mode. */
    IWDG->KR = 0xAAAAu;
}

#ifdef TACAI_ENABLE_SPI
static uint32_t boot_tick(void) { return g_ms; }
void EXTI4_15_IRQHandler(void) { SPI_LinkCsIRQ(); }
#endif

static uint32_t crc32_update(uint32_t crc, const uint8_t *data, uint32_t length)
{
    uint32_t i;
    crc = ~crc;
    while (length-- != 0u) {
        crc ^= *data++;
        for (i = 0u; i < 8u; ++i) {
            crc = (crc >> 1) ^ (0xEDB88320u & (0u - (crc & 1u)));
        }
    }
    return ~crc;
}

static uint8_t bytes_equal(const uint8_t *a, const uint8_t *b, uint32_t length)
{
    while (length-- != 0u) {
        if (*a++ != *b++) return 0u;
    }
    return 1u;
}

void SysTick_Handler(void)
{
    ++g_ms;
}

static void uart_init(void)
{
    RCC->IOPENR |= RCC_IOPENR_GPIOBEN;
    RCC->APBENR2 |= RCC_APBENR2_USART1EN;
    (void)RCC->IOPENR;
    GPIOB->MODER = (GPIOB->MODER & ~((3u << (6u * 2u)) | (3u << (7u * 2u)))) |
                   (2u << (6u * 2u)) | (2u << (7u * 2u));
    GPIOB->AFR[0] &= ~((0xFu << (6u * 4u)) | (0xFu << (7u * 4u)));
    GPIOB->OSPEEDR |= (2u << (6u * 2u)) | (2u << (7u * 2u));
    USART1->BRR = 16000000u / 115200u;
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void uart_write_byte(uint8_t value)
{
    while ((USART1->ISR & USART_ISR_TXE_TXFNF) == 0u) { boot_watchdog(); }
    USART1->TDR = value;
}

static void uart_write(const uint8_t *data, uint32_t length)
{
    while (length-- != 0u) uart_write_byte(*data++);
}

static int uart_read_byte(uint8_t *value, uint32_t timeout_ms)
{
#ifdef BOOTLOADER_UART_BYTE_TEST
    /* Host fault-injection replaces only the UART byte/tick boundary. */
    return BOOTLOADER_UART_BYTE_TEST(value, timeout_ms);
#else
    const uint32_t start = g_ms;
    while ((USART1->ISR & USART_ISR_RXNE_RXFNE) == 0u) {
        boot_watchdog();
        if (timeout_ms != 0xFFFFFFFFu && (uint32_t)(g_ms - start) >= timeout_ms) return 0;
    }
    *value = (uint8_t)USART1->RDR;
    return 1;
#endif
}

static int uart_read_exact(uint8_t *data, uint32_t length, uint32_t timeout_ms)
{
    while (length-- != 0u) {
        if (!uart_read_byte(data++, timeout_ms)) return 0;
    }
    return 1;
}

static int receive_frame(frame_t *frame, uint32_t first_byte_timeout_ms)
{
    uint8_t magic_bytes[4] = {'T', 'A', 'I', '3'};
    uint8_t value;
    uint32_t matched = 0u;
    uint32_t wire_crc;
    uint32_t calc_crc;
    const uint32_t start = g_ms;

    while (matched < 4u) {
        /* The short dual-interface poll budget only applies before a magic
         * prefix arrives. Once T/TA/TAI is in flight, allow the same bounded
         * inter-byte wait as the remaining header. Otherwise a prefix near
         * the 2 ms poll boundary is discarded without an ACK or a NACK. */
        uint32_t remaining = matched != 0u ? 500u : first_byte_timeout_ms;
        if (matched == 0u && first_byte_timeout_ms != 0xFFFFFFFFu) {
            const uint32_t elapsed = (uint32_t)(g_ms - start);
            if (elapsed >= first_byte_timeout_ms) return 0;
            remaining -= elapsed;
        }
        if (!uart_read_byte(&value, remaining)) return 0;
        matched = (value == magic_bytes[matched]) ? matched + 1u : (value == magic_bytes[0] ? 1u : 0u);
    }

    frame->header.magic = FRAME_MAGIC;
    if (!uart_read_exact(((uint8_t *)&frame->header) + 4u, FRAME_HEADER_SIZE - 4u, 500u)) return -1;
    if (frame->header.length > FRAME_MAX_PAYLOAD || frame->header.reserved != 0u) return -1;
    if (!uart_read_exact(frame->payload, frame->header.length, 1000u)) return -1;
    if (!uart_read_exact((uint8_t *)&wire_crc, 4u, 500u)) return -1;
    calc_crc = crc32_update(0u, (const uint8_t *)&frame->header, FRAME_HEADER_SIZE);
    calc_crc = crc32_update(calc_crc, frame->payload, frame->header.length);
    return calc_crc == wire_crc ? 1 : -1;
}

static uint8_t send_response(uint8_t command, uint16_t sequence, uint8_t status,
                          const uint8_t *payload, uint16_t payload_length)
{
    frame_header_t header;
    uint32_t crc;
    header.magic = FRAME_MAGIC;
    header.command = command | 0x80u;
    header.flags = status;
    header.sequence = sequence;
    header.length = payload_length;
    header.reserved = 0u;
    crc = crc32_update(0u, (const uint8_t *)&header, FRAME_HEADER_SIZE);
    crc = crc32_update(crc, payload, payload_length);
#ifdef TACAI_ENABLE_SPI
    if (g_reply_transport == TRANSPORT_SPI) {
        uint32_t i;
        const uint32_t length = FRAME_HEADER_SIZE + payload_length;
        for (i = 0u; i < FRAME_HEADER_SIZE; ++i) g_spi_wire[i] = ((uint8_t *)&header)[i];
        for (i = 0u; i < payload_length; ++i) g_spi_wire[FRAME_HEADER_SIZE + i] = payload[i];
        g_spi_wire[length] = (uint8_t)crc;
        g_spi_wire[length + 1u] = (uint8_t)(crc >> 8);
        g_spi_wire[length + 2u] = (uint8_t)(crc >> 16);
        g_spi_wire[length + 3u] = (uint8_t)(crc >> 24);
        /* A non-owner may receive NOT_READY without delaying the active
         * UART session while an unrelated SPI host is not polling. */
        return SPI_LinkSend(g_spi_wire, (uint16_t)(length + 4u),
                            g_transport == TRANSPORT_SPI ? 5000u : 0u) ? 1u : 0u;
    }
#endif
    uart_write((const uint8_t *)&header, FRAME_HEADER_SIZE);
    uart_write(payload, payload_length);
    uart_write((const uint8_t *)&crc, 4u);
    return 1u;
}

static uint8_t flash_wait(void)
{
    while ((FLASH->SR & FLASH_SR_BSY1) != 0u) { boot_watchdog(); }
    if ((FLASH->SR & FLASH_ERRORS) != 0u) {
        FLASH->SR = FLASH_ERRORS;
        return 0u;
    }
    if ((FLASH->SR & FLASH_SR_EOP) != 0u) FLASH->SR = FLASH_SR_EOP;
    return 1u;
}

static void flash_unlock(void)
{
    if ((FLASH->CR & FLASH_CR_LOCK) != 0u) {
        FLASH->KEYR = 0x45670123u;
        FLASH->KEYR = 0xCDEF89ABu;
    }
}

static void flash_lock(void)
{
    FLASH->CR |= FLASH_CR_LOCK;
}

static uint8_t flash_erase_page(uint32_t address)
{
    const uint32_t page = (address - TACAI_FLASH_BASE) / TACAI_FLASH_PAGE_SIZE;
    if (!flash_wait()) return 0u;
    FLASH->CR = (FLASH->CR & ~FLASH_CR_PNB) | FLASH_CR_PER | (page << FLASH_CR_PNB_Pos);
    FLASH->CR |= FLASH_CR_STRT;
    if (!flash_wait()) return 0u;
    FLASH->CR &= ~FLASH_CR_PER;
    return 1u;
}

static uint8_t flash_program_doubleword(uint32_t address, const uint8_t *data)
{
    uint32_t lo = (uint32_t)data[0] | ((uint32_t)data[1] << 8) |
                  ((uint32_t)data[2] << 16) | ((uint32_t)data[3] << 24);
    uint32_t hi = (uint32_t)data[4] | ((uint32_t)data[5] << 8) |
                  ((uint32_t)data[6] << 16) | ((uint32_t)data[7] << 24);
    if (!flash_wait()) return 0u;
    FLASH->CR |= FLASH_CR_PG;
    *(volatile uint32_t *)address = lo;
    *(volatile uint32_t *)(address + 4u) = hi;
    if (!flash_wait()) {
        FLASH->CR &= ~FLASH_CR_PG;
        return 0u;
    }
    FLASH->CR &= ~FLASH_CR_PG;
    return 1u;
}

static uint8_t flash_program(uint32_t address, const uint8_t *data, uint32_t length)
{
    while (length != 0u) {
        if (!flash_program_doubleword(address, data)) return 0u;
        address += 8u;
        data += 8u;
        length -= 8u;
    }
    return 1u;
}

static uint8_t app_vector_valid(void)
{
    const uint32_t sp = *(const uint32_t *)TACAI_APP_START;
    const uint32_t reset = *(const uint32_t *)(TACAI_APP_START + 4u);
    return ((sp >= 0x20000000u) && (sp <= TACAI_IAP_RAM_MAGIC_ADDR) &&
            ((reset & 1u) != 0u) && (reset >= TACAI_APP_START) &&
            (reset < TACAI_METADATA_ADDRESS));
}

static uint8_t metadata_valid(const image_metadata_t *meta)
{
    if (meta->magic != META_MAGIC) return 0u;
    if (meta->app_start != TACAI_APP_START || meta->image_length == 0u ||
        meta->image_length > TACAI_APP_MAX_SIZE) return 0u;
    if (!bytes_equal((const uint8_t *)meta->model, (const uint8_t *)TACAI_MODEL_ID,
                     sizeof(TACAI_MODEL_ID) - 1u)) return 0u;
    return crc32_update(0u, (const uint8_t *)meta,
                        sizeof(image_metadata_t) - 4u) == meta->record_crc32;
}

static uint8_t write_metadata(uint32_t state, const image_manifest_t *manifest)
{
    image_metadata_t meta;
    uint8_t *bytes = (uint8_t *)&meta;
    uint32_t i;
    for (i = 0u; i < sizeof(meta); ++i) bytes[i] = 0xFFu;
    meta.magic = META_MAGIC;
    meta.state = state;
    meta.app_start = manifest->app_start;
    meta.image_length = manifest->image_length;
    meta.image_crc32 = manifest->image_crc32;
    meta.version = manifest->version;
    for (i = 0u; i < TACAI_MODEL_ID_SIZE; ++i) meta.model[i] = manifest->model[i];
    meta.record_crc32 = crc32_update(0u, (const uint8_t *)&meta, sizeof(meta) - 4u);
    if (!flash_erase_page(TACAI_METADATA_ADDRESS)) return 0u;
    return flash_program(TACAI_METADATA_ADDRESS, (const uint8_t *)&meta, sizeof(meta));
}

static uint8_t manifest_valid(const image_manifest_t *manifest)
{
    uint32_t token;
    if (!bytes_equal((const uint8_t *)manifest->model, (const uint8_t *)TACAI_MODEL_ID,
                     sizeof(TACAI_MODEL_ID) - 1u)) return 0u;
    if (manifest->app_start != TACAI_APP_START || manifest->image_length < 8u ||
        manifest->image_length > TACAI_APP_MAX_SIZE) return 0u;
    token = manifest->image_crc32 ^ manifest->image_length ^
            manifest->app_start ^ TACAI_CONFIRM_XOR;
    return manifest->confirm_token == token;
}

static uint8_t erase_application(void)
{
    uint32_t address;
    for (address = TACAI_APP_START; address < TACAI_METADATA_ADDRESS;
         address += TACAI_FLASH_PAGE_SIZE) {
        if (!flash_erase_page(address)) return 0u;
    }
    return 1u;
}

static void jump_to_application(void)
{
    uint32_t sp;
    uint32_t reset;
    if (!app_vector_valid()) return;
    sp = *(const uint32_t *)TACAI_APP_START;
    reset = *(const uint32_t *)(TACAI_APP_START + 4u);
    __disable_irq();
#ifdef TACAI_ENABLE_SPI
    SPI_LinkDeinit();
#endif
    SysTick->CTRL = 0u;
    USART1->CR1 = 0u;
    SCB->VTOR = TACAI_APP_START;
    SCB->ICSR = SCB_ICSR_PENDSTCLR_Msk | SCB_ICSR_PENDSVCLR_Msk;
    __DSB();
    __ISB();
    __set_MSP(sp);
    __enable_irq();
    ((void (*)(void))reset)();
}

static uint8_t handle_frame(const frame_t *frame)
{
    const image_metadata_t *meta = (const image_metadata_t *)TACAI_METADATA_ADDRESS;
    if (frame->header.command == CMD_HELLO) {
        uint32_t info[4];
        info[0] = PROTOCOL_VERSION;
        info[1] = TACAI_APP_START;
        info[2] = TACAI_APP_MAX_SIZE;
        info[3] = metadata_valid(meta) ? meta->state : 0u;
        send_response(CMD_HELLO, frame->header.sequence, STATUS_OK,
                      (const uint8_t *)info, sizeof(info));
        return 1u;
    }

    if (frame->header.command == CMD_BEGIN) {
        image_manifest_t candidate;
        uint32_t i;
        if (frame->header.length != sizeof(image_manifest_t)) {
            send_response(CMD_BEGIN, frame->header.sequence, STATUS_BAD_MANIFEST, 0, 0u);
            return 1u;
        }
        for (i = 0u; i < sizeof(candidate); ++i)
            ((uint8_t *)&candidate)[i] = frame->payload[i];
        if (!manifest_valid(&candidate)) {
            send_response(CMD_BEGIN, frame->header.sequence, STATUS_BAD_MANIFEST, 0, 0u);
            return 1u;
        }
        /* A rejected BEGIN must not replace an active download's bounds.
         * Keep manifest checks ahead of any state change or Flash operation. */
        g_manifest = candidate;
        g_download_active = 0u;
        g_expected_offset = 0u;
        flash_unlock();
        if (!write_metadata(META_DOWNLOADING, &g_manifest) || !erase_application()) {
            flash_lock();
            send_response(CMD_BEGIN, frame->header.sequence, STATUS_FLASH_ERROR, 0, 0u);
            return 1u;
        }
        flash_lock();
        g_expected_offset = 0u;
        g_download_active = 1u;
        send_response(CMD_BEGIN, frame->header.sequence, STATUS_OK, 0, 0u);
        return 1u;
    }

    if (frame->header.command == CMD_DATA) {
        uint32_t offset;
        uint32_t data_length;
        if (!g_download_active || frame->header.length < 12u) {
            send_response(CMD_DATA, frame->header.sequence, STATUS_NOT_READY, 0, 0u);
            return 1u;
        }
        offset = (uint32_t)frame->payload[0] | ((uint32_t)frame->payload[1] << 8) |
                 ((uint32_t)frame->payload[2] << 16) | ((uint32_t)frame->payload[3] << 24);
        data_length = frame->header.length - 4u;
        if (offset != g_expected_offset || (data_length & 7u) != 0u ||
            offset + data_length > ((g_manifest.image_length + 7u) & ~7u)) {
            send_response(CMD_DATA, frame->header.sequence, STATUS_BAD_SEQUENCE, 0, 0u);
            return 1u;
        }
        flash_unlock();
        if (!flash_program(TACAI_APP_START + offset, frame->payload + 4u, data_length)) {
            flash_lock();
            send_response(CMD_DATA, frame->header.sequence, STATUS_FLASH_ERROR, 0, 0u);
            return 1u;
        }
        flash_lock();
        g_expected_offset += data_length;
        send_response(CMD_DATA, frame->header.sequence, STATUS_OK,
                      (const uint8_t *)&g_expected_offset, 4u);
        return 1u;
    }

    if (frame->header.command == CMD_END) {
        uint32_t crc;
        if (!g_download_active || g_expected_offset < g_manifest.image_length) {
            send_response(CMD_END, frame->header.sequence, STATUS_NOT_READY, 0, 0u);
            return 1u;
        }
        crc = crc32_update(0u, (const uint8_t *)TACAI_APP_START, g_manifest.image_length);
        if (crc != g_manifest.image_crc32) {
            send_response(CMD_END, frame->header.sequence, STATUS_CRC_MISMATCH,
                          (const uint8_t *)&crc, 4u);
            return 1u;
        }
        if (!app_vector_valid()) {
            send_response(CMD_END, frame->header.sequence, STATUS_INVALID_VECTOR, 0, 0u);
            return 1u;
        }
        flash_unlock();
        if (!write_metadata(META_VALID, &g_manifest)) {
            flash_lock();
            send_response(CMD_END, frame->header.sequence, STATUS_FLASH_ERROR, 0, 0u);
            return 1u;
        }
        flash_lock();
        g_download_active = 0u;
        send_response(CMD_END, frame->header.sequence, STATUS_OK, 0, 0u);
        return 1u;
    }

    if (frame->header.command == CMD_BOOT) {
        if (!app_vector_valid() || (metadata_valid(meta) && meta->state == META_DOWNLOADING)) {
            send_response(CMD_BOOT, frame->header.sequence, STATUS_INVALID_VECTOR, 0, 0u);
            return 1u;
        }
        if (!send_response(CMD_BOOT, frame->header.sequence, STATUS_OK, 0, 0u)) {
#ifdef TACAI_ENABLE_SPI
            g_boot_waiting = 1u; /* Do not destroy an ACK that has not been read. */
#endif
            return 1u;
        }
        while ((USART1->ISR & USART_ISR_TC) == 0u) { }
        jump_to_application();
        return 1u;
    }

    if (frame->header.command == CMD_ABORT) {
        g_download_active = 0u;
        send_response(CMD_ABORT, frame->header.sequence, STATUS_OK, 0, 0u);
        return 1u;
    }

    send_response(frame->header.command, frame->header.sequence, STATUS_BAD_FRAME, 0, 0u);
    return 1u;
}

#ifdef TACAI_ENABLE_SPI
static int receive_spi_frame(frame_t *frame)
{
    uint16_t length = SPI_LinkReceive(g_spi_wire, sizeof(g_spi_wire));
    uint32_t i;
    uint32_t wire_crc;
    uint32_t calc_crc;
    if (length == 0u) return 0;
    for (i = 0u; i < FRAME_HEADER_SIZE; ++i) ((uint8_t *)&frame->header)[i] = g_spi_wire[i];
    if (frame->header.magic != FRAME_MAGIC || frame->header.flags != 0u ||
        frame->header.reserved != 0u || frame->header.length > FRAME_MAX_PAYLOAD ||
        length != FRAME_HEADER_SIZE + frame->header.length + 4u) return -1;
    for (i = 0u; i < frame->header.length; ++i) frame->payload[i] = g_spi_wire[FRAME_HEADER_SIZE + i];
    wire_crc = (uint32_t)g_spi_wire[length - 4u] | ((uint32_t)g_spi_wire[length - 3u] << 8) |
               ((uint32_t)g_spi_wire[length - 2u] << 16) | ((uint32_t)g_spi_wire[length - 1u] << 24);
    calc_crc = crc32_update(0u, g_spi_wire, length - 4u);
    return calc_crc == wire_crc ? 1 : -1;
}

static void handle_transport_frame(const frame_t *frame, transport_t source)
{
    g_reply_transport = source;
    if (g_transport == TRANSPORT_NONE) {
        /* Recovery selects the first valid HELLO; BEGIN never implicitly
         * acquires ownership and two channels cannot interleave Flash writes. */
        if (frame->header.command != CMD_HELLO || frame->header.length != 0u) {
            (void)send_response(frame->header.command, frame->header.sequence,
                                STATUS_NOT_READY, 0, 0u);
            return;
        }
        g_transport = source;
    }
    if (source != g_transport) {
        (void)send_response(frame->header.command, frame->header.sequence,
                            STATUS_NOT_READY, 0, 0u);
        return;
    }
    (void)handle_frame(frame);
}
#endif

int main(void)
{
    frame_t frame;
    image_metadata_t const *meta = (const image_metadata_t *)TACAI_METADATA_ADDRESS;
    volatile uint32_t *request = (volatile uint32_t *)TACAI_IAP_RAM_MAGIC_ADDR;
    uint32_t entry_marker = *request;
    uint8_t forced = (entry_marker == TACAI_IAP_RAM_MAGIC);
    uint8_t recovery;
    *request = 0u;
    SCB->VTOR = TACAI_FLASH_BASE;
    __DSB();
    __ISB();
    SystemCoreClock = 16000000u;
    SysTick_Config(16000u);
    uart_init();
    recovery = !app_vector_valid() ||
               (metadata_valid(meta) && meta->state == META_DOWNLOADING);

#ifdef TACAI_ENABLE_SPI
    {
        SPI_LinkCallbacks callbacks = {boot_tick, 0, boot_watchdog};
        uint32_t start = g_ms;
        SPI_LinkInit(&callbacks);
        if (entry_marker == TACAI_SPI_IAP_RAM_MAGIC) {
            forced = 1u;
            g_transport = TRANSPORT_SPI;
        } else {
            g_transport = forced ? TRANSPORT_UART : TRANSPORT_NONE;
        }
        for (;;) {
            int result;
            boot_watchdog();
            if (g_boot_waiting && !SPI_LinkResponsePending()) jump_to_application();
            if (!forced && !recovery && g_transport == TRANSPORT_NONE &&
                (uint32_t)(g_ms - start) >= 2000u) jump_to_application();
            result = receive_spi_frame(&frame);
            if (result == 1) handle_transport_frame(&frame, TRANSPORT_SPI);
            else if (result < 0) {
                g_reply_transport = TRANSPORT_SPI;
                (void)send_response(0u, 0u, STATUS_BAD_FRAME, 0, 0u);
            }
            /* A short first-byte wait preserves legacy UART framing while
             * leaving SPI recovery responsive. A partial UART frame has the
             * original bounded byte timeouts; SPI DMA continues during them. */
            result = receive_frame(&frame, 2u);
            if (result == 1) handle_transport_frame(&frame, TRANSPORT_UART);
            else if (result < 0) {
                g_reply_transport = TRANSPORT_UART;
                (void)send_response(0u, 0u, STATUS_BAD_FRAME, 0, 0u);
            }
        }
    }
#else
    if (!forced && !recovery) {
        int result = receive_frame(&frame, 2000u);
        if (result == 1) handle_frame(&frame);
        else if (result == 0) jump_to_application();
    }

    for (;;) {
        int result = receive_frame(&frame, 0xFFFFFFFFu);
        if (result == 1) handle_frame(&frame);
        else if (result < 0) send_response(0u, 0u, STATUS_BAD_FRAME, 0, 0u);
    }
#endif
}

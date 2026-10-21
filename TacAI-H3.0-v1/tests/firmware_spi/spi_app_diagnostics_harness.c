/* Compile the real diagnostic application, capturing UART printf only. */
#include <assert.h>
#include <stdarg.h>
#include <stdint.h>
#include <stdio.h>
#include <string.h>
static char uart_output[4096];
static unsigned uart_length, uart_calls;
static int capture_uart(const char *format, ...)
{
    int count;
    va_list args;
    va_start(args, format);
    count = vsnprintf(uart_output + uart_length, sizeof(uart_output) - uart_length, format, args);
    va_end(args);
    assert(count >= 0 && (unsigned)count < sizeof(uart_output) - uart_length);
    uart_length += (unsigned)count; uart_calls++;
    return count;
}
#define APP_ENABLE_SPI 1
#define APP_SPI_DIAGNOSTICS 1
#define SPI_LINK_DIAGNOSTICS 1
#define printf capture_uart
#include "../../firmware/application/Core/Src/app_spi.c"
#undef printf
IWDG_HandleTypeDef hiwdg;
test_matrix_sample_t g_data[MATRIX_SCL_COL_NUM][MATRIX_SDA_ROW_NUM];
static uint32_t now_ms;
static unsigned receive_calls, send_calls, reset_calls, diagnostic_reads;
static SPI_LinkDiagnostics fake_diagnostics;
uint32_t HAL_GetTick(void) { return now_ms; }
HAL_StatusTypeDef HAL_IWDG_Refresh(IWDG_HandleTypeDef *handle)
{ assert(handle == &hiwdg); return HAL_OK; }
void MATRIX_GetDiagnostics(matrix_diagnostics_t *out) { memset(out, 0, sizeof(*out)); }
void test_memory_barrier(void) { }
void NVIC_SystemReset(void) { reset_calls++; }
void SPI_LinkInit(const SPI_LinkCallbacks *callbacks)
{ assert(callbacks->tick_ms == HAL_GetTick && callbacks->idle == NULL); }
void SPI_LinkGetDiagnostics(SPI_LinkDiagnostics *out)
{ diagnostic_reads++; *out = fake_diagnostics; }
bool SPI_LinkResponsePending(void) { return false; }
uint16_t SPI_LinkReceive(uint8_t *buffer, uint16_t capacity)
{ (void)buffer; assert(capacity == 536u); receive_calls++; return 0u; }
bool SPI_LinkQueue(const uint8_t *buffer, uint16_t length)
{ (void)buffer; (void)length; send_calls++; return false; }
int main(void)
{
    now_ms = 25u;
    fake_diagnostics.falling = fake_diagnostics.rising = 2u;
    fake_diagnostics.data_length = 16u;
    fake_diagnostics.data_head = 0x54414933u;
    fake_diagnostics.gpio_idr = 1u << 8;
    APP_SPI_Init();
    assert(strcmp(s_snapshot.firmware_id, "h3_spi_20261015_diag_v3") == 0);
    assert(uart_calls == 1u && strstr(uart_output, "SPI_READY,h3_spi_20261015_diag_v3,") != NULL);
    now_ms = 1024u; APP_SPI_Poll(); assert(uart_calls == 1u);
    now_ms = 1025u; APP_SPI_Poll(); assert(uart_calls == 2u);
    assert(strstr(uart_output, "SPI_DIAG,1025,fall=2,rise=2,") != NULL);
    assert(strstr(uart_output, "data=16,head=54414933,req=0,cmd=0,bad=0,") != NULL);
    assert(strstr(uart_output, "nss=1,") != NULL);
    APP_SPI_Poll(); assert(uart_calls == 2u); /* Rate limit even at identical ticks. */
    s_diag_last_ms = 0xFFFFFF00u;
    now_ms = 0x2E7u; APP_SPI_Poll(); assert(uart_calls == 2u);
    now_ms = 0x2E8u; APP_SPI_Poll(); assert(uart_calls == 3u); /* 32-bit tick wrap. */
    assert(diagnostic_reads == 3u && receive_calls == 5u && send_calls == 0u && reset_calls == 0u);
    puts("PASS: production SPI diagnostic ID/UART cadence/tick wrap/read-only cases");
    return 0;
}

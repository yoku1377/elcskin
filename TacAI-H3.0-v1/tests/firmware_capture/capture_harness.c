/* Compile the production sources with only hardware/external-service stubs. */
#include <assert.h>
#include <stdarg.h>
#include <stdio.h>
#include <string.h>
#include <stdlib.h>

static char serial_text[8192];
static size_t serial_used;
static unsigned error_logs;
static int last_failure_preserved;
static int capture_printf(const char *format, ...)
{
    va_list args;
    int n;
    va_start(args,format);
    n=vsnprintf(serial_text+serial_used,sizeof(serial_text)-serial_used,format,args);
    va_end(args);
    assert(n>=0 && serial_used+(size_t)n<sizeof(serial_text));
    if(strstr(serial_text+serial_used,"ERROR")) error_logs++;
    if(strstr(serial_text+serial_used,"previous baseline retained")) last_failure_preserved=1;
    serial_used+=(size_t)n;
    return n;
}
#define printf capture_printf
#include "../../firmware/application/Core/Src/matrix.c"
#include "../../firmware/application/Core/Src/app.c"
#include "../../firmware/application/Core/Src/log.c"
#undef printf

GPIO_TypeDef test_gpio_a, test_gpio_b;
I2C_HandleTypeDef hi2c2;
UART_HandleTypeDef huart1, huart2;
IWDG_HandleTypeDef hiwdg;
static unsigned scl_code, sda_code, sample_cycle, ticks;
static unsigned read_calls[8], force_calls, register_writes, frame_updates;
static unsigned fail_cycle, fail_start_mask, fail_read_mask;
static unsigned fail_scan_mask, fail_init_mask, init_calls;
static unsigned zero_request;
#if APP_ENABLE_SPI
static unsigned spi_published_valid, spi_published_invalid;
static MagForce_Vec3_t spi_last_force;
void APP_SPI_Init(void) {}
void APP_SPI_Poll(void) {}
void APP_SPI_Publish(bool valid,const MagForce_Vec3_t *force)
{
    if(valid) { spi_published_valid++;spi_last_force=*force; }
    else spi_published_invalid++;
}
#endif

static unsigned channel(void)
{
    static const unsigned cols[4]={2,1,0,3};
    unsigned row=sda_code==3 ? 0 : 1;
    assert(sda_code==3 || sda_code==0);
    return cols[scl_code]*2+row;
}
void HAL_GPIO_WritePin(GPIO_TypeDef *port,uint16_t pin,GPIO_PinState state)
{
    if(port==GPIOB && pin==GPIO_PIN_4) scl_code=(scl_code&2u)|(unsigned)state;
    else if(port==GPIOB && pin==GPIO_PIN_5) scl_code=(scl_code&1u)|((unsigned)state<<1);
    else if(port==GPIOA && pin==GPIO_PIN_15) sda_code=(sda_code&2u)|(unsigned)state;
    else if(port==GPIOB && pin==GPIO_PIN_3) sda_code=(sda_code&1u)|((unsigned)state<<1);
    else assert(0);
}
HAL_StatusTypeDef HAL_I2C_IsDeviceReady(I2C_HandleTypeDef *h,uint16_t a,uint32_t t,uint32_t timeout)
{
    unsigned bit=1u<<channel();(void)h;(void)a;(void)t;(void)timeout;
    if(fail_scan_mask&bit) { fail_scan_mask&=~bit;return HAL_ERROR; }
    return HAL_OK;
}
void HAL_Delay(uint32_t ms) { ticks+=ms; }
uint32_t HAL_GetTick(void) { return ticks; }
HAL_StatusTypeDef HAL_IWDG_Refresh(IWDG_HandleTypeDef *h) { (void)h;return HAL_OK; }
void NVIC_SystemReset(void) { assert(0); }
HAL_StatusTypeDef mlx90393_init(mlx90393_config_t *h)
{ init_calls++;h->conv_time=1;return fail_init_mask&(1u<<channel()) ? HAL_ERROR : HAL_OK; }
HAL_StatusTypeDef mlx90393_cmd_start_measurement(const mlx90393_config_t *h)
{
    unsigned ch=channel();(void)h;
    if(ch==0) sample_cycle++;
    return sample_cycle==fail_cycle && (fail_start_mask&(1u<<ch)) ? HAL_ERROR : HAL_OK;
}
HAL_StatusTypeDef mlx90393_cmd_read_measurement(const mlx90393_config_t *h,mlx90393_data_t *out)
{
    unsigned ch=channel();(void)h;
    read_calls[ch]++;
    if(sample_cycle==fail_cycle && (fail_read_mask&(1u<<ch))) return HAL_ERROR;
    memset(out,0,sizeof(*out));
    out->x=1000.0f+(float)(sample_cycle*10+ch);
    out->y=2000.0f+(float)(sample_cycle*10+ch);
    out->z=3000.0f+(float)(sample_cycle*10+ch);
    return HAL_OK;
}
void MAG_FORCE_Calc3DNoCalib(const MagForce_Mag3D_t delta[4][2],MagForce_Vec3_t *out)
{ force_calls++;out->x=delta[0][0].x;out->y=0;out->z=delta[3][1].z; }
void modbus_regs_set_force_xyz(uint8_t point,int16_t x,int16_t y,int16_t z)
{ (void)point;(void)x;(void)y;(void)z;register_writes++; }
void modbus_regs_inc_frame_counter(void) { frame_updates++; }
uint8_t modbus_regs_take_zero_cal_request(void) { uint8_t v=(uint8_t)zero_request;zero_request=0;return v; }
uint8_t modbus_regs_take_save_config_request(void) { return 0; }
uint8_t modbus_regs_take_comm_reconfig_request(void) { return 0; }
uint8_t modbus_regs_take_reboot_request(void) { return 0; }
uint8_t modbus_regs_get_slave_id(void) { return 1; }
void modbus_regs_init(void) {}
void modbus_regs_set_slave_id_runtime(uint8_t id) { (void)id; }
void modbus_regs_bind_slave(modbus_slave_t *s) { (void)s; }
int modbus_regs_read_cb(void *u,uint8_t f,uint16_t a,uint16_t *v) { (void)u;(void)f;(void)a;(void)v;return 0; }
int modbus_regs_write_cb(void *u,uint8_t f,uint16_t a,uint16_t v) { (void)u;(void)f;(void)a;(void)v;return 0; }
void modbus_slave_init(modbus_slave_t *s,uint8_t id,modbus_read_reg_cb_t r,modbus_write_reg_cb_t w,void *u)
{ (void)s;(void)id;(void)r;(void)w;(void)u; }
modbus_slave_status_t modbus_slave_process_frame(modbus_slave_t *s,const uint8_t *r,size_t n,uint8_t *t,size_t max,size_t *out)
{ (void)s;(void)r;(void)n;(void)t;(void)max;*out=0;return MODBUS_SLAVE_OK; }
rs485_port_status_t rs485_port_init(UART_HandleTypeDef *h,rs485_frame_callback_t cb) { (void)h;(void)cb;return RS485_PORT_OK; }
rs485_port_status_t rs485_port_start_rx(void) { return RS485_PORT_OK; }
rs485_port_status_t rs485_port_send(const uint8_t *p,uint16_t n) { (void)p;(void)n;return RS485_PORT_OK; }
void rs485_port_poll(void) {}
uint8_t FLASH_CONFIG_Load(flash_config_t *cfg) { cfg->slave_id=1;return 1; }
HAL_StatusTypeDef FLASH_CONFIG_Save(const flash_config_t *cfg) { (void)cfg;return HAL_OK; }
void IAP_ControlPoll(void) {}

static void reset_fixture(void)
{
    memset(g_baseline,0,sizeof(g_baseline));memset(g_data,0,sizeof(g_data));
    memset(&s_diagnostics,0,sizeof(s_diagnostics));memset(&s_force,0,sizeof(s_force));
    memset(read_calls,0,sizeof(read_calls));
    sample_cycle=ticks=scl_code=sda_code=0;
    fail_cycle=fail_start_mask=fail_read_mask=0;
    fail_scan_mask=fail_init_mask=init_calls=0;
    force_calls=register_writes=frame_updates=zero_request=error_logs=0;
    last_failure_preserved=0;g_error_flag=0;g_measurement_disabled=0;
    s_last_raw_stream_ms=0;serial_used=0;serial_text[0]=0;
    mlx90393_handle.conv_time=1;
#if APP_ENABLE_SPI
    spi_published_valid=spi_published_invalid=0;
    memset(&spi_last_force,0,sizeof(spi_last_force));
#endif
}
static void clear_serial(void) { serial_used=0;serial_text[0]=0;ticks+=100; }
static void assert_diag_only(unsigned mask,unsigned errors,unsigned epoch)
{
    unsigned long ms,e,b;unsigned m;char fw[64];
    const char *diag=strstr(serial_text,"DIAG,");
    assert(diag!=NULL);
    assert(sscanf(diag,"DIAG,%lu,%63[^,],%u,%lu,%lu",&ms,fw,&m,&e,&b)==5);
    assert(strcmp(fw,APP_FIRMWARE_BUILD_ID)==0);
    assert(m==mask && e==errors && b==epoch);
    assert(strstr(serial_text,"RAW,")==NULL && strstr(serial_text,"FORCE,")==NULL);
}
static void read_failure_is_atomic(void)
{
    mlx90393_data_t before[4][2];
    reset_fixture();
    for(unsigned c=0;c<4;c++) for(unsigned r=0;r<2;r++) {
        g_baseline[c][r].x=100;g_baseline[c][r].y=200;g_baseline[c][r].z=300;
    }
    assert(MATRIX_Update_Measurement(1)==HAL_OK);
    memcpy(before,g_data,sizeof(before));fail_cycle=2;fail_read_mask=1u<<3;
    assert(MATRIX_Update_Measurement(1)==HAL_ERROR);
    assert(memcmp(before,g_data,sizeof(before))==0);
    assert(s_diagnostics.valid_mask==247 && s_diagnostics.error_count==1);
    assert(MATRIX_Update_Measurement(1)==HAL_OK);
    assert(g_data[1][1].x==933); /* fresh 1033 minus baseline 100 exactly once */
}
static void failed_start_is_not_read(void)
{
    reset_fixture();fail_cycle=1;fail_start_mask=1u<<6;
    assert(MATRIX_Update_Measurement(1)==HAL_ERROR);
    assert(read_calls[6]==0 && s_diagnostics.valid_mask==191 && s_diagnostics.error_count==1);
    assert(g_data[0][0].x==0 && g_data[3][1].z==0);
}
static void baseline_commit_and_rollback(void)
{
    mlx90393_data_t baseline_before[4][2],data_before[4][2];
    reset_fixture();assert(MATRIX_Update_Baseline()==HAL_OK);
    assert(s_diagnostics.baseline_epoch==1 && g_baseline[0][0].x==1105);
    assert(g_baseline[3][1].z==3112); /* mean rounds 6..15; no channel swap */
    assert(MATRIX_Update_Measurement(1)==HAL_OK);
    memcpy(baseline_before,g_baseline,sizeof(baseline_before));memcpy(data_before,g_data,sizeof(data_before));
    fail_cycle=sample_cycle+MATRIX_EMPTY_ROUND+4;fail_read_mask=1u<<7;
    assert(MATRIX_Update_Baseline()==HAL_ERROR);
    assert(memcmp(baseline_before,g_baseline,sizeof(baseline_before))==0);
    assert(memcmp(data_before,g_data,sizeof(data_before))==0 && s_diagnostics.baseline_epoch==1);
    fail_cycle=sample_cycle+1;fail_start_mask=1u;fail_read_mask=0;
    assert(MATRIX_Update_Baseline()==HAL_ERROR);
    assert(memcmp(baseline_before,g_baseline,sizeof(baseline_before))==0 && s_diagnostics.baseline_epoch==1);
    fail_cycle=0;assert(MATRIX_Update_Baseline()==HAL_OK && s_diagnostics.baseline_epoch==2);
}
static void application_rejects_invalid_frame(void)
{
    reset_fixture();assert(MATRIX_Update_Baseline()==HAL_OK);clear_serial();
    APP_Loop();assert(force_calls==1);
#if APP_ENABLE_SPI
    assert(spi_published_valid==1 && spi_published_invalid==0);
#else
    assert(register_writes==1 && frame_updates==1);
#endif
    assert(strncmp(serial_text,"DIAG,",5)==0 && strstr(serial_text,"RAW,") && strstr(serial_text,"FORCE,"));
    clear_serial();fail_cycle=sample_cycle+1;fail_read_mask=1u<<2;
    APP_Loop();assert_diag_only(251,1,1);
    assert(g_error_flag==0 && error_logs==1 && force_calls==1);
#if APP_ENABLE_SPI
    assert(spi_published_valid==1 && spi_published_invalid==1);
#else
    assert(register_writes==1 && frame_updates==1);
#endif
    clear_serial();fail_cycle=0;APP_Loop();
    assert(g_error_flag==0 && force_calls==2 && strstr(serial_text,",255,1,1\r\nRAW,"));
    assert(g_data[0][0].x==75); /* fresh round 18 minus baseline mean round 10.5 */
#if APP_ENABLE_SPI
    assert(spi_published_valid==2 && spi_published_invalid==1 && spi_last_force.x==75);
#else
    assert(register_writes==2 && frame_updates==2);
#endif
}
static void initial_failure_is_visible(void)
{
    reset_fixture();fail_cycle=1;fail_read_mask=1u;
    APP_MatrixInit();assert(g_error_flag==1 && s_diagnostics.baseline_epoch==0);
    clear_serial();APP_MatrixUpdate();assert_diag_only(0,1,0);
    assert(force_calls==0 && register_writes==0 && frame_updates==0);
#if APP_ENABLE_SPI
    assert(spi_published_valid==0 && spi_published_invalid==1);
#endif
}
#if !APP_ENABLE_SPI
static void modbus_zero_failure_retains_epoch(void)
{
    mlx90393_data_t before[4][2];
    reset_fixture();assert(MATRIX_Update_Baseline()==HAL_OK);memcpy(before,g_baseline,sizeof(before));
    fail_cycle=sample_cycle+6;fail_read_mask=1u<<5;zero_request=1;clear_serial();
    APP_ModbusLoop();assert(last_failure_preserved && s_diagnostics.baseline_epoch==1);
    assert(memcmp(before,g_baseline,sizeof(before))==0);assert_diag_only(223,1,1);
    assert(frame_updates==0 && register_writes==0);
}
#endif
static void preliminary_scan_failure_does_not_disable_successful_startup(void)
{
    reset_fixture();fail_scan_mask=1u<<2;
    APP_Init();
    assert(error_logs==1 && init_calls==8 && s_diagnostics.baseline_epoch==1);
    assert(g_error_flag==0 && sample_cycle==15);
    clear_serial();APP_Loop();
    assert(force_calls==1 && g_data[0][0].x==55);
    assert(strstr(serial_text,",255,0,1\r\nRAW,") && strstr(serial_text,"FORCE,"));
#if APP_ENABLE_SPI
    assert(spi_published_valid==1 && spi_published_invalid==0 && spi_last_force.x==55);
#else
    assert(register_writes==1 && frame_updates==1);
#endif
}
static void failed_start_retries_with_fresh_measurement(void)
{
    unsigned reads_before;
    reset_fixture();APP_Init();clear_serial();
    APP_Loop();assert(force_calls==1);
    reads_before=read_calls[6];fail_cycle=sample_cycle+1;fail_start_mask=1u<<6;
    clear_serial();APP_Loop();assert_diag_only(191,1,1);
    assert(read_calls[6]==reads_before && g_error_flag==0 && force_calls==1 && error_logs==1);
#if APP_ENABLE_SPI
    assert(spi_published_valid==1 && spi_published_invalid==1);
#endif
    fail_cycle=0;clear_serial();APP_Loop();
    assert(read_calls[6]==reads_before+1 && g_error_flag==0 && force_calls==2);
    assert(g_data[0][0].x==75 && strstr(serial_text,",255,1,1\r\nRAW,"));
#if APP_ENABLE_SPI
    assert(spi_published_valid==2 && spi_published_invalid==1 && spi_last_force.x==75);
#endif
}
static void fatal_sensor_init_failure_remains_disabled(void)
{
    reset_fixture();fail_init_mask=1u<<4;APP_Init();
    assert(g_error_flag==1 && init_calls==8 && sample_cycle==0 && s_diagnostics.baseline_epoch==0);
    fail_init_mask=0;clear_serial();APP_Loop();assert_diag_only(0,0,0);
    assert(g_error_flag==1 && sample_cycle==0 && force_calls==0);
    assert(register_writes==0 && frame_updates==0);
#if APP_ENABLE_SPI
    assert(spi_published_valid==0 && spi_published_invalid==1);
#endif
}
int main(void)
{
    read_failure_is_atomic();failed_start_is_not_read();baseline_commit_and_rollback();
    application_rejects_invalid_frame();initial_failure_is_visible();
    preliminary_scan_failure_does_not_disable_successful_startup();
    failed_start_retries_with_fresh_measurement();fatal_sensor_init_failure_remains_disabled();
#if APP_ENABLE_SPI
    puts("PASS: 8 production capture/log recovery cases (SPI)");
#else
    modbus_zero_failure_retains_epoch();
    puts("PASS: 9 production capture/log recovery cases (UART)");
#endif
    return 0;
}

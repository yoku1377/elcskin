/**
 * @file vcnl4020.h
 * @author ultralyj (yijie_luo@tongji.edu.cn)
 * @brief  Header file of vcnl4020 module.
 * @version 0.1
 * @date 2025-02-24
 *
 * @copyright Copyright (c) 2025
 *
 */

#ifndef __VCNL4020_H__
#define __VCNL4020_H__

#ifdef __cplusplus
extern "C"
{
#endif

/* Includes ------------------------------------------------------------------*/
/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "stm32g0xx_hal.h"
#include "i2c.h"

    /* USER CODE END Includes */

    /* Private defines -----------------------------------------------------------*/
    /* USER CODE BEGIN Private defines */
#ifndef DRV_BYTE_ORDER
#ifndef __BYTE_ORDER__

#define DRV_LITTLE_ENDIAN 1234
#define DRV_BIG_ENDIAN 4321

/** if _BYTE_ORDER is not defined, choose the endianness of your architecture
 * by uncommenting the define which fits your platform endianness
 */
// #define DRV_BYTE_ORDER    DRV_BIG_ENDIAN
#define DRV_BYTE_ORDER DRV_LITTLE_ENDIAN

#else /* defined __BYTE_ORDER__ */

#define DRV_LITTLE_ENDIAN __ORDER_LITTLE_ENDIAN__
#define DRV_BIG_ENDIAN __ORDER_BIG_ENDIAN__
#define DRV_BYTE_ORDER __BYTE_ORDER__

#endif /* __BYTE_ORDER__*/
#endif /* DRV_BYTE_ORDER */

#define VCNL4020_I2C_BUS hi2c2
#define VCNL4020_I2C_ADDRESS 0x13 // The address is fixed
#define VCNL4020_I2C_TIMEOUT_DEFAULT        (1000)  /*!< Timeout in milliseconds */

// VCNL4020 Register Definitions
#define VCNL4020_REG_COMMAND 0x80             // Register #0 Command Register
#define VCNL4020_REG_PRODUCT_ID 0x81          // Register #1 Product ID Revision Register
#define VCNL4020_REG_PROX_RATE 0x82           // Register #2 Rate of Proximity Measurement
#define VCNL4020_REG_IR_LED_CURRENT 0x83      // Register #3 (Not explicitly named in datasheet)
#define VCNL4020_REG_AMBIENT_PARAM 0x84       // Register #4 Ambient Light Parameter Register
#define VCNL4020_REG_AMBIENT_RESULT_HIGH 0x85 // Register #5 Ambient Light Result High Byte
#define VCNL4020_REG_AMBIENT_RESULT_LOW 0x86  // Register #6 Ambient Light Result Low Byte
#define VCNL4020_REG_PROX_RESULT_HIGH 0x87    // Register #7 Proximity Result High Byte
#define VCNL4020_REG_PROX_RESULT_LOW 0x88     // Register #8 Proximity Result Low Byte
#define VCNL4020_REG_INT_CTRL 0x89            // Register #9 Interrupt Control Register
#define VCNL4020_REG_LOW_THRES_HIGH 0x8A      // Register #10 Low Threshold High Byte
#define VCNL4020_REG_LOW_THRES_LOW 0x8B       // Register #11 Low Threshold Low Byte
#define VCNL4020_REG_HIGH_THRES_HIGH 0x8C     // Register #12 High Threshold High Byte
#define VCNL4020_REG_HIGH_THRES_LOW 0x8D      // Register #13 High Threshold Low Byte
#define VCNL4020_REG_INT_STATUS 0x8E          // Register #14 Interrupt status register
#define VCNL4020_REG_PROX_ADJUST 0x8F         // Register #15 Proximity adjustment register

#define VCNL4020_CURRENT_REVISION 0x21

#define VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE 7
    /* USER CODE END Private defines */

    /* Exported types ------------------------------------------------------------*/
    /* USER CODE BEGIN ET */

    typedef enum
    {
        VCNL4020_PROX_RATE_1_95 = 0,
        VCNL4020_PROX_RATE_3_90625 = 1,
        VCNL4020_PROX_RATE_7_8125 = 2,
        VCNL4020_PROX_RATE_16_625 = 3,
        VCNL4020_PROX_RATE_31_25 = 4,
        VCNL4020_PROX_RATE_62_5 = 5,
        VCNL4020_PROX_RATE_125 = 6,
        VCNL4020_PROX_RATE_250 = 7
    } vcnl4020_prox_rate_t;

    typedef enum
    {
        VCNL4020_AMBIENT_RATE_1 = 0,
        VCNL4020_AMBIENT_RATE_2 = 1,
        VCNL4020_AMBIENT_RATE_3 = 2,
        VCNL4020_AMBIENT_RATE_4 = 3,
        VCNL4020_AMBIENT_RATE_5 = 4,
        VCNL4020_AMBIENT_RATE_6 = 5,
        VCNL4020_AMBIENT_RATE_8 = 6,
        VCNL4020_AMBIENT_RATE_10 = 7
    } vcnl4020_ambient_rate_t;

    typedef enum
    {
        VCNL4020_AVERAGE_1CONV = 0,
        VCNL4020_AVERAGE_2CONV = 1,
        VCNL4020_AVERAGE_4CONV = 2,
        VCNL4020_AVERAGE_8CONV = 3,
        VCNL4020_AVERAGE_16CONV = 4,
        VCNL4020_AVERAGE_32CONV = 5,
        VCNL4020_AVERAGE_64CONV = 6,
        VCNL4020_AVERAGE_128CONV = 7
    } vcnl4020_average_func_t;

    typedef enum
    {
        VCNL4020_EXCEED_1 = 0,
        VCNL4020_EXCEED_2 = 1,
        VCNL4020_EXCEED_4 = 2,
        VCNL4020_EXCEED_8 = 3,
        VCNL4020_EXCEED_16 = 4,
        VCNL4020_EXCEED_32 = 5,
        VCNL4020_EXCEED_64 = 6,
        VCNL4020_EXCEED_128 = 7
    } vcnl4020_int_cnt_exceed_t;

    typedef enum
    {
        VCNL4020_PROX_FREQ_390_625KHZ = 0,
        VCNL4020_PROX_FREQ_781_25KHZ = 1,
        VCNL4020_PROX_FREQ_1_5625MHZ = 2,
        VCNL4020_PROX_FREQ_3_125MHZ = 3
    } vcnl4020_prox_freq_t;
    /**
     * @note With setting bit 3 and bit 4 at the same write command, a simultaneously measurement of ambient  light and proximity is done. Beside als_en
        and/or prox_en first selftimed_en needs to be set. On-demand measurement modes are disabled if selftimed_en bit is set. For the
        selftimed_en mode changes in reading rates (reg #4 and reg #2) can be made only when b0 (selftimed_en bit) = 0. For the als_od mode
        changes to the reg #4 can be made only when b4 (als_od bit) = 0; this is to avoid synchronization problems and undefined states between
        the clock domains. In effect this means that it is only reasonable to change rates while no selftimed conversion is ongoing.
     *
     */
    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        uint8_t selftimed_en : 1;
        uint8_t prox_en : 1;
        uint8_t als_en : 1;
        uint8_t prox_od : 1;
        uint8_t als_od : 1;
        uint8_t prox_data_rdy : 1;
        uint8_t als_data_rdy : 1;
        uint8_t config_lock : 1; // [READ-ONLY]
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t config_lock : 1; // [READ-ONLY]
    uint8_t als_data_rdy : 1;
    uint8_t prox_data_rdy : 1;
    uint8_t als_od : 1;
    uint8_t prox_od : 1;
    uint8_t als_en : 1;
    uint8_t prox_en : 1;
    uint8_t selftimed_en : 1;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_command_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        vcnl4020_prox_rate_t prox_rate : 3; // [READ-ONLY]
        uint8_t no_use_3 : 5;
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t no_use_3 : 5;
    vcnl4020_prox_rate_t prox_rate : 3;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_prox_rate_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        uint8_t ir_led_current_value : 6;
        uint8_t fuse_prog_id : 2; // [READ-ONLY]
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t fuse_prog_id : 2; // [READ-ONLY]
    uint8_t ir_led_current_value : 6;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_ir_led_current_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        vcnl4020_average_func_t average_func : 3;
        uint8_t auto_offset_compensation : 1;
        vcnl4020_ambient_rate_t als_rate : 3;
        uint8_t cont_conv_mode : 1;
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t cont_conv_mode : 1;
    vcnl4020_ambient_rate_t als_rate : 3;
    uint8_t auto_offset_compensation : 1;
    vcnl4020_average_func_t average_func : 3;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_ambient_param_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        uint8_t int_thres_sel : 1;
        uint8_t int_thres_en : 1;
        uint8_t int_als_ready_en : 1;
        uint8_t int_prox_ready_en : 1;
        uint8_t no_use : 1;
        uint8_t int_cnt_exceed : 3;
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t int_cnt_exceed : 3;
    uint8_t no_use : 1;
    uint8_t int_prox_ready_en : 1;
    uint8_t int_als_ready_en : 1;
    uint8_t int_thres_en : 1;
    uint8_t int_thres_sel : 1;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_int_ctrl_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        uint8_t int_th_hi : 1;
        uint8_t int_th_low : 1;
        uint8_t int_als_ready : 1;
        uint8_t int_prox_ready : 1;
        uint8_t no_use_2 : 4;
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t no_use_2 : 4;
    uint8_t int_prox_ready : 1;
    uint8_t int_als_ready : 1;
    uint8_t int_th_low : 1;
    uint8_t int_th_hi : 1;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_int_status_t;

    typedef struct
    {
#if DRV_BYTE_ORDER == DRV_LITTLE_ENDIAN
        uint8_t modual_dead_time : 3;
        vcnl4020_prox_freq_t prox_freq : 2;
        uint8_t modual_delay_time : 3;
#elif DRV_BYTE_ORDER == DRV_BIG_ENDIAN
    uint8_t modual_delay_time : 3;
    vcnl4020_prox_freq_t prox_freq : 2;
    uint8_t modual_dead_time : 3;
#endif /* DRV_BYTE_ORDER */
    } vcnl4020_reg_prox_status_t;

    typedef struct
    {
        uint8_t prox_led_mA;
        vcnl4020_prox_rate_t prox_rate;
        vcnl4020_ambient_rate_t als_rate;
        vcnl4020_average_func_t average_func;
        vcnl4020_prox_freq_t prox_freq;
    } vcnl4020_configure_t;

    typedef struct
    {
        uint8_t index;
        uint8_t is_initialized;
        uint16_t prox_history_data[VCNL4020_SOFTWARE_FILTER_WINDOWS_SIZE];
        vcnl4020_configure_t config;
    } vcnl4020_object_t;
    /* USER CODE END ET */

    /* Exported constants --------------------------------------------------------*/
    /* USER CODE BEGIN EC */

    /* USER CODE END EC */

    /* Exported macro ------------------------------------------------------------*/
    /* USER CODE BEGIN EM */

    /* USER CODE END EM */

    /* Exported functions prototypes ---------------------------------------------*/

    /* USER CODE BEGIN EFP */
    int32_t VCNL4020_Init(vcnl4020_object_t *pObj);
    int32_t VCNL4020_Read_Proximity(vcnl4020_object_t *pObj, uint16_t *prox);
    int32_t VCNL4020_Read_Ambient(vcnl4020_object_t *pObj, uint16_t *ambient);

    /* USER CODE END EFP */

    /* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif

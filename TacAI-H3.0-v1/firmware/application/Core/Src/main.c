/* USER CODE BEGIN Header */
/**
 ******************************************************************************
 * @file           : main.c
 * @brief          : Main program body
 ******************************************************************************
 * @attention
 *
 * Copyright (c) 2025 STMicroelectronics.
 * All rights reserved.
 *
 * This software is licensed under terms that can be found in the LICENSE file
 * in the root directory of this software component.
 * If no LICENSE file comes with this software, it is provided AS-IS.
 *
 ******************************************************************************
 */
/* USER CODE END Header */
/* Includes ------------------------------------------------------------------*/
#include "main.h"
#include "dma.h"
#include "i2c.h"
#include "iwdg.h"
#include "tim.h"
#include "usart.h"
#include "gpio.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */
#include "app.h"
#include "app_spi.h"
#include <stdio.h>
/* USER CODE END Includes */

/* Private typedef -----------------------------------------------------------*/
/* USER CODE BEGIN PTD */

/* USER CODE END PTD */

/* Private define ------------------------------------------------------------*/
/* USER CODE BEGIN PD */

/* USER CODE END PD */

/* Private macro -------------------------------------------------------------*/
/* USER CODE BEGIN PM */
/* USER CODE END PM */

/* Private variables ---------------------------------------------------------*/
/* USER CODE BEGIN PV */
/* USER CODE END PV */

/* Private function prototypes -----------------------------------------------*/
void SystemClock_Config(void);
/* USER CODE BEGIN PFP */
/* USER CODE END PFP */

/* Private user code ---------------------------------------------------------*/
/* USER CODE BEGIN 0 */
static void APP_EarlyBootUartInit(void)
{
    RCC->IOPENR |= RCC_IOPENR_GPIOBEN;
    RCC->APBENR2 |= RCC_APBENR2_USART1EN;
    (void)RCC->IOPENR;
    GPIOB->MODER = (GPIOB->MODER & ~((3u << (6u * 2u)) | (3u << (7u * 2u)))) |
                   (2u << (6u * 2u)) | (2u << (7u * 2u));
    GPIOB->AFR[0] &= ~((0xFu << (6u * 4u)) | (0xFu << (7u * 4u)));
    GPIOB->OSPEEDR |= (2u << (6u * 2u)) | (2u << (7u * 2u));
    USART1->CR1 = 0u;
    USART1->BRR = 16000000u / 115200u;
    USART1->CR1 = USART_CR1_TE | USART_CR1_RE | USART_CR1_UE;
}

static void APP_EarlyBootUartWrite(const char *text)
{
    while (*text != '\0')
    {
        while ((USART1->ISR & USART_ISR_TXE_TXFNF) == 0u) { }
        USART1->TDR = (uint8_t)*text++;
    }
    while ((USART1->ISR & USART_ISR_TC) == 0u) { }
}
/* USER CODE END 0 */

/**
 * @brief  The application entry point.
 * @retval int
 */
int main(void)
{

    /* USER CODE BEGIN 1 */

    /*
     * The resident UART bootloader masks global interrupts while handing
     * control to the application. PRIMASK is not reset by a branch to the
     * application's Reset_Handler, so restore the normal reset state before
     * HAL_Init() starts SysTick. Without this, HAL_Delay() in the sensor
     * matrix start-up never advances after an IAP update.
     */
    __enable_irq();
    APP_EarlyBootUartInit();
    APP_EarlyBootUartWrite("BOOT0 app reset entered\r\n");

    /* USER CODE END 1 */

    /* MCU Configuration--------------------------------------------------------*/

    /* Reset of all peripherals, Initializes the Flash interface and the Systick. */
    HAL_Init();

    /* USER CODE BEGIN Init */

    /* USER CODE END Init */

    /* Configure the system clock */
    SystemClock_Config();

    /* USER CODE BEGIN SysInit */

    /* USER CODE END SysInit */

    /* Initialize all configured peripherals */
    MX_GPIO_Init();
    MX_USART1_UART_Init();
    printf("BOOT1 clock gpio uart1 ok\r\n");
#if !APP_ENABLE_SPI
    MX_DMA_Init();
    printf("BOOT2 dma ok\r\n");
#else
    printf("BOOT2 uart2 dma skipped for spi2\r\n");
#endif
    MX_I2C2_Init();
    printf("BOOT3 i2c2 ok\r\n");
    MX_TIM2_Init();
    MX_TIM14_Init();
    MX_TIM16_Init();
    printf("BOOT4 timers ok\r\n");
#if !APP_ENABLE_SPI
    MX_USART2_UART_Init();
    printf("BOOT5 uart2 ok\r\n");
#else
    /* Establish the SPI peripheral before the sensor scan/baseline. A
     * pending request remains queued until APP_Init returns; no sample is
     * marked valid during sensor initialization. */
    APP_SPI_Init();
    printf("BOOT5 spi2 ready; matrix init next\r\n");
#endif
    /* USER CODE BEGIN 2 */
    APP_Init();
    printf("BOOT6 app init ok\r\n");
    MX_IWDG_Init();
    printf("BOOT7 watchdog ok\r\n");
    /* USER CODE END 2 */

    /* Infinite loop */
    /* USER CODE BEGIN WHILE */

    while (1)
    {
        /* USER CODE END WHILE */

        /* USER CODE BEGIN 3 */
        APP_Loop();
    }
    /* USER CODE END 3 */
}

/**
 * @brief System Clock Configuration
 * @retval None
 */
void SystemClock_Config(void)
{
    RCC_OscInitTypeDef RCC_OscInitStruct = {0};
    RCC_ClkInitTypeDef RCC_ClkInitStruct = {0};

    /** Configure the main internal regulator output voltage
     */
    HAL_PWREx_ControlVoltageScaling(PWR_REGULATOR_VOLTAGE_SCALE1);

    /** Initializes the RCC Oscillators according to the specified parameters
     * in the RCC_OscInitTypeDef structure.
     */
    RCC_OscInitStruct.OscillatorType = RCC_OSCILLATORTYPE_HSI | RCC_OSCILLATORTYPE_LSI;
    RCC_OscInitStruct.HSIState = RCC_HSI_ON;
    RCC_OscInitStruct.HSIDiv = RCC_HSI_DIV1;
    RCC_OscInitStruct.HSICalibrationValue = RCC_HSICALIBRATION_DEFAULT;
    RCC_OscInitStruct.LSIState = RCC_LSI_ON;
    RCC_OscInitStruct.PLL.PLLState = RCC_PLL_ON;
    RCC_OscInitStruct.PLL.PLLSource = RCC_PLLSOURCE_HSI;
    RCC_OscInitStruct.PLL.PLLM = RCC_PLLM_DIV1;
    RCC_OscInitStruct.PLL.PLLN = 8;
    RCC_OscInitStruct.PLL.PLLP = RCC_PLLP_DIV2;
    RCC_OscInitStruct.PLL.PLLQ = RCC_PLLQ_DIV2;
    RCC_OscInitStruct.PLL.PLLR = RCC_PLLR_DIV2;
    if (HAL_RCC_OscConfig(&RCC_OscInitStruct) != HAL_OK)
    {
        Error_Handler();
    }

    /** Initializes the CPU, AHB and APB buses clocks
     */
    RCC_ClkInitStruct.ClockType = RCC_CLOCKTYPE_HCLK | RCC_CLOCKTYPE_SYSCLK | RCC_CLOCKTYPE_PCLK1;
    RCC_ClkInitStruct.SYSCLKSource = RCC_SYSCLKSOURCE_PLLCLK;
    RCC_ClkInitStruct.AHBCLKDivider = RCC_SYSCLK_DIV1;
    RCC_ClkInitStruct.APB1CLKDivider = RCC_HCLK_DIV1;

    if (HAL_RCC_ClockConfig(&RCC_ClkInitStruct, FLASH_LATENCY_2) != HAL_OK)
    {
        Error_Handler();
    }
}

/* USER CODE BEGIN 4 */

/* USER CODE END 4 */

/**
 * @brief  This function is executed in case of error occurrence.
 * @retval None
 */
void Error_Handler(void)
{
    /* USER CODE BEGIN Error_Handler_Debug */
    /* User can add his own implementation to report the HAL error return state */
    __disable_irq();
    APP_EarlyBootUartWrite("BOOT_ERROR Error_Handler\r\n");
    while (1)
    {
        APP_EarlyBootUartWrite("BOOT_ERROR Error_Handler\r\n");
    }
    /* USER CODE END Error_Handler_Debug */
}

#ifdef USE_FULL_ASSERT
/**
 * @brief  Reports the name of the source file and the source line number
 *         where the assert_param error has occurred.
 * @param  file: pointer to the source file name
 * @param  line: assert_param error line source number
 * @retval None
 */
void assert_failed(uint8_t *file, uint32_t line)
{
    /* USER CODE BEGIN 6 */
    /* User can add his own implementation to report the file name and line number,
       ex: printf("Wrong parameters value: file %s on line %d\r\n", file, line) */
    /* USER CODE END 6 */
}
#endif /* USE_FULL_ASSERT */

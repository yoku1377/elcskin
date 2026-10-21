/* USER CODE BEGIN Header */
/**
  ******************************************************************************
  * @file           : main.h
  * @brief          : Header for main.c file.
  *                   This file contains the common defines of the application.
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

/* Define to prevent recursive inclusion -------------------------------------*/
#ifndef __MAIN_H
#define __MAIN_H

#ifdef __cplusplus
extern "C" {
#endif

/* Includes ------------------------------------------------------------------*/
#include "stm32g0xx_hal.h"

/* Private includes ----------------------------------------------------------*/
/* USER CODE BEGIN Includes */

/* USER CODE END Includes */

/* Exported types ------------------------------------------------------------*/
/* USER CODE BEGIN ET */

/* USER CODE END ET */

/* Exported constants --------------------------------------------------------*/
/* USER CODE BEGIN EC */

/* USER CODE END EC */

/* Exported macro ------------------------------------------------------------*/
/* USER CODE BEGIN EM */

/* USER CODE END EM */

/* Exported functions prototypes ---------------------------------------------*/
void Error_Handler(void);

/* USER CODE BEGIN EFP */

/* USER CODE END EFP */

/* Private defines -----------------------------------------------------------*/
/* U11 pin 14 -> R1/D1 on TacAI-H2.1_4.21: active-low LED. */
#define LED_Pin GPIO_PIN_0
#define LED_GPIO_Port GPIOB

/* 4x2 MLX90393 matrix multiplexers (TacAI-H2.1_4.21, sheet I2C_MUX). */
#define I2C1_SDA_SEL0_Pin GPIO_PIN_15
#define I2C1_SDA_SEL0_GPIO_Port GPIOA
#define I2C1_SDA_SEL1_Pin GPIO_PIN_3
#define I2C1_SDA_SEL1_GPIO_Port GPIOB
#define I2C1_SCL_SEL0_Pin GPIO_PIN_4
#define I2C1_SCL_SEL0_GPIO_Port GPIOB
#define I2C1_SCL_SEL1_Pin GPIO_PIN_5
#define I2C1_SCL_SEL1_GPIO_Port GPIOB

/* USER CODE BEGIN Private defines */

/* USER CODE END Private defines */

#ifdef __cplusplus
}
#endif

#endif /* __MAIN_H */

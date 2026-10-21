#ifndef FLASH_CONFIG_H
#define FLASH_CONFIG_H

#ifdef __cplusplus
extern "C" {
#endif

#include <stdint.h>
#include "main.h"

#define FLASH_CONFIG_DEFAULT_SLAVE_ID  1u

typedef struct
{
    uint8_t slave_id;
} flash_config_t;

void FLASH_CONFIG_LoadDefault(flash_config_t *cfg);

uint8_t FLASH_CONFIG_Load(flash_config_t *cfg);

HAL_StatusTypeDef FLASH_CONFIG_Save(const flash_config_t *cfg);

#ifdef __cplusplus
}
#endif

#endif
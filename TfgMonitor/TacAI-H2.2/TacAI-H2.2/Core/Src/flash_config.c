#include "flash_config.h"
#include <string.h>
#include <stddef.h>

#define FLASH_CONFIG_MAGIC    0x54414348u  /* "TACH" */
#define FLASH_CONFIG_VERSION  0x0001u

/*
 * 如果你的芯片头文件已经定义 FLASH_SIZE，就直接用。
 * 如果没有，就手动定义 FLASH_CONFIG_FLASH_SIZE_BYTES。
 */
#ifndef FLASH_CONFIG_FLASH_SIZE_BYTES
#ifdef FLASH_SIZE
#define FLASH_CONFIG_FLASH_SIZE_BYTES  FLASH_SIZE
#else
#define FLASH_CONFIG_FLASH_SIZE_BYTES  (128u * 1024u)
#endif
#endif

#ifndef FLASH_CONFIG_PAGE_INDEX
#define FLASH_CONFIG_PAGE_INDEX \
    ((FLASH_CONFIG_FLASH_SIZE_BYTES / FLASH_PAGE_SIZE) - 1u)
#endif

#ifndef FLASH_CONFIG_ADDRESS
#define FLASH_CONFIG_ADDRESS \
    (FLASH_BASE + FLASH_CONFIG_PAGE_INDEX * FLASH_PAGE_SIZE)
#endif

typedef struct
{
    uint32_t magic;
    uint16_t version;
    uint16_t size;

    uint8_t slave_id;
    uint8_t reserved0;
    uint16_t reserved1;

    uint16_t crc;
    uint16_t reserved2;
} flash_config_record_t;

/*
 * 这个结构体大小必须是 8 字节倍数，方便按 double word 写 Flash。
 */
typedef union
{
    flash_config_record_t rec;
    uint64_t words[(sizeof(flash_config_record_t) + 7u) / 8u];
} flash_config_record_union_t;

static uint16_t flash_config_crc16(const uint8_t *data, uint32_t len)
{
    uint16_t crc = 0xFFFFu;

    for (uint32_t i = 0; i < len; i++)
    {
        crc ^= data[i];

        for (uint8_t j = 0; j < 8u; j++)
        {
            if (crc & 0x0001u)
            {
                crc = (crc >> 1) ^ 0xA001u;
            }
            else
            {
                crc >>= 1;
            }
        }
    }

    return crc;
}

static uint8_t flash_config_slave_id_valid(uint8_t id)
{
    return ((id >= 1u) && (id <= 247u)) ? 1u : 0u;
}

void FLASH_CONFIG_LoadDefault(flash_config_t *cfg)
{
    if (cfg == 0)
    {
        return;
    }

    cfg->slave_id = FLASH_CONFIG_DEFAULT_SLAVE_ID;
}

uint8_t FLASH_CONFIG_Load(flash_config_t *cfg)
{
    const flash_config_record_t *rec =
        (const flash_config_record_t *)FLASH_CONFIG_ADDRESS;

    uint16_t crc_calc;

    if (cfg == 0)
    {
        return 0u;
    }

    FLASH_CONFIG_LoadDefault(cfg);

    if (rec->magic != FLASH_CONFIG_MAGIC)
    {
        return 0u;
    }

    if (rec->version != FLASH_CONFIG_VERSION)
    {
        return 0u;
    }

    if (rec->size != sizeof(flash_config_record_t))
    {
        return 0u;
    }

    if (!flash_config_slave_id_valid(rec->slave_id))
    {
        return 0u;
    }

    crc_calc = flash_config_crc16(
        (const uint8_t *)rec,
        offsetof(flash_config_record_t, crc)
    );

    if (crc_calc != rec->crc)
    {
        return 0u;
    }

    cfg->slave_id = rec->slave_id;
    return 1u;
}

HAL_StatusTypeDef FLASH_CONFIG_Save(const flash_config_t *cfg)
{
    FLASH_EraseInitTypeDef erase;
    uint32_t page_error = 0u;
    HAL_StatusTypeDef ret;
    flash_config_record_union_t image;

    if (cfg == 0)
    {
        return HAL_ERROR;
    }

    if (!flash_config_slave_id_valid(cfg->slave_id))
    {
        return HAL_ERROR;
    }

    memset(&image, 0xFF, sizeof(image));

    image.rec.magic = FLASH_CONFIG_MAGIC;
    image.rec.version = FLASH_CONFIG_VERSION;
    image.rec.size = sizeof(flash_config_record_t);
    image.rec.slave_id = cfg->slave_id;

    image.rec.crc = flash_config_crc16(
        (const uint8_t *)&image.rec,
        offsetof(flash_config_record_t, crc)
    );

    HAL_FLASH_Unlock();

    __HAL_FLASH_CLEAR_FLAG(
        FLASH_FLAG_EOP |
        FLASH_FLAG_OPERR |
        FLASH_FLAG_PROGERR |
        FLASH_FLAG_WRPERR |
        FLASH_FLAG_PGAERR |
        FLASH_FLAG_SIZERR
    );

    memset(&erase, 0, sizeof(erase));
    erase.TypeErase = FLASH_TYPEERASE_PAGES;
    erase.Page = FLASH_CONFIG_PAGE_INDEX;
    erase.NbPages = 1u;

#ifdef FLASH_BANK_1
    erase.Banks = FLASH_BANK_1;
#endif

    ret = HAL_FLASHEx_Erase(&erase, &page_error);

    if (ret == HAL_OK)
    {
        for (uint32_t i = 0u; i < (sizeof(image.words) / sizeof(image.words[0])); i++)
        {
            ret = HAL_FLASH_Program(
                FLASH_TYPEPROGRAM_DOUBLEWORD,
                FLASH_CONFIG_ADDRESS + i * 8u,
                image.words[i]
            );

            if (ret != HAL_OK)
            {
                break;
            }
        }
    }

    HAL_FLASH_Lock();

    return ret;
}
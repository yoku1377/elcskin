#ifndef APP_SPI_H
#define APP_SPI_H

#ifndef APP_ENABLE_SPI
#define APP_ENABLE_SPI 0
#endif

#ifndef APP_SPI_DIAGNOSTICS
#define APP_SPI_DIAGNOSTICS 0
#endif
#if defined(SPI_LINK_RX_IRQ) && SPI_LINK_RX_IRQ
#define APP_SPI_FIRMWARE_ID "h3_spi_20261016_rxirq_v5"
#elif defined(APP_SPI_AUDIT) && APP_SPI_AUDIT
#define APP_SPI_FIRMWARE_ID "h3_spi_20261016_audit_v4"
#elif APP_SPI_DIAGNOSTICS
#define APP_SPI_FIRMWARE_ID "h3_spi_20261015_diag_v3"
#else
#define APP_SPI_FIRMWARE_ID "h3_spi_20261014_v1"
#endif

#if APP_ENABLE_SPI
#include <stdbool.h>
#include "mag_force.h"
void APP_SPI_Init(void);
void APP_SPI_Poll(void);
void APP_SPI_Publish(bool valid, const MagForce_Vec3_t *force);
#endif
#endif

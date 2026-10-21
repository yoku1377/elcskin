param([switch]$EnableSpi, [switch]$SpiDiagnostics, [switch]$SpiAudit, [switch]$SpiRxIrqAudit)
$ErrorActionPreference = 'Stop'
if ($SpiRxIrqAudit) { $SpiDiagnostics = $true }
if ($SpiAudit) { $SpiDiagnostics = $true }
if ($SpiDiagnostics) { $EnableSpi = $true }
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$tool = 'C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin'
$gcc = Join-Path $tool 'arm-none-eabi-gcc.exe'
$objcopy = Join-Path $tool 'arm-none-eabi-objcopy.exe'
$size = Join-Path $tool 'arm-none-eabi-size.exe'
$build = Join-Path $project $(if ($SpiRxIrqAudit) { 'build_spi_rxirq_v5' } elseif ($SpiAudit) { 'build_spi_audit_v4' } elseif ($SpiDiagnostics) { 'build_spi_diag_v3' } elseif ($EnableSpi) { 'build_spi' } else { 'build' })
$imageName = if ($SpiRxIrqAudit) { 'TacAI_H3_App_SPI_rxirq_v5' } elseif ($SpiAudit) { 'TacAI_H3_App_SPI_audit_v4' } elseif ($SpiDiagnostics) { 'TacAI_H3_App_SPI_diag_v3' } elseif ($EnableSpi) { 'TacAI_H3_App_SPI_v2' } else { 'TacAI_H3_App_v1' }
New-Item -ItemType Directory -Force -Path $build | Out-Null

$includes = @("-I$project\Core\Inc", "-I$project\Drivers\STM32G0xx_HAL_Driver\Inc", "-I$project\Drivers\STM32G0xx_HAL_Driver\Inc\Legacy", "-I$project\Drivers\CMSIS\Device\ST\STM32G0xx\Include", "-I$project\Drivers\CMSIS\Include")
$common = @('-mcpu=cortex-m0plus','-mthumb','-Os','-g3','-ffunction-sections','-fdata-sections','-fno-common','-Wall','-Wextra','-DUSE_HAL_DRIVER','-DSTM32G031xx','-DMAG_FORCE_CALIBRATION_PROFILE=8') + $includes
if ($EnableSpi) {
  # GNU Arm 12.2 crashes while emitting -g3 macro debug info for the SPI
  # conditional app.c; optimized machine code compiles cleanly with -g0.
  $common += @('-DAPP_ENABLE_SPI=1', '-g0', '-fstack-usage', "-I$project\..\shared")
}
if ($SpiDiagnostics) {
  $common += @('-DAPP_SPI_DIAGNOSTICS=1', '-DSPI_LINK_DIAGNOSTICS=1')
}
if ($SpiAudit) {
  $common += '-DAPP_SPI_AUDIT=1'
}
if ($SpiRxIrqAudit) {
  $common += '-DSPI_LINK_RX_IRQ=1'
}

$sources = @(
  'Core\Src\mlx90393.c','Core\Src\reprintf.c','Core\Src\log.c','Core\Src\matrix.c',
  'Core\Src\main.c','Core\Src\gpio.c','Core\Src\dma.c','Core\Src\i2c.c','Core\Src\iwdg.c',
  'Core\Src\tim.c','Core\Src\usart.c','Core\Src\stm32g0xx_it.c','Core\Src\stm32g0xx_hal_msp.c',
  'Core\Src\modbus_slave.c','Core\Src\regs.c','Core\Src\rs485.c','Core\Src\app.c',
  'Core\Src\mag_force.c','Core\Src\flash_config.c','Core\Src\iap_control.c','Core\Src\syscalls.c',
  'Core\Src\system_stm32g0xx.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_i2c.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_i2c_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_rcc.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_rcc_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_ll_rcc.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_flash.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_flash_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_gpio.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_dma.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_dma_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_pwr.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_pwr_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_cortex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_exti.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_iwdg.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_tim.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_tim_ex.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_uart.c',
  'Drivers\STM32G0xx_HAL_Driver\Src\stm32g0xx_hal_uart_ex.c'
)

$objects = @()
if ($EnableSpi) {
  $sources += @('Core\Src\app_spi.c', '..\shared\spi_link.c')
}
foreach ($source in $sources) {
  $objectName = ($source -replace '[\\/:]','_') -replace '\.c$','.o'
  $object = Join-Path $build $objectName
  & $gcc @common -c (Join-Path $project $source) -o $object
  if ($LASTEXITCODE -ne 0) { throw "compile failed: $source" }
  $objects += $object
}
$startup = Join-Path $build 'startup.o'
& $gcc @common -x assembler-with-cpp -c "$project\Drivers\CMSIS\Device\ST\STM32G0xx\Source\Templates\gcc\startup_stm32g031xx.s" -o $startup
$objects += $startup

$elf = Join-Path $build "$imageName.elf"
& $gcc '-mcpu=cortex-m0plus' '-mthumb' '-Wl,--gc-sections' "-T$project\application.ld" '--specs=nano.specs' @objects '-lm' "-Wl,-Map=$build\$imageName.map" -o $elf
if ($LASTEXITCODE -ne 0) { throw 'link failed' }
& $objcopy -O ihex $elf "$build\$imageName.hex"
if ($LASTEXITCODE -ne 0) { throw 'HEX export failed' }
& $objcopy -O binary $elf "$build\$imageName.bin"
if ($LASTEXITCODE -ne 0) { throw 'BIN export failed' }
& $size $elf

param([switch]$EnableSpi)

$ErrorActionPreference = 'Stop'
$project = Split-Path -Parent $MyInvocation.MyCommand.Path
$app = Join-Path (Split-Path -Parent $project) 'application'
$tool = 'C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin'
$gcc = Join-Path $tool 'arm-none-eabi-gcc.exe'
$objcopy = Join-Path $tool 'arm-none-eabi-objcopy.exe'
$size = Join-Path $tool 'arm-none-eabi-size.exe'
$build = Join-Path $project $(if ($EnableSpi) { 'build-spi' } else { 'build' })
$outputName = if ($EnableSpi) { 'TacAI_H3_Dual_SPI_UART_Bootloader' } else { 'TacAI_H3_UART_Bootloader' }
New-Item -ItemType Directory -Force -Path $build | Out-Null

$common = @('-mcpu=cortex-m0plus','-mthumb','-Os','-ffunction-sections','-fdata-sections','-fno-common','-Wall','-Wextra','-Werror','-DSTM32G031xx',"-I$project\include", "-I$app\Drivers\CMSIS\Include", "-I$app\Drivers\CMSIS\Device\ST\STM32G0xx\Include")
if ($EnableSpi) {
    $common += @('-DTACAI_ENABLE_SPI', "-I$project\..\shared")
}
& $gcc @common -c "$project\src\main.c" -o "$build\main.o"
if ($LASTEXITCODE -ne 0) { throw 'bootloader main compilation failed' }
& $gcc @common -c "$project\src\system_boot.c" -o "$build\system.o"
if ($LASTEXITCODE -ne 0) { throw 'bootloader system compilation failed' }
& $gcc @common -x assembler-with-cpp -c "$app\Drivers\CMSIS\Device\ST\STM32G0xx\Source\Templates\gcc\startup_stm32g031xx.s" -o "$build\startup.o"
if ($LASTEXITCODE -ne 0) { throw 'bootloader startup compilation failed' }
$objects = @("$build\startup.o", "$build\system.o", "$build\main.o")
if ($EnableSpi) {
    & $gcc @common -c "$project\..\shared\spi_link.c" -o "$build\spi_link.o"
    if ($LASTEXITCODE -ne 0) { throw 'SPI link compilation failed' }
    $objects += "$build\spi_link.o"
}
& $gcc '-mcpu=cortex-m0plus' '-mthumb' '-nostartfiles' '-Wl,--gc-sections' '-Wl,--fatal-warnings' "-T$project\bootloader.ld" '--specs=nano.specs' '--specs=nosys.specs' "-Wl,-Map=$build\$outputName.map" @objects -o "$build\$outputName.elf"
if ($LASTEXITCODE -ne 0) { throw 'bootloader link failed' }
& $objcopy -O ihex "$build\$outputName.elf" "$build\$outputName.hex"
if ($LASTEXITCODE -ne 0) { throw 'bootloader HEX generation failed' }
& $objcopy -O binary "$build\$outputName.elf" "$build\$outputName.bin"
if ($LASTEXITCODE -ne 0) { throw 'bootloader BIN generation failed' }
& $size "$build\$outputName.elf"

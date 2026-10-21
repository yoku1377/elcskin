# TacAI H3.0 CH342F UART firmware upgrade guide

## Important boundary

The CH342F board exposes only 3.3 V, TXD, RXD, and GND. It has no RTS/DTR connection to STM32 BOOT0/NRST. PB6/PB7 are not supported as a UART pair by the STM32G031 ROM bootloader, so this delivery uses a custom bootloader at 0x08000000.

## First deployment with ST-Link

1. Back up any device-specific configuration if it must be retained.
2. Use `release/TacAI_H3_FirstDeploy_STLink_preserve_config_v1.bin`.
3. Program it at address `0x08000000`. The file ends at the application image and does not contain the metadata or configuration pages.
4. Do not enable readout protection or write protection until the bootloader/application jump and UART upgrade have been verified on the actual board.
5. Power-cycle, connect the v5.0.1 monitor, perform RAW tare, and verify normal RAW/FORCE streaming.

Before any force calibration, complete the H3.0-specific 8/8 sensor audit: verify every MLX90393 responds, press each physical point independently to establish the channel map, and record X/Y/Z RAW polarity. The Leju V3/NO8 channel/model assumptions must not be accepted from CAD appearance alone.

## Subsequent single-USB upgrade

1. Disconnect the live monitor from the serial port.
2. Open **固件升级（UART）**.
3. Select `release/TacAI_H3_App_v1.tacai`.
4. Check model `H3G031G8`, address `0x08002000`, image length, version, and CRC32 shown in the window.
5. Type `升级 H3G031G8` and accept the final confirmation.
6. Do not unplug during the transfer. If power is lost after erase begins, reconnect and send the same complete image again.

The confirmation field must contain the exact text `升级 H3G031G8`. v5.0.1 keeps the start button clickable after a valid image is selected; if the confirmation text is missing or wrong, it shows an explicit warning instead of silently doing nothing.

The PC and bootloader both validate model, address, size, header/frame CRC32, application CRC32, and vector table. The bootloader writes a `DOWNLOADING` marker before erasing application pages and writes `VALID` only after full verification.

## Recovery

- If the application is valid, the PC sends an exact ASCII entry command and the application resets into the bootloader through a reserved SRAM word.
- If the application is invalid or a previous download was interrupted, the bootloader waits indefinitely for a new image.
- If an older application does not understand the entry command, first deployment still requires ST-Link. A power-cycle during the upgrader's synchronization window can catch the bootloader only after this custom bootloader has already been installed.
- The design does not provide automatic rollback because the 64 KiB MCU cannot hold two complete ~39-43 KiB applications plus bootloader and configuration.

## Hardware verification still required

- CH342F TX/RX crossing and 3.3 V logic levels.
- Bootloader 2 s startup window and application-to-bootloader reset path.
- Erase/program timing and watchdog/option-byte behavior on the actual MCU.
- Power interruption at metadata write, first application erase, middle block, final block, and metadata VALID write.
- At least 20 consecutive upgrades, including forced cable disconnects, before production release.

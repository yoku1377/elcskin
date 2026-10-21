# TacAI H3.0 UART-IAP and resultant-force architecture

This delivery is intentionally separate from the existing H2.2 firmware and product monitor v4.5.

## Leju V3 / NO8 provenance boundary

- The reusable baseline comes from the Leju Heiman V3 NO8 project: STM32G031 low-level startup/HAL, two-TMUX1204 matrix scanning, eight MLX90393 acquisition, and the existing RAW/UART frame format.
- H3.0 remains a separate firmware target and device identity. Its bootloader, relocated application and release images do not overwrite or replace the existing NO8 target.
- The NO8 four-region `softfusion_v3` coefficients are bring-up references only. They are not H3.0 calibration coefficients and cannot support H3.0 accuracy, 0.01 N recovery or EMI claims.
- Similar-looking eight-point CAD geometry does not prove identical magnet paths, channel order, polarity or mechanical transfer. Those properties must be measured on the H3.0 hardware.

## Read-only audit result

- H3.0 MCU: STM32G031G8U6, 64 KiB Flash and 8 KiB SRAM.
- H3.0 application UART: USART1 on PB6/PB7, connected to the CH342F TXD/RXD pair.
- CH342F RTS/DTR are not routed to BOOT0 or NRST.
- STM32G031 system-memory ROM bootloader UART pins are PA9/PA10 (PA11/PA12 on this package) or PA2/PA3. PB6/PB7 are used by its I2C bootloader, not its UART bootloader.
- Existing firmware starts at 0x08000000 and uses 0x0800F800-0x0800FFFF as its configuration page. Its existing map reports 43.12 KiB ROM.
- Existing NO8 firmware explicitly outputs Fx=0 and Fy=0 because only positive-Z loading was calibrated. Those fields are not calibrated three-axis forces and must not be used in a vector magnitude.

## Flash layout for H3.0 v1

| Range | Size | Purpose |
|---|---:|---|
| 0x08000000-0x08001FFF | 8 KiB | immutable UART bootloader |
| 0x08002000-0x0800EFFF | 52 KiB | application image |
| 0x0800F000-0x0800F7FF | 2 KiB | IAP state and validated-image metadata |
| 0x0800F800-0x0800FFFF | 2 KiB | existing device configuration |

The bootloader never erases its own 8 KiB or the device configuration page.

## Safety model and unavoidable limitation

The 64 KiB device cannot hold the bootloader, the current ~43 KiB application, the configuration page, and a second complete application slot. This design therefore provides recoverability, not A/B rollback:

1. The PC validates the image container, model identifier, address, length, and CRC32 before enabling upgrade.
2. A second explicit confirmation is required in the UI.
3. The bootloader validates the same manifest and confirmation token before changing Flash.
4. The bootloader records `DOWNLOADING` in its own metadata page before erasing any application page.
5. Only sequential, CRC-protected blocks are accepted.
6. The application is marked `VALID` only after a full Flash CRC32 check and vector-table validation.
7. If power is lost after erase begins, the bootloader remains usable and waits for the complete image to be resent. The old application cannot be restored automatically.

## Force semantics

- `resultant_force_n`: scalar output of an independently fitted RAW-to-reference-resultant model.
- `firmware_fx_raw`, `firmware_fy_raw`, `firmware_fz_raw`: legacy firmware fields; not a calibrated vector for the curved H3.0 finger.
- `model_confidence`: coverage indicator, not an accuracy guarantee.
- `P00-P11`: display-only spatial allocation whose sum is normalized to the selected scalar resultant.

The copied NO8 model is retained only as a clearly labelled initial-check model. If silicone, magnet, sensor geometry, or finger curvature changes, a new H3.0 calibration dataset and model are mandatory before accuracy claims.

## Mandatory first-board sensor checks

Before collecting H3.0 calibration data, confirm all eight sensors respond, press one physical location at a time to build an unambiguous physical-location-to-channel map, and record the sign/polarity of all X/Y/Z RAW changes. Stop if any channel is missing, duplicated, swapped, inverted unexpectedly, saturated or strongly coupled to the wrong physical location.

# TacAI H3.0 monitor, calibration acquisition and firmware upgrade

This is a separate H3.0 delivery. It does not overwrite the existing v4.5 PC monitor or the original firmware project.

## Current build: v5.3.28 (2026-10-21)

The current PC source is `pc/TfgMonitorPython_H3_v5.0`. It includes UART/CH341/CH347 sensor acquisition, bounded SPI snapshot recovery, RAW baseline compensation, and ZP-500N continuous reference acquisition. The gauge's serial value is retained as received: the observed `0016.1` means 16.1 N, without an offset to match a disconnected display.

For model development, use the calibration dialog's automatic gauge reference with ZP-500N, COM13 and 2400 baud. A calibration session preserves the full sensor/gauge/event trajectory, loading and unloading phases, raw serial bytes, and the existing three-second plateau files. Gauge references are associated by host receipt time; they are not hardware-synchronized. Backlogged or stale measurements are excluded as fresh references. The supported gauge profile is the observed N-mode format; changing load, unloading and zero must be checked on the actual instrument.

Build instructions, external resource hashes and offline verification are in [docs/CURRENT_BUILD.md](docs/CURRENT_BUILD.md). EXEs, raw captures, logs and temporary builds are local artifacts outside Git. The 2026-10-21 rebuild preserves the v5.3.28 model and runtime algorithms.

## Historical local outputs

- `release/TacAI H3 Python Monitor v5.2.19 Candidate Region Trace IAP.exe`: historical PC candidate. Retains H3's eight estimated regions and NO7's twelve display regions. Each mean is now an Excel AVERAGE linked to copied frame values with original CSV row numbers, source-column mapping and SHA-256. Future captures append the spatial algorithm and noise-floor parameters. Statistical results and force algorithms are unchanged; these region allocations are not independently calibrated local forces. See `release/v5.2.19_八区计算与客户复核说明.txt`.
- `release/TacAI H3 Python Monitor v5.2.18 Candidate NO7 Means IAP.exe`: previous NO7 format compatibility candidate. All 720 NO7 means match the user's manual sheets within rounding. Retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.17 Candidate Folder Means IAP.exe`: previous per-folder template candidate. One CSV per row, arithmetic means per region, absolute deviations and 30 N FS percentages; retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.16 Candidate CSV Evaluation IAP.exe`: previous scalar recording report. Superseded by the per-region, per-folder template workflow in v5.2.17; retained for reproducing earlier import failures.
- `release/TacAI H3 Python Monitor v5.2.15 Candidate Finger Cloud IAP.exe`: previous visual candidate. It ports this project's H2.2 point-cloud geometry, colors, camera, and MC-Tac reference shell into the existing Qt OpenGL view, fed by the H3 eight-channel Fz display allocations. Front is at the top and rear at the bottom. The dots are interpolated display samples, not new measurements or physical displacement.
- `release/TacAI H3 Python Monitor v5.2.14 Candidate Thin Fz Arrows IAP.exe`: earlier thin-arrow visual candidate, retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.13 Candidate Integrated Labels IAP.exe`: earlier arrow-free membrane candidate with integrated channel labels, retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.12 Candidate Continuous Membrane IAP.exe`: earlier continuous-membrane candidate with separate channel dots, retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.11 Candidate Optical Membrane IAP.exe`: earlier GPU membrane candidate with raised arrows, retained for comparison.
- `release/TacAI H3 Python Monitor v5.2.10 Candidate Fz Legend IAP.exe`: clarifies that the arrows are +Z Fz magnitude indicators. The on-screen front/rear and S1–S8 mapping remains a software convention pending an eight-point physical audit. Earlier EXEs remain available.
- `release/TacAI H3 Python Monitor v5.2.4 Direction Safe IAP.exe`: earlier V3-3 Windows monitor. It replaces broad direction/classifier zeroing with a narrow known-release prototype gate, preserves a continuous low-confidence response for unseen directions, exposes the confidence warning, reorganizes the UI, and drives the 3D arrows with an independent approximately 60 Hz critically damped animation. Calibration, recording, consistency Excel, serial connection and safe firmware upgrade remain available.
- `release/TacAI H3 Python Monitor v5.2.3 Consistency Report IAP.exe`: v5.2.2 force model and safe firmware upgrade plus a built-in “一致性评价 Excel” workflow. It recursively imports calibration CSV sessions and generates editable-threshold accuracy, low-force, repeatability, hysteresis, unload-residual, inter-device consistency, pass/fail and ranking sheets without a separate Python or Node.js installation.
- `release/TacAI H3 Python Monitor v5.2.2 Low Force Release IAP.exe`: 2026-10-08 V3-3 validation build. It suppresses the recorded unload residual only when the independent load classifier is negative, applies front/middle/rear low-force lookup correction through 5 N, fades the correction out from 5 N to 10 N, leaves loads above 10 N unchanged, and retains the safe UART firmware-upgrade dialog.
- `release/TacAI H3 Python Monitor v5.2.1 Soft Gate IAP.exe`: V3-3 post-power-cycle test build.  It keeps the existing calibrated regression, replaces the brittle hard loaded/unloaded cut-off with a four-round-bounded soft gate, records the classifier score in new calibration captures, and retains the safe UART firmware-upgrade dialog.
- `release/TacAI H3 Python Monitor v5.0.1 Resultant IAP.exe`: Windows monitor and UART upgrader. v5.0.1 fixes the invisible confirmation/progress text and silent disabled-upgrade-button behavior.
- `release/TacAI_H3_FirstDeploy_STLink_preserve_config_v1.bin`: first deployment at `0x08000000`; it contains the immutable UART bootloader and H3 application, and stops before the metadata/configuration pages.
- `release/TacAI_H3_App_v1.tacai`: subsequent application update through the existing CH342F USB-UART connection.
- `release/manifest.json`: exact sizes, CRC32 and SHA-256 values.

Read `docs/UART_IAP_UPGRADE_GUIDE.md` before the first physical-board operation.

See `docs/VERSION_RECORDS.md` for candidate evidence, known limits and the append-only issue template.

## Force meaning

The main displayed force is designed as one scalar resultant fitted directly from the 24 RAW magnetic channels to the scalar force-gauge reading. This matches the practical fixture: apply and measure the convenient axial load, without pretending that unmeasured Fx/Fy references exist. It is not `sqrt(Fx^2 + Fy^2 + Fz^2)`.

The current v5.2.4 model is marked `H3_V3_3_FZ_V4_DIRECTION_SAFE_PENDING_FIXED_ANGLE_VALIDATION`. Existing force-gauge data supports normal loading from 0.2 N through 30 N on V3-3. The release-prototype zero gate and the direction-invariant fallback improve continuity, but do not establish oblique-load accuracy, accuracy below 0.2 N, cross-device consistency, independent Fx/Fy calibration, or the 0.01 N recovery requirement.

Hard recovery/EMI gates are built into the monitor: rapid-unload peak `<= 1.5 N`, final 10 s maximum unloaded residual `<= 0.01 N`, EMI steady shift `<= 0.01 N`, EMI transient `<= 1.5 N`, no quality fault and no paired-frame gap above 1.0 s.

The software deliberately remains ineligible for final recovery/EMI acceptance until the model status is `FINAL_H3_VALIDATED_NO_DEADBAND_0P01N`. A software-forced zero or a replay on previously collected data must not be treated as proof of mechanical recovery.

## Verification boundary

As rechecked on 2026-10-09, the root protocol/release suite passes 9/9, the current PC suite passes 34/34, and the packaged v5.2.4 EXE returns exit code 0 from `--self-test`. The consistency workbook also passes generation, readback, formula-error scanning and seven-sheet rendering. Physical STM32 Flash timing, CH342F wiring, power-interruption recovery, fixed-angle force accuracy, 0.01 N recovery and electromagnetic immunity still require the hardware checklists.

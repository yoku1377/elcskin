# Build and verification record

## Firmware build

- Toolchain: Arm GNU Toolchain 12.2.MPACBTI-Rel1.
- `firmware/bootloader/build.ps1` builds the 0x08000000 bootloader and enforces an 8 KiB linker limit.
- `firmware/application/build.ps1` builds the 0x08002000 application and enforces the 52 KiB application partition.
- `tools/tacai_image.py` validates the application vector table, creates the `.tacai` container, and creates the first-deployment ST-Link BIN without targeting the final two Flash pages.

Current build sizes and checksums are stored in `release/manifest.json`.

Firmware build recorded for the original delivery:

- bootloader: 2,820 bytes binary (`text=2816`, `data=4`, `bss=1324`), within the 8 KiB partition;
- application: 39,236 bytes binary (`text=39116`, `data=120`, `bss=6008`), within the 52 KiB partition;
- first-deployment BIN: 47,428 bytes from `0x08000000`, ending before metadata and configuration;
- packaged PC EXE v5.0.1: 55,473,315 bytes.

Current PC release checked on 2026-10-09:

- `release/TacAI H3 Python Monitor v5.2.4 Direction Safe IAP.exe`: 74,115,307 bytes;
- SHA-256: `36c009955c1781204826ff85095470dfd868736b113b0101e5d82e65f6d20a38`;
- packaged `--self-test`: exit code 0.

Candidate PC release built on 2026-10-09:

- `release/TacAI H3 Python Monitor v5.2.5 Candidate Spatial IAP.exe`: 74,113,764 bytes;
- SHA-256: `39c84254341e3b2f5ff07b31b84704c4ba55835ade5121475f11a1d8c17f7fef`;
- packaged `--self-test`: actual process exit code 0;
- PC test suite: 39/39; repository protocol/release suite: 9/9;
- this candidate is a PC-only model/UI update, not a new `.tacai` image. The legacy v5.2.4 executable remains untouched.

UI-only follow-up candidate built on 2026-10-09:

- `release/TacAI H3 Python Monitor v5.2.6 Candidate 3D Deck IAP.exe`: 74,116,629 bytes;
- SHA-256: `92071bb71d6cbaf02c3eb1a83bc866e1b562bb88970119e7612fbbf98b4fc164`;
- packaged `--self-test`: actual process exit code 0;
- PC test suite: 40/40; repository protocol/release suite: 9/9;
- native 1500×950 and 1100×760 zero-load and synthetic-load visual checks passed; the 12 N screenshots are interface fixtures, not hardware measurements;
- v5.2.5 model and all MCU images are unchanged; no serial or Flash operation was performed.

Matte visual follow-up candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.7 Candidate Matte Deck IAP.exe`: 74,114,076 bytes;
- SHA-256: `7a04a331e9e8e2fd0ec43020a33d64c687af8e1d9c4c9b1e5a0eb421a60af4b1`;
- packaged `--self-test`: actual process exit code 0;
- PC test suite: 41/41; repository protocol/release suite: 9/9;
- native 1500×950 and 1100×760 synthetic-load visual checks passed; no actual device measurement or firmware change was made.

3D antialiasing follow-up candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.8 Candidate 3D MSAA IAP.exe`: 56,049,928 bytes;
- SHA-256: `8c52c66f2e6a613d18ca9c3a9a1ac7bc47f6ca429b10634fb52f65c73046b624`;
- packaged `--self-test`: actual process exit code 0; PC test suite: 41/41; repository protocol/release suite: 9/9;
- retained the v5.2.7 3D interface; on the local RTX 4060, its bound OpenGL framebuffer reports `GL_SAMPLE_BUFFERS=1`, `GL_SAMPLES=4` versus the pre-change `0/0`;
- this is a PC-only rendering update. No MCU image, force model, UART protocol, or physical-device acceptance result changed.

Continuous-membrane visual candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.9 Candidate Cinematic Membrane IAP.exe`: 56,053,342 bytes;
- SHA-256: `e61cd869a9302ddeccac443b47358bb949d410470256e85445d6370465efbce3`;
- packaged `--self-test`: actual process exit code 0; PC test suite: 43/43; repository suite: 9/9;
- native source-window previews at 1500×950 and 1100×760 passed idle and synthetic 35 N arrow-fit checks; synthetic frames are not hardware measurements, and packaged `--self-test` constructs the UI but does not show the OpenGL window;
- this is a PC-only visual update. Serial acquisition, model coefficients, calibration, IAP implementation and MCU images are unchanged; real-device acceptance remains pending.

Fz legend candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.10 Candidate Fz Legend IAP.exe`: 56,054,414 bytes;
- SHA-256: `1646d410dd03dfe235215c43cf7e91caf51d3374fcd851201ba831486bcd42cf`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0; native 1500×950 idle preview passed;
- UI copy clarifies the fixed +Z Fz magnitude glyph and pending physical channel/orientation audit. Force logic, UART and firmware remain unchanged.

Optical-membrane candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.11 Candidate Optical Membrane IAP.exe`: 56,057,333 bytes;
- SHA-256: `3d51c4e696d57ad33d9913b272e93a137d33e00c972979e3e661d723b332cec4`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0;
- native OpenGL previews passed for idle, synthetic two-point load and narrow panel; the integrated 1500×950 idle window and the packaged EXE's actual idle OpenGL window were also inspected. These are visual fixtures, not real-device measurements;
- only the 3D rendering layer changed. Real-device visual performance, channel orientation and force acceptance remain pending.

Continuous-membrane candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.12 Candidate Continuous Membrane IAP.exe`: 56,052,049 bytes;
- SHA-256: `a5afd736018367a5397fc430e992d846a1387a4832e6de5919191440c00590bd`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0;
- native OpenGL previews passed for idle, synthetic two-point load, narrow panel and the integrated 1500×950 window; the packaged EXE's actual idle OpenGL window was also inspected. Synthetic frames are display fixtures, not device measurements;
- arrows, pillar halos, target rings and crosshair guides were removed. The surface itself now uses a shallow visual dimple and one-sided grazing highlight. Force calculation, acquisition, calibration, IAP, and firmware remain unchanged; physical channel mapping and real-device acceptance remain pending.

Integrated-label candidate built on 2026-10-10:

- `release/TacAI H3 Python Monitor v5.2.13 Candidate Integrated Labels IAP.exe`: 56,051,478 bytes;
- SHA-256: `5e6568fdec49f7dcec351281702d6b91e7fd06a826498fd7109a7fbf2fc4eed9`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0;
- native OpenGL previews passed for idle, synthetic two-point load, narrow panel and the integrated 1500×950 window; the packaged EXE's actual idle OpenGL window was also inspected. Synthetic frames are display fixtures, not device measurements;
- separate channel dots were removed; labels now mark their own locations and change color for active/peak response. Force calculation, acquisition, calibration, IAP, and firmware remain unchanged; physical channel mapping and real-device acceptance remain pending.

Finger-cloud candidate built on 2026-10-12:

- `release/TacAI H3 Python Monitor v5.2.15 Candidate Finger Cloud IAP.exe`: 56,225,237 bytes;
- SHA-256: `39989b007bc2ada13587c15911bcc80127bd08faec4e895dddb3ede131a1d4e1`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0;
- native OpenGL previews passed for idle, synthetic two-point load, narrow panel, synthetic 35 N upper range, and the integrated 1500×950 window; the packaged EXE's actual idle OpenGL window was inspected. Synthetic frames are display fixtures, not device measurements;
- the rendering ports the existing H2.2 point-cloud shape, 34×72 samples, HSL color ramp, visual lift, oblique camera and permissively licensed MC-Tac shell into Qt OpenGL. Points are interpolated from eight display allocations and do not represent new sensors, physical displacement, or a PaXini UI replica. Force calculation, acquisition, calibration, IAP, and MCU firmware are unchanged; real-device acceptance remains pending.

Thin-arrow candidate built on 2026-10-12:

- `release/TacAI H3 Python Monitor v5.2.14 Candidate Thin Fz Arrows IAP.exe`: 56,058,515 bytes;
- SHA-256: `92cb3558f0a20408d46727fce55ad00f0781743ab176a68a296bce9420e3eed7`;
- PC test suite: 43/43; repository suite: 9/9; source and packaged `--self-test`: exit 0;
- native OpenGL previews passed for idle, synthetic two-point load, narrow panel, synthetic 35 N upper range and integrated 1500×950 window; the packaged EXE's actual idle OpenGL window was inspected. Synthetic frames are display fixtures, not device measurements;
- open-tip fixed-axis arrows visualize only Fz allocation magnitude. Membrane light remains a location cue. This is a temporary rendering choice and has not been identified as PaXini's specific visual style. Force calculation, acquisition, calibration, IAP, and firmware remain unchanged; physical channel mapping and real-device acceptance remain pending.

## Host-side verification

Run from the repository root:

```powershell
C:\Users\yoku\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe -m unittest discover -s tests -v
```

Run UI tests in an ASCII-path virtual environment. Set the plugin path before starting Qt:

```powershell
$env:QT_QPA_PLATFORM = 'offscreen'
$env:QT_QPA_PLATFORM_PLUGIN_PATH = '<venv>\Lib\site-packages\PyQt5\Qt5\plugins\platforms'
<venv>\Scripts\python.exe -m unittest discover -s pc\TfgMonitorPython_H3_v5.0 -p 'test_*.py' -v
```

Verification results rechecked on 2026-10-09:

- 9/9 release-image, IAP recovery-simulation and test/acceptance-protocol unit tests passed;
- 34/34 PC tests passed, including force-model gating/fallback, 60 Hz arrow response, monitor state/recording/timeout, consistency reporting and firmware-upgrade feedback;
- the v5.2.4 one-file EXE passed `--self-test` in a hidden native-Windows Qt process with exit code 0;
- the generated consistency workbook imported successfully, rendered all seven worksheets and produced no formula-error matches. Its source contained one device, so this proves report generation, not inter-device consistency;
- the packaged v5.2.4 text note says 33 tests. This is a historical documentation mismatch; the current discovered PC suite contains 34 tests;
- `qwindows.dll` is discovered before pyqtgraph imports Qt, with a PyInstaller runtime hook as a second guard.

Historical replay evidence and exact source-file hashes are recorded in `docs/VERSION_RECORDS.md`. The 2026-10-08 three-round replay has zero false-cleared loaded frames; the 2026-09-30 three-round replay still false-clears 18 frames at 0.2 N. Neither replay establishes the 0.01 N hardware requirement.

The PyInstaller environment must use an ASCII-only path. Qt reports a corrupted plugin path to PyInstaller when the virtual environment itself is under the current Chinese workspace path.

Host tests cannot prove Flash electrical behavior, reset timing, UART wiring, or power-loss recovery on physical hardware. Those items remain explicitly pending in the upgrade guide.

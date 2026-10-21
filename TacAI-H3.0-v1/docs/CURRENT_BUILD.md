# 从当前源码构建 v5.3.28

`tools/build_current_monitor.py` 直接冻结仓库中 `pc/TfgMonitorPython_H3_v5.0` 的当前源码。它不读取历史发布包、旧构建清单或 `tmp/outputs` 中的基线，也不修改版本、零点、倍率或模型参数。日期仅用于区分本次构建，软件版本仍为 `5.3.28-zp500n-stream`。

## 环境与外部资源

使用 Windows、Python 3.12 的独立构建虚拟环境。按 PC 目录中的 `requirements.txt` 安装依赖；图像导出还需要 Pillow。构建过程不会下载或安装包。两项生产 C 状态注入测试需要主机 C 编译器：支持 Visual Studio 2022 Build Tools 的 `cl.exe`，或 PATH 中的 gcc/clang。测试只编译并运行主机桩程序，不连接 STM32。

必须显式传入 `--resources`，目录内包含以下三个原始文件。允许指向已验证的资源保管目录；工具只复制这三项，不把该目录作为源码基线。任一文件缺失或 SHA256 不符就停止，不会从网络补齐。

| 文件 | SHA256 |
| --- | --- |
| `CH347DLLA64.dll` | `00ae967ac6a21253cc53e32085423807c7817198d9328b17b936ed8ca075f375` |
| `CH341DLLA64.DLL` | `0eeed57d53f0ff9b6daaaab87207ff90c2e03762441aba5ed0904b3f7d5f4737` |
| `TacAI_H3_App_SPI_rxirq_v5.tacai` | `c5e09fd9a30a4da4916a774da45c7b94ee9ba88f511b7c7a2b04b90b4f944d42` |

当前 PC 目录缺少其中部分文件是允许的，三项均以显式资源目录中的校验结果为准。DLL 和应用镜像仅打包进 EXE，构建工具不加载 DLL、不发送命令、不擦写固件。

## 构建命令

在仓库根目录执行，按机器环境修改 Python、资源和可选离线依赖目录：

```powershell
$buildPython = 'C:\Users\yoku\AppData\Local\TacAI-H3-build-venv\Scripts\python.exe'
$resourceDir = 'D:\H3-build-resources'
$offlinePackages = 'C:\Users\yoku\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\Lib\site-packages'
& $buildPython -B .\tools\build_current_monitor.py `
    --resources $resourceDir `
    --date 20261021 `
    --dependency-dir $offlinePackages
if ($LASTEXITCODE -ne 0) { throw '构建或离线验证失败，请查看 outputs 下的日志' }
```

构建环境已经能够导入 `openpyxl`、`et_xmlfile`、`PIL` 时可省略 `--dependency-dir`。否则该参数只用于把缺少的这三个包及对应包元数据复制到本次 `isolateddeps`；不会把整个外部 site-packages 加入搜索路径，也不修改虚拟环境。它可以重复指定。离线包必须兼容所选 Python 及 Windows 平台。

CI 使用相同命令和 Windows Python 环境，由外部只读资源目录提供三个文件。不要通过发布历史中的 ZIP 或旧 manifest 拼装源码。构建日期对应的工作目录或 EXE 目录已存在时工具拒绝覆盖；失败后先保留日志并核对原因，再使用新的日期或另行归档对应失败目录，工具不会自动删除它们。

## 输入、测试和输出

`source_files()` 是构建与独立验证共同使用的输入选择规则：递归保留 `.py`、`.json`，保留 `requirements.txt` 及 `assets/`、`testdata/`、`models/` 数据树；仅保留上表三个 DLL/镜像资源。排除缓存、logs、build、dist、旧 `.spec`、预览截图和其他旧固件。所有选中文件的 SHA256 及来源写入 manifest，五份模型 profile 按原字节复制。

工具先运行显式列出的 43 个当前回归模块，包括模拟串口/USB、真实字节回放、标定窗口、SPI ACK 排空及升级异常测试，再执行仓库 `tests/test_spi_snapshot_stale_firmware.py` 中两项生产 C 状态注入测试。测试数量和跳过数从真实日志解析并记录；部分旧外部数据夹具缺失时当前通常有 8 项跳过，不把跳过视为硬件通过。生产 C 测试的两组 PASS 标记必须出现，不能因缺编译器而跳过构建门禁。

测试在冻结源码中运行，GUI 设为 `offscreen`，日志写入本次工作目录。PyInstaller 参数保持 v28 的 onefile/windowed、Qt runtime hook、openpyxl 和资源配置。构建不启动生成的 GUI，不打开任何本机 COM、CH341/CH347、J-Link 或传感器。

以 `--date 20261021` 为例：

- 冻结源码及构建临时文件：`tmp/current_monitor_20261021_build/`。
- 唯一交付文件：`release/TacAI_H3_v5_3_28_ZP500NStream_20261021/TacAI H3 v5.3.28 ZP500N Stream.exe`。
- 验证材料：`outputs/current_monitor_20261021/manifest.json`、`dependencies.json`、`offline_tests.log`、`firmware_state_repro.log`、`build.log`。

发布目录仅放一个 EXE，不生成 ZIP。EXE、DLL、外部镜像、构建目录和现场日志不提交 Git；提交可维护源码、测试、脚本及文档。独立验证器应检查 EXE SHA256、嵌入代码和资源与冻结源码一致；如需 EXE 启动自检，应单独使用无硬件的 `--self-test --self-test-json` 并保存记录，构建脚本本身不运行 EXE。

## 测力计边界

本版按串口原始 `0016.1` 解码为 `16.1 N`，不会为迎合屏幕约 17 N 的描述增加偏移或倍率。已确认的线格式为 ZP-500N 的 N 单位、六字节正数；负值、非 N 单位及其他精度仍不支持，遇到不符的格式停止参考输入并保留原始证据。

测力计和传感器的关联基于主机接收时间，仪器没有提供独立源时间戳。积压数据保留为诊断，不补造历史时间，也不作为刚采到的标定参考。离线算术/分帧回放通过不等于现场全量程计量验证；用户反馈的载荷回落另行保留为现场观察。

## 收集下一轮模型调优数据

本次只构建稳定现版，模型和标定参数不调整。使用“标定采集”窗口保留测力计真实读数，供后续独立拟合与复核：

1. 保持传感器和测力计为同一套装配，连接传感器并确认持续采集。完全卸载、静置后完成传感器空载置零并保留至少2秒空载数据，再打开“标定采集”。填写唯一设备编号、装配/批次和输出目录；不同传感器分别建立任务，不混用零点或数据。
2. 参考方式选“测力计串口自动读取”，协议选“ZP-500N 原程序协议（2400）”，选择实际测力计串口（现场为 `COM13`），连接测力计。该模式自动使用 2400、8N1；确认读数随加载、卸载变化，保持连接。
3. 选择所需方案，完全卸载后点击“创建并开始采集”。采集中不允许重新置零。每个位置按计划完成加载和卸载，目标值只是操作指引，以测力计实际读数为参考。载荷稳定后点击“载荷稳定，采样3秒”；步骤之间的变化和保持也会连续记录。
4. 每个最终零载点可先保持30～60秒，再点击3秒采样，保留回落和残余变化。出现参考过期、串口积压或通信恢复提示时，确认当前载荷稳定后手动重采当前点；不要把断档前后拼成一个3秒窗口。
5. 完成后“结束并保留数据”，保留整个任务生成的同名前缀文件，供调优使用。出现异常时另保留运行日志，其中包含传感器断连的具体原因。不要只交均值或截图。

每个任务的独立 UUID 防止覆盖，产物包括：

- `H3_trajectory_<设备>_<UUID>_sensor.csv`：会话全程的24通道 RAW、有效零点差值、模型读数、步骤/加载卸载阶段、主机接收时间及设备毫秒。
- 同前缀 `_gauge.csv`：每次有效测力计数值事件、实际 N、原始字符串/字节和接收时间；积压、被拒绝数据及状态仍留在 `_events.csv` 的协议事件中。
- 同前缀 `_events.csv`：开始、步骤、稳定采样、间断/恢复、参考失效、停止及原始协议证据。
- `H3_calibration_<设备>_<时间>_<UUID>_frames.csv` 和 `_summary.csv`：原有每点完整3秒帧及均值/质量，格式不变。

手填目标或读数不会伪装成连续测力计真值。轨迹只关联该传感器帧之前、最多1秒内到达的有效测力计事件；未来、过期和积压参考不作为真值。两路记录采用主机接收时间关联，不能宣称硬件同步，也不据名义10Hz补造源时间。

轨迹内的位置、加载/卸载方向和轮次是计划步骤标签；步骤之间的过渡记录不代表仪器实测的物理加载状态。零点数组及 epoch 随帧保存，会话开始前那次置零的发生时间和20帧窗口不在本会话文件内。

当前已确认的 ZP-500N 串口格式只有一位小数 N，即 0.1 N 步进。完整方案中的 0.01/0.02/0.05 N 等目标点可保留传感器轨迹，但这台仪器的当前输出不能提供对应分辨率的真实力参考；不要据此作 0.01 N 精度或回零验收。需要这些低力点的计量依据时，应另用经核对的更高分辨率参考仪器及协议。

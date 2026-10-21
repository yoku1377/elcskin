# NO8 HEX 首次串口烧录说明

本流程使用 STM32G031 芯片内部固化的 ROM Bootloader，通过现有 CH342F 的第二路 UART 写入完整 NO8 HEX，不需要 ST-Link。

## 交付文件

- `TacAI H3 Python Monitor v5.0.2 NO8 HEX UART.exe`：已将烧录功能融合进上位机；顶部按钮为“烧录 NO8 HEX（UART）”。HEX 已内置，打开烧录页后会自动加载并校验。
- `TacAI NO8 HEX UART Flasher v1.0.exe`：独立 Windows 烧录器。
- `TacAI_NO8_4region_softfusion_v3.hex`：待烧录的旧 NO8 完整固件。

该 HEX 的有效地址为 `0x08000000–0x0800AC7F`，有效数据 44,160 字节。烧录器仅允许 STM32G03/G04（PID `0x0466`）及 64 KiB 内部 Flash 范围，烧录后会逐字节回读校验。

## CH342F 第二路接口

根据 USB-UART 原理图，P2 为第二路 UART：

| P2 引脚 | 信号 | 连接方向 |
|---|---|---|
| 1 | 3.3 V 输出 | 仅在确认主板供电方式后使用 |
| 2 | USART2_RX / PA3 | CH342 TXD1 → STM32 RX |
| 3 | USART2_TX / PA2 | STM32 TX → CH342 RXD1 |
| 4 | GND | 必须共地 |

当前电脑上第二路通常枚举为 COM4，但必须以设备管理器或拔插变化为准。

如果主控板已经独立供电，不要直接并接 P2 的 3.3 V。此时只连接 RX、TX、GND。

## 进入 STM32 ROM Bootloader

1. 关闭监视器、串口助手等所有占用 COM 口的软件。
2. 将 CH342 P2 连接到主控 USART2 的 PA2、PA3 和 GND。
3. 确认主控板上的 BOOT0/PA14 测试点。不要短接未经确认的焊盘。
4. 通过合适的上拉电阻将 BOOT0 保持为高电平（3.3 V）。禁止接 5 V。
5. 在 BOOT0 保持高电平时，重新给主控上电或执行一次硬件复位。
6. 打开烧录器，选择第二路 COM 口和交付的 NO8 HEX。
7. 输入确认口令 `烧录 NO8`，点击“开始擦除并烧录”。
8. 工具显示“烧录及校验完成”后断电，将 BOOT0 恢复为低电平，再重新上电。

正常启动后，旧 NO8 固件从 USART1（PB6/PB7，对应原来的 COM3）以 115200、8N1 输出数据。

在融合版上位机中，先断开当前监视串口，再点击顶部的“烧录 NO8 HEX（UART）”。烧录窗口独立选择第二路串口，默认优先选择 COM4；烧录完成并恢复普通启动后，再回到主界面连接 COM3。

## 失败判断

- `无法同步 STM32 ROM Bootloader`：主控没有进入 BOOT 模式、选错 COM 口、P2 没有接到 PA2/PA3，或 TX/RX/GND 接线有误。
- `芯片型号不匹配`：工具读到了其他型号，禁止继续写入。
- 擦除后写入中断：保持 BOOT0 为高，重新上电/复位并再次烧录同一个 HEX；成功前不要恢复普通启动。
- BOOT0 拉高仍无法进入：需要确认 STM32 option bytes 的启动配置，或改用 SWD 调试器处理。

## 数据影响

此流程执行全片擦除。当前应用、Flash 内保存的标定参数和配置都会被删除，并由旧 NO8 HEX 覆盖；STM32 option bytes 不在本工具的写入范围内。


"""Package the native Commander variant; preserve previous release archives."""
from pathlib import Path

import build_spi_gpio_witness_release as build


if __name__ == "__main__":
    build.OUTPUT = build.ROOT / "release/spi_gpio_witness_v3_20261016"
    build.WORK = build.ROOT / "tmp/spi_gpio_witness_v3_build"
    build.EXE_NAME = "H3_SPI_GPIO_Witness_v3"
    build.RELEASE_ZIP = build.ROOT / "release/H3_SPI_GPIO_Witness_v3_20261016.zip"
    build.RELEASE_VERSION = 3
    build.EXTRA_SELF_TESTS = ["jlink_commander_gpio_reader.py"]
    build.EXTRA_SOURCE_FILES = [Path(__file__), build.ROOT / "tools/jlink_commander_gpio_reader.py"]
    build.README_OVERRIDE = """H3 SPI 输入状态对照 v3：原厂 Commander 版

固件保持 h3_spi_20261016_rxirq_v5，所有接线保持。
关闭J-Flash、Commander、Demo及UART日志工具。
在PowerShell中运行：
& '.\\H3_SPI_GPIO_Witness_v3.exe' --jlink-exe 'C:\\Program Files\\SEGGER\\JLink\\JLink.exe' --no-pause

工具自动用原厂JLink.exe连接运行中的STM32G031G8，读取GPIO输入，同时让CH347
在NSS保持高时输出有限00/FF段。仅在完整成功发送窗口内分析采样；每段还要求
观察到SCK高低两态。未观察到只记证据不足，不判定硬件故障。

不依赖JLink_x64.dll枚举，不按S/N选择，允许原厂启动日志显示S/N=-1。
仅使用已现场验证的device/si/speed/connect/IsHalted/mem32/exit命令，不使用exec。
不发送擦除、编程、写内存、复位或暂停命令。连接采用本机原厂程序的Connect流程；
采样前后检查CPU处于运行状态，CPU若暂停则停止测试。
后台进程、命令和采样均有超时；失败保留原始日志。

完成后把logs下本次目录整个压缩发来，包含summary.json、jlink_session.log、
samples.jsonl和transactions.jsonl。仅软件模拟自检通过，实机结果待本次运行。

SEGGER Commander参考：https://kb.segger.com/J-Link_Commander
"""
    build.main()

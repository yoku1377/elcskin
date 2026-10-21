"""Build the USB-enumerating revision without overwriting the original release."""
from pathlib import Path

import build_spi_gpio_witness_release as build


if __name__ == "__main__":
    build.OUTPUT = build.ROOT / "release/spi_gpio_witness_v2_20261016"
    build.WORK = build.ROOT / "tmp/spi_gpio_witness_v2_build"
    build.EXE_NAME = "H3_SPI_GPIO_Witness_v2"
    build.RELEASE_ZIP = build.ROOT / "release/H3_SPI_GPIO_Witness_v2_20261016.zip"
    build.RELEASE_VERSION = 2
    build.RELEASE_NOTES = """v2 更新：
先枚举本机USB探针，记录DLL版本/路径与实际序列号，再按序列号打开。
不再固定选择旧式USB地址0。唯一探针自动选择；多个探针须用 --jlink-serial 指定。
打开探针失败与连接STM32失败分开记录；失败不会继续发送CH347数据。
其他接线、v5固件、GPIO读取和对照数据流程保持原样。
此次修正不能预先证明现场连接问题已经解决，以运行日志为准。
"""
    build.EXTRA_SOURCE_FILES = [Path(__file__)]
    build.main()

"""Package a UART-installable SPI evidence build; never access hardware."""
from pathlib import Path
import hashlib
import json
import shutil
import struct
import subprocess
import sys
import zipfile

from tacai_image import FirmwareImage, create_container

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/spi_diagnostics_v3_20261016"
BUILD = ROOT / "firmware/application/build_spi_diag_v3"
FIRMWARE_ID = b"h3_spi_20261015_diag_v3"
V2_SHA = "e5a11578fcc00ec53e5329a7c6437865b793ca2e7464b268e7f4791b41aebd7c"


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    v2 = ROOT / "release/spi_trial_20261014/TacAI_H3_App_SPI_v2.tacai"
    assert sha(v2) == V2_SHA, "Original SPI v2 artifact changed"
    payload = (BUILD / "TacAI_H3_App_SPI_diag_v3.bin").read_bytes()
    assert FIRMWARE_ID in payload and b"SPI_READY," in payload and b"SPI_DIAG," in payload
    image = create_container(payload, 0x00020003)
    firmware = OUTPUT / "TacAI_H3_App_SPI_diag_v3.tacai"
    firmware.write_bytes(image.encode())
    checked = FirmwareImage.load(firmware)
    assert checked.payload == payload
    # Check that the real application vector points at its CS IRQ handler.
    elf = BUILD / "TacAI_H3_App_SPI_diag_v3.elf"
    nm = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin\arm-none-eabi-nm.exe")
    symbols = subprocess.check_output([str(nm), str(elf)], text=True)
    irq = next(int(line.split()[0], 16) for line in symbols.splitlines()
               if line.endswith(" EXTI4_15_IRQHandler"))
    assert struct.unpack_from("<I", payload, (16 + 7) * 4)[0] == irq | 1
    logger = ROOT / "tools/spi_uart_capture.py"
    subprocess.run([sys.executable, str(logger), "--self-test"], check=True)
    work = ROOT.parent / "SPI_预联调_20261015/diag_v3_pyinstaller"
    work.mkdir(parents=True, exist_ok=True)
    with (work / "build.log").open("w", encoding="utf-8") as log:
        result = subprocess.run([
            sys.executable, "-m", "PyInstaller", "--noconfirm", "--onefile", "--console",
            "--name", "H3_SPI_UART_Capture", "--distpath", str(OUTPUT),
            "--workpath", str(work / "work"), "--specpath", str(work), str(logger),
        ], stdout=log, stderr=subprocess.STDOUT)
    if result.returncode:
        raise RuntimeError(f"UART logger build failed; see {work / 'build.log'}")
    capture = OUTPUT / "H3_SPI_UART_Capture.exe"
    subprocess.run([str(capture), "--self-test"], check=True)
    shutil.copy2(logger, OUTPUT / logger.name)
    readme = """H3 SPI UART 诊断版 v3 — 2026-10-16 打包

用途：定位 CH347 USB 已打开，但 SPI 快照轮询全部为 FF 的原因。
这是诊断版，还没有实物 SPI 验收结果，不承诺已经修复超时。
在 CH347Demo 中直接测试 SPI；原 UART 仅用于安装应用和旁路接收诊断日志。
原 SPI v2 保留，校准模型/系数未改。固件 ID 保持 h3_spi_20261015_diag_v3。

准备：断电后拆除 CH347 板 MOSI-MISO 回环短接线，再恢复传感器 SPI 接线。
CH347 SCS0/CS0 -> H3 PA8(NSS)，SCK -> PA0，MOSI -> PA4，MISO -> PA3，GND共地。
SPI逻辑电平为3.3V。不能把原USB-UART的TX继续接到PA3(MISO)。

一、安装
1. 停止Demo的SPI操作并等待至少3秒，用原来已验证可用的UART连接，上位机选择UART串口。
2. 固件升级选择 TacAI_H3_App_SPI_diag_v3.tacai，完成升级后重新上电。
3. 这一步仅更新应用，不需要更换 Bootloader 或首次组合 HEX。
4. 运行固件 ID 应为 h3_spi_20261015_diag_v3。

二、采集诊断日志
1. 关闭上位机的 UART 连接，保持传感器供电与 SPI 接线。
   UART日志须另接3.3V TTL USB-UART：MCU PB6(TX) -> USB-UART RXD，并连接GND共地。
   只收日志不必接PB7(RX)；它与SPI PA0/PA3/PA4/PA8分开，仅SPI或J-Link SWD不会携带此UART日志。
2. 双击 H3_SPI_UART_Capture.exe，按列表序号选择上述 USB-UART 的 COM 口。
   工具只接收 115200/8N1 字节，不发送任何命令，也不操作 SPI/Flash。
3. 工具默认接收180秒。先保持供电，接收日志不需重启传感器。
   SPI_DIAG约每秒持续输出，SPI_READY仅在应用启动时输出；中途打开工具收不到SPI_READY属于正常情况。
   UART原调试口和CH347 SPI接口可分别由日志工具与Demo使用，不要让其他程序占用该UART端口。
   若CH347 USB断开或重新枚举，须待枚举完成后重新打开串口工具及CH347Demo，再初始化SPI。
4. CH347Demo使用Mode0、468.75KHz、MSB、8bit，EnableCS勾选、CS1、CS1 POLA_LOW。
   片选下拉框保持CS Deactive（高电平释放），初始化SPI(C0)后点片选控制(C1)。
   上方输出框填写：54 41 49 33 20 00 01 00 00 00 00 00 BD 00 57 14
   点快写(C4)，再点片选控制(C1)。
   清空上方输出框，输入长度536；执行快读(C3)、片选控制(C1)、等1秒，共3轮。
   整个过程只发送一次16字节请求，不在读取之间重新初始化SPI。
5. 工具保存全部原始 UART 到旁边 logs 文件夹；终端显示 SPI_READY/SPI_DIAG。
   把生成的 .log 和本次 CH347Demo 日志一起提供即可。
   提前完成可以Ctrl+C停止；已经收到的行会保存在日志中。
   原上位机运行日志不会显示这些新增原始 UART 行，需要这个工具或串口助手。

三、字段含义（用于定位，不能单凭一项断言某根线损坏）
fall/rise：H3 PA8 NSS 检测到的下降/上升沿次数。
rx：最近一个 CS 事务实际收到的字节数，响应轮询通常为 536。
data/head：最近一次非全 FF 的 MOSI 事务长度/前 4 字节；TAI3 为 54414933。
req：链路接受的 TAI3 请求数量；cmd：应用通过帧校验的请求数量；bad：校验失败数量。
stage/commit/ack：响应准备、装载到 TX DMA、完整响应读出次数。
fault/resync：DMA/SPI 异常或 CS 重同步次数；nss：当前片选电平，空闲应为 1。
cr1/cr2/sr/rxleft/txleft/flags：寄存器与状态快照。

fall/rise不增加：先定位 MCU 是否实际看到片选或是否启用了中断。
有片选而 rx 为0：定位 SPI 使能、时钟、硬件 NSS 和 DMA 接收。
有收包但 head 错误：定位 MOSI 数据、时序、位序和事务边界。
req/cmd有增长但响应仍全FF：结合 stage/commit/ack 定位发送路径。
收到 SPI_READY 或 SPI_DIAG 才能确认诊断应用已运行到相应位置；没有输出还不能判断 SPI 本身。
响应等待期间诊断输出可能暂停约2秒；此包用于当前手动测试，不用于极限吞吐或最短CS间隔验收。
ack只证明MCU看到了足长FF读取事务，不证明电脑已收到正确MISO数据或通过CRC。

该诊断版仍支持原 UART 升级。以后可用 UART 换回之前的 .tacai。
真正通过 SPI 升级应用仍要求常驻双接口 Bootloader；此包不替换 Bootloader。
"""
    (OUTPUT / "README_先看这里.txt").write_text(readme, encoding="utf-8-sig")
    sources = [
        "firmware/shared/spi_link.c", "firmware/shared/spi_link.h",
        "firmware/application/Core/Inc/app_spi.h", "firmware/application/Core/Src/app_spi.c",
        "firmware/application/Core/Src/app.c", "firmware/application/build.ps1",
        "tests/firmware_spi/spi_link_harness.c", "tests/firmware_spi/spi_link_diagnostics_harness.c",
        "tests/firmware_spi/spi_app_diagnostics_harness.c", "tests/firmware_spi/stm32g031xx.h",
        "tests/test_spi_firmware.py", "tools/spi_uart_capture.py", "tools/build_spi_diag_release.py",
    ]
    with zipfile.ZipFile(OUTPUT / "diagnostic_source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for name in sources:
            archive.write(ROOT / name, name)
    manifest = {
        "firmware_id": FIRMWARE_ID.decode(), "model": image.model,
        "app_start": hex(image.app_start), "app_version": hex(image.app_version),
        "app_length": len(payload), "app_crc32": hex(image.image_crc32),
        "exti_irq_vector": hex(irq | 1), "original_v2_sha256": V2_SHA,
        "bootloader_changed": False, "hardware_validation": "pending",
        "tests": {"firmware_harnesses": 4, "c_scenarios": 26,
                  "uart_capture_source_self_test": "passed", "uart_capture_exe_self_test": "passed"},
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                  for p in OUTPUT.iterdir() if p.is_file() and p.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/TacAI_H3_SPI诊断固件_v3_含UART日志工具_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    assert sha(v2) == V2_SHA
    print(json.dumps({"firmware": str(firmware), "zip": str(target), "size": target.stat().st_size,
                      "app_length": len(payload), "app_crc32": hex(image.image_crc32)}, ensure_ascii=False))


if __name__ == "__main__":
    main()

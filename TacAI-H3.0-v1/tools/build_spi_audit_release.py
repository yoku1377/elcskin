"""Package the complete acquisition application after the 2026-10-16 audit.

This command exports files only; it never opens UART, SPI or a debug probe.
"""
from pathlib import Path
import hashlib
import json
import shutil
import struct
import subprocess
import zipfile

from tacai_image import FirmwareImage, create_container

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "firmware/application/build_spi_audit_v4"
OUTPUT = ROOT / "release/spi_audit_v4_20261016"
NAME = "TacAI_H3_App_SPI_audit_v4"
FIRMWARE_ID = "h3_spi_20261016_audit_v4"
ARM = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_hex(path):
    memory = {}
    base = 0
    for line in path.read_text(encoding="ascii").splitlines():
        assert line.startswith(":"), "Invalid Intel HEX record"
        row = bytes.fromhex(line[1:])
        assert sum(row) & 255 == 0 and len(row) == row[0] + 5
        address = int.from_bytes(row[1:3], "big")
        if row[3] == 4:
            base = int.from_bytes(row[4:-1], "big") << 16
        elif row[3] == 0:
            for offset, byte in enumerate(row[4:-1]):
                target = base + address + offset
                assert target not in memory
                memory[target] = byte
        else:
            assert row[3] in (1, 5), "Unexpected Intel HEX record type"
    return memory


def main():
    OUTPUT.mkdir(parents=True, exist_ok=True)
    payload = (BUILD / f"{NAME}.bin").read_bytes()
    assert FIRMWARE_ID.encode() in payload
    assert b"SPI_READY," in payload and b"SPI_DIAG," in payload
    assert b"RAW,%lu" in payload and b"FORCE,%lu," in payload
    image = create_container(payload, 0x00020004)
    container = OUTPUT / f"{NAME}.tacai"
    container.write_bytes(image.encode())
    assert FirmwareImage.load(container).payload == payload
    for suffix in ("bin", "hex", "elf", "map"):
        shutil.copy2(BUILD / f"{NAME}.{suffix}", OUTPUT / f"{NAME}.{suffix}")
    memory = read_hex(OUTPUT / f"{NAME}.hex")
    assert min(memory) == image.app_start
    assert max(memory) < 0x0800F000
    assert bytes(memory.get(image.app_start + i, 255) for i in range(len(payload))) == payload
    symbols = subprocess.check_output([str(ARM / "arm-none-eabi-nm.exe"), str(BUILD / f"{NAME}.elf")], text=True)
    symbol_map = {parts[2]: int(parts[0], 16) for line in symbols.splitlines()
                  if len(parts := line.split()) == 3}
    irq = symbol_map["EXTI4_15_IRQHandler"] | 1
    assert struct.unpack_from("<I", payload, (16 + 7) * 4)[0] == irq
    assert "SPI_LinkQueue" in symbol_map and "MATRIX_Update_Measurement" in symbol_map
    assert "MX_USART2_UART_Init" not in symbol_map
    assert symbol_map["_ebss"] < symbol_map["_estack"]

    readme = """H3 完整采集固件：SPI 自检修正版 v4（2026-10-16）

固件 ID：h3_spi_20261016_audit_v4，应用版本：0x00020004。
保留 8 颗 MLX90393 的真实采集、基线、力计算、UART RAW/FORCE/DIAG、UART IAP。

本次修复
1. LOG_Error 不再永久禁止采集。运行中某一帧失败，只发布无效诊断；下一周期继续尝试采集。
   初始传感器初始化或初始基线失败仍禁止发布有效测量，防止旧数据充当新数据。
2. SPI 响应异步排队，主机暂不读取时，采集/UART 主循环不再等待读取 2 秒。
   SPI IAP 仍须完整 ACK 被读出才复位，不会在排队时直接复位。
3. SPI IRQ 中停止 DMA 和清 RX FIFO 的等待有上限；异常事务丢弃并等待后续片选恢复。
4. LED 按原理图从 PA5 修正到 PB0。SPI 在矩阵初始化之前建立，并明确报告采样无效状态。

原理图核对
SPI2：PA0/SCK AF0，PA3/MISO AF0，PA4/MOSI AF1，PA8/NSS AF1。
CN1：1=3.3V，2=GND，3=MOSI，4=MISO，5=SCK，6=NSS。
UART1：PB6=TX，PB7=RX，AF0，115200/8N1。
矩阵使用 PA11/PA12 的 I2C2 AF6；图中 I2C1_SCL/SDA 是网络名，不代表必须选 I2C1 外设。
TMUX U9 通道码 {2,1,0,3}，U10 通道码 {3,0}，与图纸一致。

文件用途
- .tacai：使用原来可用的 UART 上位机升级应用，选择这个文件。
- .hex：供 J-Link/ST-Link 烧录的应用镜像，地址自带，只覆盖 0x08002000 起的应用。
- .bin：同一负载，烧录地址 0x08002000。
- .elf/.map 和 source.zip：核对/复现使用。
本包不含组合工厂固件；没有更新常驻 Bootloader、元数据或设备配置区。
没有 J-Link 时使用 UART 升级路径即可。只有 PB6 -> RXD 是接收日志，不能向 MCU 发送升级命令。

CH347Demo 参数与更正
Mode0，468.75kHz，MSB，8bit，低有效 CS0，事务之间 CS 高电平至少 1ms。
Demo 中 CS Active=低电平，CS Deactive=高电平；手动释放片选用 Deactive。
此前说明里把 Active 当作释放写反，应以本说明为准。
C3/C4 的 DLL 调用本身会在传输前拉低、结束后拉高，不需要再人为保持 Active。
请求和读取使用分开的 CS 事务。首轮可能为 FF；后续必须读取完整帧再验 CRC/序号。
上位机 Python SPI 后端也发现相反的 CS 调用，源代码已修正；已有 EXE 不会随源码自动改变。

验收边界
已通过实际生产 C 的主机故障注入、Arm 编译、应用向量/CRC/HEX 负载与 Flash 边界检查。
没有连接这块传感器进行实物验证，不能宣称本包已经解决现场全 FF 或 UART 静默。
本包修正的是已经查实的软件错误与异常路径；原 SPI v2/diag v3 发布文件保留。
"""
    (OUTPUT / "README_自检结果与文件用途.txt").write_text(readme, encoding="utf-8-sig")
    sources = sorted((ROOT / "firmware/application").rglob("*.c"))
    sources += sorted((ROOT / "firmware/application").rglob("*.h"))
    sources += sorted((ROOT / "firmware/application/Drivers").rglob("*.s"))
    sources += [ROOT / "firmware/application/application.ld", ROOT / "firmware/application/build.ps1"]
    sources += sorted((ROOT / "firmware/shared").glob("*.[ch]"))
    sources += sorted((ROOT / "tests").rglob("*.py")) + sorted((ROOT / "tests").rglob("*.c")) + sorted((ROOT / "tests").rglob("*.h"))
    sources += [ROOT / "tools/tacai_image.py", Path(__file__), ROOT / "pc/TfgMonitorPython_H3_v5.0/ch347_spi.py"]
    with zipfile.ZipFile(OUTPUT / "source.zip", "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(sources)):
            if not any(part.startswith("build") for part in path.relative_to(ROOT).parts):
                archive.write(path, path.relative_to(ROOT).as_posix())
    manifest = {
        "firmware_id": FIRMWARE_ID, "model": image.model,
        "app_start": hex(image.app_start), "app_end_exclusive": hex(image.app_start + len(payload)),
        "app_version": hex(image.app_version), "app_length": len(payload),
        "app_crc32": hex(image.image_crc32), "exti_irq_vector": hex(irq),
        "ram_static_end": hex(symbol_map["_ebss"]), "stack_top": hex(symbol_map["_estack"]),
        "hardware_validation": "pending", "bootloader_changed": False,
        "calibration_profile": 8,
        "files": {path.name: {"size": path.stat().st_size, "sha256": sha(path)}
                  for path in sorted(OUTPUT.iterdir()) if path.is_file() and path.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/TacAI_H3_SPI完整采集_自检修正版_v4_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    print(json.dumps({"zip": str(target), **{key: value for key, value in manifest.items() if key != "files"}}, ensure_ascii=False))


if __name__ == "__main__":
    main()

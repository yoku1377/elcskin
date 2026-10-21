"""Export the delivered SPI diagnostic application for SWD programming."""
from pathlib import Path
import hashlib
import json
import struct
import subprocess
import zipfile

from tacai_image import APP_START, FirmwareImage

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "release/spi_diagnostics_v3_20261016/TacAI_H3_App_SPI_diag_v3.tacai"
OUTPUT = ROOT / "release/spi_diagnostics_v3_jlink_20261016"
OBJCOPY = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin\arm-none-eabi-objcopy.exe")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_hex(path):
    memory = {}
    base = 0
    eof = False
    for line in path.read_text(encoding="ascii").splitlines():
        assert line.startswith(":"), "Invalid Intel HEX record"
        raw = bytes.fromhex(line[1:])
        assert len(raw) == raw[0] + 5 and sum(raw) & 0xFF == 0, "Invalid HEX length/checksum"
        count, offset, kind = raw[0], int.from_bytes(raw[1:3], "big"), raw[3]
        data = raw[4:4 + count]
        if kind == 0:
            for index, value in enumerate(data):
                address = base + offset + index
                assert address not in memory, "Overlapping HEX records"
                memory[address] = value
        elif kind == 4:
            assert count == 2
            base = int.from_bytes(data, "big") << 16
        elif kind == 1:
            assert count == 0
            eof = True
        elif kind != 5:
            raise AssertionError(f"Unexpected HEX record type {kind}")
    assert eof
    return memory


def main():
    image = FirmwareImage.load(SOURCE)
    assert image.app_version == 0x00020003
    assert b"h3_spi_20261015_diag_v3" in image.payload
    OUTPUT.mkdir(parents=True, exist_ok=True)
    binary = OUTPUT / "TacAI_H3_App_SPI_diag_v3.bin"
    ihex = OUTPUT / "TacAI_H3_App_SPI_diag_v3.hex"
    binary.write_bytes(image.payload)
    subprocess.run([str(OBJCOPY), "-I", "binary", "-O", "ihex",
                    "--change-addresses=0x08002000", str(binary), str(ihex)], check=True)
    memory = read_hex(ihex)
    end = APP_START + len(image.payload)
    assert min(memory) == APP_START and max(memory) == end - 1
    assert len(memory) == len(image.payload)
    assert bytes(memory[address] for address in range(APP_START, end)) == image.payload
    assert not any(address < APP_START or address >= 0x0800F000 for address in memory)
    assert binary.read_bytes() == SOURCE.read_bytes()[64:]
    stack, reset = struct.unpack_from("<II", image.payload)
    readme = """H3 SPI 诊断 v3 — J-Link/SWD 应用版，2026-10-16

推荐文件：TacAI_H3_App_SPI_diag_v3.hex
HEX自带应用地址0x08002000，连接方式SWD，目标STM32G031G8U6。
它与已交付TacAI_H3_App_SPI_diag_v3.tacai内的应用负载逐字节相同。
运行固件ID：h3_spi_20261015_diag_v3。

一、烧录
1. 连接J-Link的SWDIO、SWCLK、GND、VTref，按原方法给目标板供电。
2. 在J-Link烧录工具中选择STM32G031G8、SWD，加载上述HEX。
3. 使用应用范围按页/扇区擦除并编程、校验；不要选择全片擦除。
   当前应用数据范围：0x08002000至0x0800B0FF，共37120字节。
   对应2KB擦除页范围：0x08002000至0x0800B7FF。
4. 保留原Bootloader 0x08000000至0x08001FFF，以及配置页0x0800F800至0x0800FFFF。
5. 校验成功后复位运行。UART日志仅在另接下述USB-UART时可接收；只有SPI和J-Link时日志工具为空是正常情况。

BIN备用文件：TacAI_H3_App_SPI_diag_v3.bin，必须填写烧录起址0x08002000。
这是应用版，依赖板上已有的TacAI UART或双接口Bootloader，不从0x08000000写入。
HEX/BIN不写Bootloader、IAP元数据页0x0800F000或配置页0x0800F800。

若此前UART升级中断，Bootloader可能保留DOWN状态。仅在确认为这种情况时，
可单独擦除元数据页0x0800F000至0x0800F7FF后复位；切勿连带擦除下一页配置。
正常运行的SPI v2板无需这一步。

二、继续SPI诊断
先断电拆除CH347的MOSI-MISO回环短接，再恢复传感器四根SPI信号与共地。
SPI：PA0=SCK，PA3=MISO，PA4=MOSI，PA8=NSS；3.3V，Mode0，MSB，8bit，468750Hz。
UART日志须另接3.3V TTL USB-UART：PB6(TX) -> USB-UART RXD，并连接GND共地，115200/8N1。
只收日志不必接PB7(RX)；外部UART TX不能接PA3。仅SPI或J-Link SWD不会携带此UART日志。
使用CH347Demo测试SPI；另有上述UART接线时，才使用之前诊断包的H3_SPI_UART_Capture.exe接收日志。
接收日志时先保持供电，不需重启传感器；SPI_DIAG约每秒持续输出，SPI_READY仅在应用启动时输出。
若CH347 USB断开或重新枚举，须待枚举完成后重新打开串口工具及CH347Demo，再初始化SPI。
此版本用于定位片选、收包及响应状态；实物SPI问题仍待验证。
"""
    (OUTPUT / "README_JLink烧录说明.txt").write_text(readme, encoding="utf-8-sig")
    manifest = {
        "model": image.model, "firmware_id": "h3_spi_20261015_diag_v3",
        "source_container": str(SOURCE.relative_to(ROOT)), "source_sha256": sha(SOURCE),
        "app_start": hex(APP_START), "app_end_exclusive": hex(end),
        "app_length": len(image.payload), "app_crc32": hex(image.image_crc32),
        "initial_stack": hex(stack), "reset_vector": hex(reset),
        "preserve_bootloader": True, "preserve_metadata": True, "preserve_config": True,
        "verified": {"container_crc_and_vectors": True, "hex_record_checksums": True,
                     "hex_equals_container_payload": True, "bin_equals_container_payload": True,
                     "only_application_addresses": True},
        "hardware_validation": "pending",
        "files": {p.name: {"size": p.stat().st_size, "sha256": sha(p)}
                  for p in OUTPUT.iterdir() if p.is_file() and p.name != "manifest.json"},
    }
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    archive_path = ROOT / "release/TacAI_H3_SPI_diag_v3_JLink_20261016.zip"
    with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    with zipfile.ZipFile(archive_path) as archive:
        assert archive.testzip() is None
        for path in OUTPUT.iterdir():
            if path.is_file():
                assert archive.read(path.name) == path.read_bytes()
    print(json.dumps({"hex": str(ihex), "bin": str(binary), "zip": str(archive_path),
                      "app_start": hex(APP_START), "app_end_exclusive": hex(end),
                      "payload_bytes": len(image.payload), "verified": True}, ensure_ascii=False))


if __name__ == "__main__":
    main()

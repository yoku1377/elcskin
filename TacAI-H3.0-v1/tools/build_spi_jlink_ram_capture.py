"""Package address-bound, non-flashing J-Link diagnostics for SPI diag v3."""
from pathlib import Path
import hashlib
import json
import shutil
import subprocess
import zipfile

ROOT = Path(__file__).resolve().parents[1]
OUTPUT = ROOT / "release/spi_diag_v3_jlink_ram_20261016"
ELF = ROOT / "firmware/application/build_spi_diag_v3/TacAI_H3_App_SPI_diag_v3.elf"
BINARY = ROOT / "release/spi_diagnostics_v3_jlink_20261016/TacAI_H3_App_SPI_diag_v3.bin"
TOOL = Path(r"C:\Program Files (x86)\Arm GNU Toolchain arm-none-eabi\12.2 mpacbti-rel1\bin")


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    assert sha(BINARY) == "c9e41f4bdf3ac6207d39d224c31a4740977c2e830830932276f90b365d368540"
    assert sha(ELF) == "f885b43734a0bc90582f017a4d62186e57ac9dd0848059b872b1844958d43ac0"
    symbols = subprocess.check_output([str(TOOL / "arm-none-eabi-nm.exe"), "-S", "-n", str(ELF)], text=True)
    rows = [line.split() for line in symbols.splitlines()]
    assert any(row == ["2000129c", "00000060", "b", "s_diagnostics"] for row in rows)
    for address, name in (("20000a10", "s_diag_bad_frames"), ("20000a14", "s_diag_commands"),
                          ("20000a28", "s_initialized"), ("20000748", "uwTick")):
        assert any(row[0] == address and row[-1] == name for row in rows if len(row) == 4)
    OUTPUT.mkdir(parents=True, exist_ok=True)
    shutil.copy2(BINARY, OUTPUT / BINARY.name)
    commands = [
        "halt", "regs",
        "mem32 0x2000129C 0x0E",  # 14 counters; J-Link counts are hexadecimal.
        "mem32 0x20000A10 0x03", "mem32 0x20000A28 0x04", "mem32 0x20000748 0x01",
        "mem32 0xE000ED08 0x01",  # VTOR distinguishes application from bootloader.
        "mem32 0x50000000 0x06", "mem32 0x50000020 0x02",
        "mem32 0x40003800 0x03",  # CR1/CR2/SR; never read the SPI data register.
        "mem32 0x40020008 0x04", "mem32 0x4002001C 0x04", "mem32 0x40020800 0x02",
        "go", 'verifybin "TacAI_H3_App_SPI_diag_v3.bin" 0x08002000', "go", "exit",
    ]
    allowed = {"halt", "regs", "mem32", "go", "verifybin", "exit"}
    assert all(line.split()[0] in allowed for line in commands)
    assert all("0x4000380C" not in line for line in commands)
    assert commands[-2:] == ["go", "exit"]
    (OUTPUT / "read_spi_ram.jlink").write_text("\n".join(commands) + "\n", encoding="ascii")
    runner = r'''@echo off
setlocal
set "H3_CAPTURE_TAG=%~1"
if not "%H3_CAPTURE_TAG%"=="before" if not "%H3_CAPTURE_TAG%"=="after" set "H3_CAPTURE_TAG=snapshot"
set "H3_JLINK_EXE="
for /f "delims=" %%P in ('where JLink.exe 2^>nul') do set "H3_JLINK_EXE=%%P"
if not defined H3_JLINK_EXE for /d %%D in ("%ProgramFiles%\SEGGER\JLink*") do if exist "%%~fD\JLink.exe" set "H3_JLINK_EXE=%%~fD\JLink.exe"
if not defined H3_JLINK_EXE for /d %%D in ("%ProgramFiles(x86)%\SEGGER\JLink*") do if exist "%%~fD\JLink.exe" set "H3_JLINK_EXE=%%~fD\JLink.exe"
if not defined H3_JLINK_EXE (
  echo JLink.exe was not found. Open SEGGER J-Link Commander manually.
  echo Use device STM32G031G8, SWD, 1000 kHz, then run the supplied commands.
  pause
  exit /b 1
)
pushd "%~dp0"
if not exist "logs" mkdir "logs"
:choose_log
set "H3_CAPTURE_LOG=logs\%H3_CAPTURE_TAG%_%RANDOM%_%RANDOM%.log"
if exist "%H3_CAPTURE_LOG%" goto choose_log
echo Keep target power on. Stop SPI transfers while this snapshot is taken.
echo Close other J-Link programs first; CH347Demo can remain open and idle.
echo Brief halt, RAM reads, resume. No reset, flash programming, or erase.
echo J-Link: %H3_JLINK_EXE%
echo Log: %H3_CAPTURE_LOG%
"%H3_JLINK_EXE%" -Device STM32G031G8 -If SWD -Speed 1000 -AutoConnect 1 -ExitOnError 0 -CommandFile "read_spi_ram.jlink" > "%H3_CAPTURE_LOG%" 2>&1
set "H3_CAPTURE_RESULT=%ERRORLEVEL%"
type "%H3_CAPTURE_LOG%"
echo.
echo Capture returned %H3_CAPTURE_RESULT%. Send the log even if it reports an error.
echo Full log: %CD%\%H3_CAPTURE_LOG%
popd
pause
endlocal
'''
    (OUTPUT / "capture.cmd").write_bytes(runner.replace("\n", "\r\n").encode("ascii"))
    for label, name in (("before", "1_before.cmd"), ("after", "2_after.cmd")):
        (OUTPUT / name).write_bytes(f'@echo off\r\ncall "%~dp0capture.cmd" {label}\r\n'.encode("ascii"))
    readme = """H3 SPI diag v3：J-Link RAM诊断，无需UART接线

用途：在CH347 SPI和J-Link SWD现有接线下，读取MCU内部SPI诊断计数。
只适用于刚交付的TacAI_H3_App_SPI_diag_v3.hex/BIN，地址不适用于SPI v2等其他构建。
此包不烧录、不擦除、不发复位命令。读取期间短暂停核，读完恢复运行。
未做实机脚本验收；脚本语法和地址已按SEGGER文档及对应ELF静态核对。

操作：
1. 保持目标板供电和现有SPI/SWD接线；关闭占用J-Link的烧录/调试程序。
   CH347Demo可以保持打开。确认诊断应用已经运行，不要停在烧录后的暂停状态。
2. Demo停止收发并点C1结束当前事务，然后双击1_before.cmd，得到第一份logs/before_*.log。
3. 脚本结束后，在Demo输出16字节：
   54 41 49 33 20 00 01 00 00 00 00 00 BD 00 57 14
   点C4再点C1；清空输出框，输入长度536；C3、C1、等1秒，共3轮。
   参数：Mode0、468.75KHz、MSB、8bit、EnableCS、CS1、低有效；片选下拉框CS Deactive（高电平释放）。
4. 等全部SPI操作完成，再双击2_after.cmd。把logs文件夹内before/after两份.log及Demo日志发来。
   采样过程中不要点击Demo收发；前后两次采样之间不要断电、复位或再次烧录。

读取解释：
0x2000129C起的14个uint32依次为fall/rise/transactions/requests/staged/committed/
acknowledged/faults/resyncs/last_length/data_length/data_head/last_spi_sr/last_dma_isr。
data_head正常TAI3请求为54414933；后续全FF读取不会覆盖最近非FF帧头。
0x20000A10起依次为bad_frames、commands、last_diag_ms。
0x20000A28起为initialized/resync/cs_response/cs_active等字节状态。
静态诊断结构的后10个字是临时快照字段，不应把它们的零值当作硬件寄存器值。
脚本另外直接读取GPIO、SPI2、DMA/DMAMUX寄存器；不会读取SPI2数据寄存器DR。
末尾verifybin只读回Flash与包内BIN对比，用于确认地址对应的固件；它不烧录。
若读失败，脚本仍继续执行go以恢复内核运行；请提供完整日志。
所有计数只能结合前后两份快照判断。ack计数不能证明电脑已正确接收MISO或通过CRC。

原串口日志工具收不到数据的原因：SPI_DIAG从USART1 PB6输出，需要USB-UART RXD接PB6并共地。
只接SPI和SWD时，不会在COM口自动出现这些UART行；此次改为直接通过SWD读RAM。

SEGGER命令参考：https://kb.segger.com/J-Link_Commander
"""
    (OUTPUT / "README_先看这里.txt").write_text(readme, encoding="utf-8-sig")
    manifest = {"model": "H3G031G8", "firmware_id": "h3_spi_20261015_diag_v3",
                "required_payload_sha256": sha(BINARY), "elf_sha256": sha(ELF),
                "spi_diagnostics_address": "0x2000129c", "spi_counter_words": 14,
                "flashes_firmware": False, "resets_target": False, "temporarily_halts_target": True,
                "hardware_validation": "pending", "files": {
                    p.name: {"size": p.stat().st_size, "sha256": sha(p)} for p in OUTPUT.iterdir()
                    if p.is_file() and p.name != "manifest.json"}}
    (OUTPUT / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    target = ROOT / "release/H3_SPI_diag_v3_JLink_RAM_20261016.zip"
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(OUTPUT.iterdir()):
            if path.is_file():
                archive.write(path, path.name)
    with zipfile.ZipFile(target) as archive:
        assert archive.testzip() is None
        assert all(archive.read(p.name) == p.read_bytes() for p in OUTPUT.iterdir() if p.is_file())
    print(json.dumps({"zip": str(target), "output": str(OUTPUT), "verified": True,
                      "hardware_executed": False}, ensure_ascii=False))


if __name__ == "__main__":
    main()

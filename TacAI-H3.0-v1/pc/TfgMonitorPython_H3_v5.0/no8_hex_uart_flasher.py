"""Standalone GUI for programming the legacy NO8 HEX over STM32 ROM UART."""

from __future__ import annotations

import sys
from pathlib import Path

import serial
import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets

from stm32_rom_uart import IntelHexImage, RomBootError, Stm32RomBootloader, parse_intel_hex


APP_TITLE = "TacAI NO8 HEX UART 烧录器 v1.0"
CONFIRM_TEXT = "烧录 NO8"
DEFAULT_HEX_NAME = "TacAI_NO8_4region_softfusion_v3.hex"


def _application_directory() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _bundled_default_hex() -> Path | None:
    candidates = [_application_directory() / DEFAULT_HEX_NAME]
    runtime_root = getattr(sys, "_MEIPASS", None)
    if runtime_root:
        candidates.append(Path(runtime_root) / DEFAULT_HEX_NAME)
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


class FlashWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)
    completed = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)

    def __init__(self, port: str, image_path: str):
        super().__init__()
        self.port = port
        self.image_path = image_path

    def run(self):
        try:
            image = parse_intel_hex(self.image_path)
            with serial.Serial(
                self.port,
                115200,
                bytesize=serial.EIGHTBITS,
                parity=serial.PARITY_EVEN,
                stopbits=serial.STOPBITS_ONE,
                timeout=1.2,
                write_timeout=3.0,
                xonxoff=False,
                rtscts=False,
                dsrdtr=False,
            ) as uart:
                # The present CH342 PCB does not route these pins, but keeping
                # them inactive avoids surprises with other USB-UART adapters.
                uart.dtr = False
                uart.rts = False
                client = Stm32RomBootloader(
                    uart,
                    lambda done, total, text: self.progress.emit(done, total, text),
                )
                client.synchronize()
                version, pid, commands = client.identify()
                self.progress.emit(
                    0,
                    image.byte_count * 2,
                    f"已识别 STM32：PID 0x{pid:04X}，ROM v{version >> 4}.{version & 0x0F}，"
                    f"支持 {len(commands)} 条命令",
                )
                client.program_and_verify(image)
            self.completed.emit(
                "NO8 HEX 已完成全片写入和逐字节回读校验。\n\n"
                "现在请断电，将 BOOT0 恢复为低电平，再重新上电。"
            )
        except Exception as error:
            self.failed.emit(str(error))


class No8HexFlashDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.image: IntelHexImage | None = None
        self.worker: FlashWorker | None = None
        self.setWindowTitle(APP_TITLE)
        self.resize(760, 620)
        self.setStyleSheet("""
            QMainWindow, QWidget { background:#0b1d2b; color:#e8f3ff; }
            QLabel { color:#e8f3ff; }
            QLabel#title { color:#63d9ff; font-size:17pt; font-weight:700; }
            QLabel#warning { color:#ffcf70; font-weight:700; }
            QLabel#step { color:#7de3ff; font-weight:700; }
            QComboBox, QLineEdit, QPlainTextEdit {
                background:#071722; color:#e8f3ff; border:1px solid #3b7894;
                border-radius:4px; padding:6px;
            }
            QPushButton {
                background:#12364a; color:#eefbff; border:1px solid #3b7894;
                border-radius:5px; padding:7px 11px;
            }
            QPushButton:hover { background:#1d5169; }
            QPushButton#danger { background:#673147; border-color:#d36182; font-weight:700; }
            QPushButton:disabled { background:#26343d; color:#9aa8b0; border-color:#53616a; }
            QProgressBar {
                background:#071722; color:#ffffff; border:1px solid #3b7894;
                border-radius:4px; text-align:center; min-height:22px;
            }
            QProgressBar::chunk { background:#1aa3c8; }
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        title = QtWidgets.QLabel(APP_TITLE)
        title.setObjectName("title")
        layout.addWidget(title)

        warning = QtWidgets.QLabel(
            "本工具会擦除 STM32G031 当前程序，并把所选完整 NO8 HEX 写到 0x08000000。"
            "烧录前必须让主控进入原厂 ROM Bootloader。"
        )
        warning.setObjectName("warning")
        warning.setWordWrap(True)
        layout.addWidget(warning)

        instructions = QtWidgets.QLabel(
            "硬件顺序：①关闭其他串口软件；②使用 CH342 第二路 USART2（PA2/PA3，通常为 COM4）；"
            "③TX/RX 交叉并共地；④将 BOOT0 拉高；⑤重新上电或复位；⑥再点击开始烧录。\n"
            "烧录完成后：断电 → BOOT0 恢复低电平 → 重新上电。"
        )
        instructions.setWordWrap(True)
        layout.addWidget(instructions)

        port_row = QtWidgets.QHBoxLayout()
        port_row.addWidget(QtWidgets.QLabel("目标串口"))
        self.port_box = QtWidgets.QComboBox()
        refresh_button = QtWidgets.QPushButton("刷新串口")
        refresh_button.clicked.connect(self._refresh_ports)
        port_row.addWidget(self.port_box, 1)
        port_row.addWidget(refresh_button)
        layout.addLayout(port_row)

        file_row = QtWidgets.QHBoxLayout()
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setReadOnly(True)
        choose_button = QtWidgets.QPushButton("选择 NO8 HEX")
        choose_button.clicked.connect(self._choose_hex)
        file_row.addWidget(self.path_edit, 1)
        file_row.addWidget(choose_button)
        layout.addLayout(file_row)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumHeight(145)
        layout.addWidget(self.details)

        confirm_label = QtWidgets.QLabel(f"确认口令：请输入“{CONFIRM_TEXT}”")
        confirm_label.setObjectName("step")
        layout.addWidget(confirm_label)
        self.confirm_edit = QtWidgets.QLineEdit()
        self.confirm_edit.setPlaceholderText(CONFIRM_TEXT)
        self.confirm_edit.textChanged.connect(self._update_start_enabled)
        layout.addWidget(self.confirm_edit)

        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("等待开始 · %p%")
        self.status_label = QtWidgets.QLabel("尚未开始")
        self.status_label.setWordWrap(True)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status_label)
        layout.addStretch()

        buttons = QtWidgets.QHBoxLayout()
        self.start_button = QtWidgets.QPushButton("开始擦除并烧录")
        self.start_button.setObjectName("danger")
        self.start_button.clicked.connect(self._start)
        self.start_button.setEnabled(False)
        close_button = QtWidgets.QPushButton("关闭")
        close_button.clicked.connect(self.close)
        buttons.addStretch()
        buttons.addWidget(self.start_button)
        buttons.addWidget(close_button)
        layout.addLayout(buttons)

        self._refresh_ports()
        default_hex = _bundled_default_hex()
        if default_hex is not None:
            self._load_hex(default_hex)
        else:
            self.details.setPlainText("请选择 TacAI_NO8_4region_softfusion_v3.hex")

    def _refresh_ports(self):
        current_data = self.port_box.currentData()
        self.port_box.clear()
        ports = list(serial.tools.list_ports.comports())
        for port in ports:
            label = f"{port.device}  ·  {port.description or '串口设备'}"
            self.port_box.addItem(label, port.device)
        if current_data:
            for index in range(self.port_box.count()):
                if self.port_box.itemData(index) == current_data:
                    self.port_box.setCurrentIndex(index)
                    break
        # On this CH342 setup COM4 is the currently unused second UART and is
        # the expected PA2/PA3 boot channel. Prefer it without hard-requiring it.
        for index in range(self.port_box.count()):
            if self.port_box.itemData(index).upper() == "COM4":
                self.port_box.setCurrentIndex(index)
                break
        self._update_start_enabled()

    def _choose_hex(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(
            self,
            "选择完整 NO8 固件",
            str(_application_directory()),
            "Intel HEX (*.hex);;所有文件 (*)",
        )
        if path:
            self._load_hex(Path(path))

    def _load_hex(self, path: Path):
        try:
            image = parse_intel_hex(path)
        except (OSError, ValueError) as error:
            self.image = None
            self.path_edit.clear()
            self.details.setPlainText("HEX 校验失败：" + str(error))
            QtWidgets.QMessageBox.critical(self, "HEX 被拒绝", str(error))
            self._update_start_enabled()
            return
        self.image = image
        self.path_edit.setText(str(path))
        self.details.setPlainText(
            f"文件：{path.name}\n"
            f"地址：0x{image.minimum_address:08X} – 0x{image.maximum_address:08X}\n"
            f"有效数据：{image.byte_count:,} 字节；数据段：{len(image.segments)}\n"
            f"初始 SP：0x{image.initial_sp:08X}\n"
            f"复位入口：0x{image.reset_handler:08X}\n"
            "检查结果：完整 STM32G031 内部 Flash 应用镜像"
        )
        self.status_label.setText("HEX 校验通过；等待进入 BOOT 模式并确认烧录")
        self._update_start_enabled()

    def _update_start_enabled(self):
        ready = (
            self.image is not None
            and bool(self.port_box.currentData())
            and self.confirm_edit.text().strip() == CONFIRM_TEXT
            and self.worker is None
        )
        self.start_button.setEnabled(ready)

    def _start(self):
        if self.image is None:
            QtWidgets.QMessageBox.warning(self, "缺少 HEX", "请先选择并通过校验的完整 NO8 HEX。")
            return
        port = self.port_box.currentData()
        if not port:
            QtWidgets.QMessageBox.warning(self, "缺少串口", "请选择 CH342 USART2 对应串口。")
            return
        if self.confirm_edit.text().strip() != CONFIRM_TEXT:
            QtWidgets.QMessageBox.warning(self, "需要确认", f"请输入确认口令：{CONFIRM_TEXT}")
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "最后确认",
            f"即将通过 {port} 全片擦除 STM32G031，并写入：\n\n"
            f"{self.image.path.name}\n\n"
            "是否已将 BOOT0 拉高并重新上电/复位？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return

        self.worker = FlashWorker(str(port), str(self.image.path))
        self.worker.progress.connect(self._on_progress)
        self.worker.completed.connect(self._on_completed)
        self.worker.failed.connect(self._on_failed)
        self.port_box.setEnabled(False)
        self.confirm_edit.setEnabled(False)
        self.start_button.setEnabled(False)
        self.progress_bar.setFormat("正在烧录 · %p%")
        self.status_label.setText("正在打开串口并同步 Bootloader…")
        self.worker.start()

    def _on_progress(self, done: int, total: int, message: str):
        self.progress_bar.setValue(0 if total <= 0 else int(done * 1000 / total))
        self.status_label.setText(message)

    def _on_completed(self, message: str):
        self.progress_bar.setValue(1000)
        self.progress_bar.setFormat("烧录及校验完成 · %p%")
        self.status_label.setText(message)
        QtWidgets.QMessageBox.information(self, "烧录成功", message)
        self._finish_worker()

    def _on_failed(self, message: str):
        self.progress_bar.setFormat("烧录未完成 · %p%")
        self.status_label.setText("烧录未完成：" + message)
        QtWidgets.QMessageBox.critical(
            self,
            "烧录未完成",
            message
            + "\n\n请保持 BOOT0 为高，重新上电/复位后再试。"
            + "若已经执行全片擦除，在烧录成功前不要恢复普通启动。",
        )
        self._finish_worker()

    def _finish_worker(self):
        self.worker = None
        self.port_box.setEnabled(True)
        self.confirm_edit.setEnabled(True)
        self._update_start_enabled()

    def closeEvent(self, event):
        if self.worker is not None:
            QtWidgets.QMessageBox.warning(self, "烧录进行中", "当前不能关闭窗口，请等待烧录完成或报错。")
            event.ignore()
            return
        event.accept()


def main() -> int:
    app = QtWidgets.QApplication(sys.argv)
    app.setApplicationName(APP_TITLE)
    window = No8HexFlashDialog()
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())


"""Qt dialog for TacAI H3 UART, CH347F and CH341A SPI application upgrades."""

from __future__ import annotations

from pathlib import Path
from datetime import datetime
import json
import os
import sys
import time
import traceback
import uuid

import serial
import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets

from iap_protocol import IapClient, IapError, load_image
from ch347_spi import CH347Spi
from ch341_spi import CH341Spi
from spi_iap_protocol import SPIIapClient

CONFIRM_TEXT = "升级 H3G031G8"
UPGRADER_VERSION = "5.3.28"
RECOVERY_WAIT_SECONDS = 90.0
SYNC_ATTEMPT_SECONDS = 2.2


def _port_present(port: str) -> bool:
    wanted = port.casefold()
    return any(info.device.casefold() == wanted for info in serial.tools.list_ports.comports())


class UpgradeWorker(QtCore.QThread):
    progress = QtCore.pyqtSignal(int, int, str)
    completed = QtCore.pyqtSignal(str)
    failed = QtCore.pyqtSignal(str)
    log_ready = QtCore.pyqtSignal(str)
    diagnostic = QtCore.pyqtSignal(str)

    def __init__(self, port: str, image_path: str, transport="uart", clock_hz=468750):
        super().__init__()
        self.port = port
        self.image_path = image_path
        self.transport = transport
        self.clock_hz = clock_hz
        self.phase = "尚未开始"
        self.log_path = None

    def _start_log(self):
        base = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                else Path(__file__).resolve().parent)
        directory = Path(os.environ.get("TACAI_UPGRADE_LOG_DIR", base / "logs" / "upgrade"))
        try:
            directory.mkdir(parents=True, exist_ok=True)
            self.log_path = directory / (datetime.now().strftime("%Y%m%d_%H%M%S_") + uuid.uuid4().hex[:8] + ".log")
            self._log("started", transport=self.transport, port=self.port,
                      image=self.image_path, clock_hz=self.clock_hz)
            self.log_ready.emit(str(self.log_path))
        except OSError:
            self.log_path = None
            self.log_ready.emit("无法写入日志目录；本次详细信息仍显示在窗口中")

    def _log(self, event, **details):
        if self.log_path is not None:
            try:
                with self.log_path.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(dict(time=datetime.now().isoformat(timespec="milliseconds"),
                                                 event=event, phase=self.phase, **details),
                                            ensure_ascii=False) + "\n")
            except OSError:
                pass  # A log disk failure must never trigger another Flash attempt.

    def _report(self, done, total, text):
        self._log("progress", done=done, total=total, message=text)
        self.progress.emit(done, total, text)

    def _spi_trace(self, record):
        self._log("spi_protocol", detail=record)
        if record.get("event") in ("poll_corrupt", "timeout", "transport_error"):
            message = (f"SPI {record['event']}：cmd=0x{record.get('command', 0):02X}，"
                       f"seq={record.get('sequence', '?')}；{record.get('error', record.get('message', ''))}")
            if record.get("raw_prefix_hex"):
                message += "；响应前缀=" + record["raw_prefix_hex"]
            self.diagnostic.emit(message)

    def run(self):
        self._start_log()
        try:
            image = load_image(self.image_path)
            self._log("image_validated", bytes=len(image.payload), crc32=f"{image.image_crc32:08X}",
                      version=f"{image.app_version:08X}")
            if self.transport in ("spi", "ch341"):
                self._run_spi(image)
                return
            self._run_uart(image)
        except Exception as error:
            self._log("failed", error=str(error), traceback=traceback.format_exc())
            self.failed.emit(str(error))

    @staticmethod
    def _close_after_error(connection, primary):
        try:
            connection.close()
        except Exception as cleanup:
            if hasattr(primary, "add_note"):
                primary.add_note(f"连接释放也失败：{cleanup}")

    def _run_uart(self, image):
        """Only port discovery and HELLO may retry; Flash and BOOT run once."""
        self.phase = "UART 连接与同步"
        deadline = time.monotonic() + RECOVERY_WAIT_SECONDS
        last_error = None
        self._report(0, len(image.payload), "正在同步 UART Bootloader；请保持 USB 连接")
        while time.monotonic() < deadline:
            if not _port_present(self.port):
                self._report(0, len(image.payload),
                             f"等待 {self.port} 重新出现（剩余约 {max(0, int(deadline - time.monotonic()))} 秒）；尚未开始擦写")
                time.sleep(0.10)
                continue
            uart = None
            try:
                uart = serial.Serial(self.port, 115200, timeout=0.06, write_timeout=1.0)
                client = IapClient(uart, self._report)
                self._report(0, len(image.payload), f"已打开 {self.port}，正在同步 Bootloader；尚未开始擦写")
                client.enter(sync_timeout=SYNC_ATTEMPT_SECONDS)
            except (serial.SerialException, OSError, IapError) as error:
                last_error = error
                if uart is not None:
                    self._close_after_error(uart, error)
                self._report(0, len(image.payload),
                             f"UART 同步未成功：{error}。仍在等待连接，尚未开始擦写；必要时仅在此阶段重新上电传感器")
                time.sleep(0.10)
                continue
            except Exception as error:
                if uart is not None:
                    self._close_after_error(uart, error)
                raise
            # Once BEGIN may have been sent, no exception can return to the
            # discovery loop. An ACK loss must never cause another erase.
            self.phase = "UART 擦除、写入与校验"
            try:
                self._report(0, len(image.payload), "已捕获 Bootloader，开始升级；请保持供电和 USB 连接")
                client.upgrade(image)
                self.phase = "镜像校验完成，等待启动确认"
                self._report(len(image.payload), len(image.payload), "镜像 CRC32 校验通过，正在请求启动应用")
                client.boot()
            except Exception as error:
                self._close_after_error(uart, error)
                raise IapError(f"{self.phase}失败：{error}。本次已停止，不会自动重新擦除或写入。") from error
            try:
                uart.close()
            except Exception as error:
                raise IapError(f"镜像校验及启动命令已完成，但 UART 关闭失败：{error}；本次不会重新擦写，请重新连接确认设备运行状态。") from error
            self.phase = "升级完成"
            message = "UART 升级完成：整镜像 CRC32 与向量表校验通过，启动命令已确认；请重新连接核对采集数据"
            self._log("completed", message=message)
            self.completed.emit(message)
            return
        raise IapError(f"在 {int(RECOVERY_WAIT_SECONDS)} 秒内未同步 UART Bootloader；尚未开始擦写。最后错误：{last_error}")

    def _run_spi(self, image):
        """Retry discovery/synchronization only; never replay a Flash command."""
        adapter = "CH341A" if self.transport == "ch341" else "CH347F"
        self.phase = f"{adapter} 连接与同步"
        deadline = time.monotonic() + RECOVERY_WAIT_SECONDS
        last_error = None
        while time.monotonic() < deadline:
            spi = None
            try:
                self._report(0, len(image.payload),
                                   f"正在连接 {adapter} #{self.port}；需已部署 UART/SPI Bootloader")
                spi = (CH341Spi(device_index=int(self.port)) if self.transport == "ch341"
                       else CH347Spi(device_index=int(self.port), clock_hz=int(self.clock_hz), cs=0))
                configuration = getattr(spi, "configuration_summary", None)
                if isinstance(configuration, str):
                    self._log("spi_configuration", summary=configuration)
                client = SPIIapClient(spi, self._report, trace=self._spi_trace)
                client.enter(sync_timeout=SYNC_ATTEMPT_SECONDS)
            except Exception as error:
                last_error = error
                if spi is not None:
                    try:
                        spi.close()
                    except Exception as cleanup:
                        raise IapError(f"SPI 同步失败：{error}；USB 释放也失败：{cleanup}") from error
                self._report(0, len(image.payload),
                                   f"等待 SPI Bootloader：{error}。可重新上电传感器；首次需 SWD 部署。")
                time.sleep(0.15)
                continue
            try:
                self.phase = f"{adapter} 擦除、写入与校验"
                self._report(0, len(image.payload), "SPI Bootloader 已同步，开始校验并更新应用")
                client.upgrade(image)
                self.phase = "镜像校验完成，等待启动确认"
                client.boot()
            except Exception as error:
                try:
                    spi.close()
                except Exception as cleanup:
                    if hasattr(error, "add_note"):
                        error.add_note(f"USB 释放也失败：{cleanup}")
                raise
            try:
                spi.close()
            except Exception as cleanup:
                raise IapError(f"镜像校验和启动命令已完成，但 USB 释放失败：{cleanup}；请重新连接设备") from cleanup
            self.phase = "升级完成"
            message = "SPI 升级成功，整镜像 CRC32 与向量表校验通过，已请求启动新应用"
            self._log("completed", message=message)
            self.completed.emit(message)
            return
        raise IapError(f"{int(RECOVERY_WAIT_SECONDS)} 秒内未同步 SPI Bootloader。"
                       f"请核对 {adapter} 驱动、CS0 接线以及首次 SWD 部署。最后错误：{last_error}")


class FirmwareUpgradeDialog(QtWidgets.QDialog):
    def __init__(self, port: str, parent=None, default_image_path: str | Path | None = None,
                 transport="uart", clock_hz=468750):
        super().__init__(parent)
        self.port = port
        self.transport = transport
        self.clock_hz = clock_hz
        self.target_description = (f"CH341A #{port} · SPI CS0 · 固定硬件时钟"
                                   if transport == "ch341" else
                                   f"CH347F #{port} · SPI CS0 · {clock_hz} Hz"
                                   if transport == "spi" else f"UART {port or '未选择'}")
        self.image = None
        self.worker = None
        self._result_text = None
        self.setWindowTitle(f"TacAI H3 v{UPGRADER_VERSION} SPI 固件升级" if transport in ("spi", "ch341")
                            else f"TacAI H3 v{UPGRADER_VERSION} UART 固件升级")
        self.resize(720, 650)
        self.setStyleSheet("""
            QDialog { background:#071116; color:#e7f1f4; }
            QWidget { font-family:'Segoe UI','Microsoft YaHei'; font-size:10pt; color:#e7f1f4; }
            QLabel { background:transparent; }
            QLabel#warningLabel {
                background:#28271e; color:#f1d39c; border:1px solid #5a4b30;
                border-radius:8px; padding:10px 12px; font-weight:600;
            }
            QFrame#upgradeSurface {
                background:#10232c; border:1px solid #24404d; border-radius:10px;
            }
            QLabel#stepLabel { color:#b8dce0; font-weight:600; }
            QLabel#upgradeStatus { color:#a8c4cb; }
            QLineEdit {
                min-height:30px; background:#0a181f; color:#e7f1f4;
                border:1px solid #31505d; border-radius:7px; padding:3px 8px;
                selection-background-color:#246b70;
            }
            QLineEdit:focus { border-color:#45c9d1; }
            QLineEdit:disabled { background:#0d1b23; border-color:#24404d; color:#8aa2ac; }
            QPlainTextEdit {
                background:#0a181f; color:#b7cbd1; border:1px solid #24404d;
                border-radius:8px; padding:8px; selection-background-color:#246b70;
            }
            QPushButton {
                min-height:30px; background:#18313b; color:#e7f1f4;
                border:1px solid #34535f; border-radius:7px; padding:3px 12px;
            }
            QPushButton:hover { background:#213f49; border-color:#4a727e; }
            QPushButton:pressed { background:#11252d; }
            QPushButton:focus { border-color:#45c9d1; }
            QPushButton#primary { background:#176f76; border-color:#329ca4; color:#f2ffff; font-weight:600; }
            QPushButton#primary:hover { background:#21838a; border-color:#45c9d1; }
            QPushButton:disabled, QPushButton#primary:disabled {
                background:#0d1b23; color:#8aa2ac; border-color:#24404d;
            }
            QProgressBar {
                background:#0a181f; color:#e7f1f4; border:1px solid #31505d;
                border-radius:6px; text-align:center; min-height:23px;
            }
            QProgressBar::chunk { background:#45c9d1; border-radius:5px; }
        """)
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)

        warning = QtWidgets.QLabel(
            "SPI 升级需先通过 SWD 部署支持 UART/SPI 的常驻 Bootloader；现有 UART IAP 无法更新 Bootloader。"
            "仅支持 H3G031G8 的 .tacai 应用镜像。擦除后掉电需重新发送完整镜像，Bootloader 与配置页保留。"
            if transport in ("spi", "ch341") else
            "仅支持 H3G031G8 的 .tacai 镜像。开始擦除后若掉电，旧应用无法回滚，"
            "但常驻 Bootloader 会保留。连接同步阶段最多等待 90 秒；开始擦写后请保持供电与 USB 连接，失败会停止并保留原因。"
        )
        warning.setWordWrap(True)
        warning.setObjectName("warningLabel")
        layout.addWidget(warning)

        image_panel = QtWidgets.QFrame()
        image_panel.setObjectName("upgradeSurface")
        image_layout = QtWidgets.QVBoxLayout(image_panel)
        image_layout.setContentsMargins(14, 14, 14, 14)
        image_layout.setSpacing(10)
        row = QtWidgets.QHBoxLayout()
        row.setSpacing(10)
        self.path_edit = QtWidgets.QLineEdit()
        self.path_edit.setReadOnly(True)
        choose = self.choose_button = QtWidgets.QPushButton("选择 .tacai 镜像")
        choose.clicked.connect(self._choose)
        row.addWidget(self.path_edit, 1)
        row.addWidget(choose)
        image_layout.addLayout(row)

        self.details = QtWidgets.QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setPlainText(f"目标接口：{self.target_description}\n尚未选择镜像")
        image_layout.addWidget(self.details, 1)
        layout.addWidget(image_panel, 1)

        confirm_label = QtWidgets.QLabel(
            f"第 1 步：在下面输入框中完整输入确认口令：  {CONFIRM_TEXT}"
        )
        confirm_label.setObjectName("stepLabel")
        confirm_label.setWordWrap(True)
        layout.addWidget(confirm_label)
        self.confirm_edit = QtWidgets.QLineEdit()
        self.confirm_edit.setPlaceholderText(f"请在此输入：{CONFIRM_TEXT}")
        self.confirm_edit.setAccessibleName("固件升级确认口令")
        self.confirm_edit.textChanged.connect(self._update_enabled)
        layout.addWidget(self.confirm_edit)

        progress_label = QtWidgets.QLabel("第 2 步：输入正确后点击“开始安全升级”；进度如下")
        progress_label.setObjectName("stepLabel")
        progress_label.setWordWrap(True)
        layout.addWidget(progress_label)
        self.progress_bar = QtWidgets.QProgressBar()
        self.progress_bar.setRange(0, 1000)
        self.progress_bar.setValue(0)
        self.progress_bar.setFormat("尚未开始 · %p%")
        self.status = QtWidgets.QLabel("等待输入确认口令")
        self.status.setObjectName("upgradeStatus")
        self.status.setWordWrap(True)
        layout.addWidget(self.progress_bar)
        layout.addWidget(self.status)
        self.log_label = QtWidgets.QLabel("升级日志会在开始后保存")
        self.log_label.setWordWrap(True)
        self.log_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        layout.addWidget(self.log_label)
        self.event_log = QtWidgets.QPlainTextEdit()
        self.event_log.setReadOnly(True)
        self.event_log.setMaximumBlockCount(1000)
        self.event_log.setMinimumHeight(105)
        self.event_log.setAccessibleName("升级过程与错误日志")
        layout.addWidget(self.event_log)

        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(9)
        self.start_button = QtWidgets.QPushButton("开始安全升级")
        self.start_button.setObjectName("primary")
        self.start_button.setEnabled(False)
        self.start_button.clicked.connect(self._start)
        close = QtWidgets.QPushButton("关闭")
        close.clicked.connect(self.reject)
        buttons.addStretch()
        buttons.addWidget(self.start_button)
        buttons.addWidget(close)
        layout.addLayout(buttons)

        if default_image_path is not None and Path(default_image_path).is_file():
            self._load_path(Path(default_image_path), show_error=False)

    def _choose(self):
        path, _ = QtWidgets.QFileDialog.getOpenFileName(self, "选择 TacAI H3 镜像", "", "TacAI image (*.tacai)")
        if not path:
            return
        self._load_path(Path(path), show_error=True)

    def _load_path(self, path: Path, show_error: bool):
        self._result_text = None
        try:
            self.image = load_image(path)
        except (ValueError, OSError, IapError) as error:
            self.image = None
            if show_error:
                QtWidgets.QMessageBox.critical(self, "镜像被拒绝", str(error))
            self._update_enabled()
            return
        self.path_edit.setText(str(path))
        self.details.setPlainText(
            f"目标接口：{self.target_description}\n"
            f"型号：{self.image.model}\n"
            f"应用地址：0x{self.image.app_start:08X}\n"
            f"版本：0x{self.image.app_version:08X}\n"
            f"长度：{len(self.image.payload)} 字节\n"
            f"应用 CRC32：0x{self.image.image_crc32:08X}\n\n"
            "以上字段已在 PC 端校验；Bootloader 将再次独立校验。"
        )
        self.status.setText("镜像校验通过；等待人工确认")
        self._update_enabled()

    def _update_enabled(self):
        ready = self.image is not None and bool(self.port) and self.worker is None
        confirmed = self.confirm_edit.text().strip() == CONFIRM_TEXT
        self.start_button.setEnabled(ready and confirmed)
        self.choose_button.setEnabled(self.worker is None)
        if self.worker is None and ready and self._result_text is None:
            self.status.setText(
                "确认口令正确，可以开始升级" if confirmed
                else f"尚未开始：请先完整输入确认口令“{CONFIRM_TEXT}”"
            )

    def _start(self):
        if self.image is None:
            QtWidgets.QMessageBox.warning(self, "尚未选择镜像", "请先选择并校验 .tacai 固件镜像。")
            return
        if not self.port:
            if self.transport in ("spi", "ch341"):
                QtWidgets.QMessageBox.warning(self, "尚未选择设备", "请关闭窗口，在主界面检测并选择 SPI 设备后重试。")
            else:
                QtWidgets.QMessageBox.warning(self, "尚未选择串口", "请关闭窗口，在主界面选择 CH342F 对应串口后重试。")
            return
        if self.confirm_edit.text().strip() != CONFIRM_TEXT:
            self.status.setText(f"未开始：确认口令不正确；请输入“{CONFIRM_TEXT}”")
            QtWidgets.QMessageBox.warning(
                self,
                "需要确认口令",
                f"为了防止误擦除，请在第 1 步输入框中完整输入：\n\n{CONFIRM_TEXT}",
            )
            self.confirm_edit.setFocus()
            self.confirm_edit.selectAll()
            return
        answer = QtWidgets.QMessageBox.question(
            self,
            "最后确认",
            (f"开始后工具通过 {'CH341A' if self.transport == 'ch341' else 'CH347F'} SPI 同步设备；若应用无响应，可重新上电传感器。\n\n"
             if self.transport in ("spi", "ch341") else
             "开始后工具先同步串口 Bootloader。仅连接同步阶段可以重新上电；出现擦除或写入进度后请保持 USB 与供电连接。\n\n") +
            "设备清单被 Bootloader 接受后才会擦除应用。是否继续？",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No,
            QtWidgets.QMessageBox.No,
        )
        if answer != QtWidgets.QMessageBox.Yes:
            return
        self._result_text = None
        self.event_log.clear()
        self.worker = UpgradeWorker(self.port, self.path_edit.text(), self.transport, self.clock_hz)
        self.worker.progress.connect(self._progress)
        self.worker.completed.connect(self._completed)
        self.worker.failed.connect(self._failed)
        self.worker.log_ready.connect(self._on_log_ready)
        self.worker.diagnostic.connect(self._append_event)
        self.confirm_edit.setEnabled(False)
        self._update_enabled()
        self.progress_bar.setFormat("正在升级 · %p%")
        self.status.setText("正在连接 SPI；应用无响应时可重新上电传感器…" if self.transport in ("spi", "ch341")
                            else "正在同步 UART Bootloader；请保持 USB 连接…")
        self.worker.start()

    def _on_log_ready(self, path):
        self.log_label.setText("日志：" + path)

    def _append_event(self, text):
        self.event_log.appendPlainText(datetime.now().strftime("%H:%M:%S ") + text)

    def _progress(self, done: int, total: int, text: str):
        self.progress_bar.setValue(0 if total <= 0 else int(1000 * done / total))
        self.status.setText(text)
        self._append_event(text)

    def _completed(self, message: str):
        self.progress_bar.setValue(1000)
        self.progress_bar.setFormat("升级完成 · %p%")
        self._result_text = message
        self.status.setText(message)
        self.event_log.appendPlainText("完成：" + message)
        QtWidgets.QMessageBox.information(self, "升级完成", message)
        self.worker = None
        self.confirm_edit.setEnabled(True)
        self._update_enabled()

    def _failed(self, message: str):
        self.progress_bar.setFormat("升级未完成 · %p%")
        self._result_text = "升级未完成：" + message
        self.status.setText(self._result_text)
        self.event_log.appendPlainText(self._result_text)
        QtWidgets.QMessageBox.critical(
            self,
            "升级未完成",
            message + "\n\n本次任务已停止。请保留下面的过程日志，核对具体阶段后再决定是否重试。",
        )
        self.worker = None
        self.confirm_edit.setEnabled(True)
        self._update_enabled()

    def reject(self):
        if self.worker is not None:
            QtWidgets.QMessageBox.warning(self, "升级进行中", "升级线程仍在运行，当前不能关闭窗口。")
            return
        super().reject()

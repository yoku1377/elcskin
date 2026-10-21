"""Integrated calibration-data acquisition wizard for TacAI H3.

The wizard records synchronized device RAW/FORCE frames and either a manually
entered force-gauge reference or a continuously streamed ASCII gauge value.
Files are appended after every completed step so an interrupted session keeps
all data collected up to that point.
"""

from __future__ import annotations

import csv
from collections import deque
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
import re
import time

import numpy as np
import serial
import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets

from calibration_trajectory import CalibrationTrajectoryWriter, audit_json, received_time_iso


CAPTURE_SECONDS = 3.0
MIN_CAPTURE_FRAMES = 10
RAW_COLUMNS = [f"S{sensor}_dB{axis}" for sensor in range(1, 9) for axis in "xyz"]
DELTA_COLUMNS = [f"S{sensor}_retared_dB{axis}" for sensor in range(1, 9) for axis in "xyz"]


@dataclass(frozen=True)
class CaptureStep:
    repeat: int
    position: str
    target_n: float
    direction: str
    purpose: str


def force_calibration_steps(repeats: int = 3, quick: bool = False) -> list[CaptureStep]:
    if quick:
        ascending = [0.0, 0.1, 0.5, 1.0, 2.0, 5.0, 10.0, 15.0, 20.0, 25.0, 30.0]
    else:
        ascending = [0.0, 0.01, 0.02, 0.05, 0.1, 0.2, 0.5, 1.0, 2.0, 5.0,
                     10.0, 15.0, 20.0, 25.0, 30.0]
    descending = list(reversed(ascending[:-1]))
    steps = []
    for repeat in range(1, repeats + 1):
        for position in ("前", "中", "后"):
            steps.extend(CaptureStep(repeat, position, value, "加载", "力值/迟滞") for value in ascending)
            steps.extend(CaptureStep(repeat, position, value, "卸载", "力值/迟滞") for value in descending)
    return steps


def location_mapping_steps(repeats: int = 3) -> list[CaptureStep]:
    positions = [f"S{i}中心" for i in range(1, 9)] + ["两按键交界", "左边缘", "右边缘", "前端尖端"]
    return [
        CaptureStep(repeat, position, force, "定点加载", "位置映射")
        for repeat in range(1, repeats + 1)
        for position in positions
        for force in (3.0, 15.0, 30.0)
    ]


def repeatability_steps() -> list[CaptureStep]:
    return [
        CaptureStep(repeat, position, force, "重复加载", "重复性")
        for position in ("前", "中", "后")
        for force in (5.0, 15.0, 30.0)
        for repeat in range(1, 11)
    ]


def build_steps(preset: str) -> list[CaptureStep]:
    if preset.startswith("快速"):
        return force_calibration_steps(repeats=1, quick=True)
    if preset.startswith("8点"):
        return location_mapping_steps()
    if preset.startswith("重复性"):
        return repeatability_steps()
    return force_calibration_steps(repeats=3, quick=False)


def parse_gauge_value(text: str) -> float:
    """Extract the final finite numeric value from a gauge ASCII line."""
    numbers = re.findall(r"[-+]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][-+]?\d+)?", text)
    if not numbers:
        raise ValueError("测力计行内没有数字")
    value = float(numbers[-1])
    if not np.isfinite(value):
        raise ValueError("测力计数值不是有限数")
    return value


class GaugeReader(QtCore.QThread):
    measurement = QtCore.pyqtSignal(float, str, float)
    status = QtCore.pyqtSignal(str, str)
    sample_context = QtCore.pyqtSignal(dict)
    protocol_event = QtCore.pyqtSignal(dict)
    reference_invalidated = QtCore.pyqtSignal(dict)

    def __init__(self, port: str, baud: int, scale_to_n: float, protocol: str = "ascii"):
        super().__init__()
        if protocol not in ("ascii", "zp500n"):
            raise ValueError("未知测力计协议")
        self.port = port
        self.baud = 2400 if protocol == "zp500n" else baud
        self.scale_to_n = scale_to_n
        self.protocol = protocol
        self.running = True

    def stop(self):
        self.running = False

    def _run_ascii(self, gauge):
        while self.running:
            line = gauge.readline().decode("utf-8", errors="ignore").strip()
            if not line:
                continue
            try:
                original_value = parse_gauge_value(line)
                value = original_value * self.scale_to_n
            except ValueError:
                continue
            if not np.isfinite(value):
                continue
            received_at = time.time()
            self.sample_context.emit({
                "protocol": "ascii", "port": self.port, "baud": self.baud,
                "original_value": original_value, "source_unit": "external_scale",
                "scale_to_n": self.scale_to_n, "force_n": value,
                "raw_text": line, "host_received_at": received_at,
            })
            self.measurement.emit(value, line, received_at)

    def _run_zp500n(self, gauge, opened_at: float):
        from zp500n_protocol import ZP500NProtocol

        # The original program schedules FA from a 50 ms timer after Open().
        ready_at = opened_at + 0.05
        while self.running:
            remaining = ready_at - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(0.01, remaining))
        if not self.running:
            return

        def raw_event(data, *, phase, read_started_at, read_completed_at, received_at,
                      waiting=0, read_gap=0.0):
            self.protocol_event.emit({
                "event": "zp500n_raw_receive", "protocol": "zp500n",
                "port": self.port, "baud": self.baud, "phase": phase,
                "raw_bytes_hex": data.hex(), "raw_text": data.decode("ascii", errors="replace"),
                "length": len(data), "in_waiting_before_read": waiting,
                "read_started_monotonic_s": read_started_at,
                "read_completed_monotonic_s": read_completed_at,
                "read_duration_s": read_completed_at - read_started_at,
                "read_loop_gap_s": read_gap, "host_received_at": received_at,
            })

        # An instrument can still be streaming from an earlier connection.
        # Preserve already queued bytes as raw evidence before FA changes mode.
        # This bounded drain never promotes an unconfirmed unit to a reference.
        for _ in range(4):
            if not self.running:
                return
            waiting = max(0, int(getattr(gauge, "in_waiting", 0)))
            if not waiting:
                break
            started = time.monotonic()
            data = gauge.read(min(256, waiting))
            completed, received = time.monotonic(), time.time()
            if data:
                raw_event(data, phase="PRE_FA", read_started_at=started,
                          read_completed_at=completed, received_at=received, waiting=waiting)
            else:
                break
        if not self.running:
            return
        protocol = ZP500NProtocol()
        protocol.start(time.monotonic())
        last_state = None

        def report_state():
            nonlocal last_state
            metadata = protocol.metadata_raw
            state = (protocol.state, metadata, protocol.unit, protocol.rejected_frames)
            if state == last_state:
                return
            last_state = state
            self.protocol_event.emit({
                "event": "zp500n_protocol_state", "protocol": "zp500n",
                "port": self.port, "baud": self.baud, "state": protocol.state,
                "metadata_raw_hex": metadata.hex(),
                "metadata_raw_text": metadata.decode("ascii", errors="replace"),
                "source_unit": protocol.unit,
                "scale_to_n": protocol.unit_scale_to_n,
                "metadata_host_received_at": protocol.metadata_received_at,
                "metadata_repeat_count": getattr(protocol, "metadata_repeat_count", None),
                "metadata_phase": getattr(protocol, "metadata_phase", None),
                "last_error": protocol.last_error,
                "rejected_frames": protocol.rejected_frames,
                "last_rejected_raw_hex": protocol.last_rejected_raw.hex() if protocol.last_rejected_raw is not None else None,
                "host_received_at": time.time(),
            })

        def send_pending():
            for command in protocol.drain_writes():
                if not self.running:
                    return
                if command not in (b"\xfa", b"\x25"):
                    raise OSError("ZP-500N 只读协议拒绝了非读取命令")
                if gauge.write(command) != len(command):
                    raise OSError("ZP-500N 读取握手命令未完整发送；未自动重发")
                self.protocol_event.emit({
                    "event": "zp500n_read_command", "protocol": "zp500n",
                    "port": self.port, "baud": self.baud,
                    "command_hex": command.hex(), "host_received_at": time.time(),
                })

        send_pending()
        report_state()
        last_read_at = time.monotonic()
        backlog = False
        measurement_announced = False

        def invalidate_reference(reason, *, read_gap=0.0, data_length=0):
            nonlocal backlog
            if backlog:
                return
            backlog = True
            context = {
                "event": "zp500n_reference_invalidated", "protocol": "zp500n",
                "port": self.port, "baud": self.baud, "reason": reason,
                "read_loop_gap_s": read_gap, "raw_length": data_length,
                "host_received_at": time.time(),
            }
            # Clear the old reference before any queued data could be emitted.
            self.reference_invalidated.emit(context)
            self.protocol_event.emit(context)

        try:
            while self.running:
                read_started_at = time.monotonic()
                waiting = max(0, int(getattr(gauge, "in_waiting", 0)))
                data = gauge.read(min(256, waiting) if waiting else 1)
                now, received_at = time.monotonic(), time.time()
                read_gap = now - last_read_at
                last_read_at = now
                state_before = protocol.state
                if read_gap > 0.3:
                    invalidate_reference("read_loop_pause", read_gap=read_gap, data_length=len(data))
                elif state_before == "STREAMING" and (waiting >= 12 or len(data) >= 12):
                    invalidate_reference("queued_numeric_frames", read_gap=read_gap, data_length=len(data))
                try:
                    samples = protocol.feed(data, now, received_at=received_at) if data else protocol.advance(now)
                except Exception:
                    if data:
                        raw_event(data, phase=state_before, read_started_at=read_started_at,
                                  read_completed_at=now, received_at=received_at,
                                  waiting=waiting, read_gap=read_gap)
                    raise
                if len(samples) > 1:
                    invalidate_reference("multiple_numeric_frames_in_one_read", read_gap=read_gap,
                                         data_length=len(data))
                if data:
                    raw_event(data, phase=state_before, read_started_at=read_started_at,
                              read_completed_at=now, received_at=received_at,
                              waiting=waiting, read_gap=read_gap)
                send_pending()
                report_state()
                for sample in samples:
                    raw_text = sample.raw.decode("ascii", errors="strict")
                    context = {
                        "protocol": "zp500n", "port": self.port, "baud": self.baud,
                        "original_value": sample.value, "source_unit": sample.unit,
                        "scale_to_n": sample.scale_to_n, "force_n": sample.force_n,
                        "raw_text": raw_text, "raw_bytes_hex": sample.raw.hex(),
                        "metadata_raw_hex": protocol.metadata_raw.hex(),
                        "metadata_raw_text": protocol.metadata_raw.decode("ascii", errors="replace"),
                        "host_received_at": sample.host_received_at,
                        "clock_alignment": "host_receipt_only_not_hardware_synchronized",
                        "reference_usable": not backlog, "read_loop_gap_s": read_gap,
                    }
                    if backlog or not self.running:
                        self.protocol_event.emit({"event": "zp500n_sample_suppressed", **context,
                                                  "reason": "backlog" if backlog else "reader_stopped"})
                        continue
                    self.sample_context.emit(context)
                    if not measurement_announced:
                        self.status.emit("测力计数据已识别：已收到有效N单位数值", "success")
                        measurement_announced = True
                    self.measurement.emit(sample.force_n, raw_text, sample.host_received_at)
                if (backlog and not data and protocol.state == "STREAMING"
                        and protocol.pending_numeric_bytes == 0):
                    # A partial queued frame must first complete (and be
                    # suppressed) so fixed-width alignment is never discarded.
                    backlog = False
                    self.protocol_event.emit({
                        "event": "zp500n_backlog_drained", "protocol": "zp500n",
                        "port": self.port, "baud": self.baud,
                        "host_received_at": received_at, "requires_new_complete_frame": True,
                    })
        finally:
            report_state()

    def run(self):
        gauge = None
        try:
            options = {"timeout": 0.2}
            if self.protocol == "zp500n":
                options = {"timeout": 0.01, "write_timeout": 0.5,
                           "bytesize": serial.EIGHTBITS, "parity": serial.PARITY_NONE,
                           "stopbits": serial.STOPBITS_ONE, "xonxoff": False,
                           "rtscts": False, "dsrdtr": False}
            if self.protocol == "zp500n":
                gauge = serial.Serial(port=None, baudrate=self.baud, **options)
                # Match the original SerialPort defaults before opening the
                # handle; disabling hardware flow control alone is insufficient.
                gauge.dtr = False
                gauge.rts = False
                gauge.port = self.port
                gauge.open()
            else:
                gauge = serial.Serial(self.port, self.baud, **options)
            opened_at = time.monotonic()
        except Exception as error:
            if gauge is not None:
                try:
                    gauge.close()
                except Exception as close_error:
                    self.status.emit(f"测力计初始化异常后关闭串口也失败：{close_error}", "error")
            self.status.emit(f"测力计串口打开失败：{error}", "error")
            return
        if self.protocol == "zp500n":
            self.status.emit(f"测力计串口已打开 {self.port} @ {self.baud}，正在识别数据格式", "info")
        else:
            self.status.emit(f"测力计已连接 {self.port} @ {self.baud}", "success")
        try:
            if self.protocol == "zp500n":
                self._run_zp500n(gauge, opened_at)
            else:
                self._run_ascii(gauge)
        except Exception as error:
            self.status.emit(f"测力计读取失败：{error}", "error")
        finally:
            try:
                gauge.close()
            except Exception as error:
                self.status.emit(f"测力计串口关闭失败：{error}", "error")


class CalibrationAcquisitionDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.steps: list[CaptureStep] = []
        self.step_index = 0
        self.session_started = False
        self.capturing = False
        self.stream_paused = False
        self.capture_started_at = None
        self.capture_reference_mode = None
        self.capture_rows: list[dict] = []
        self.latest_gauge_n = 0.0
        self.latest_gauge_at = 0.0
        self.latest_gauge_raw = ""
        self._gauge_seen = False
        self.gauge_history = deque(maxlen=4096)
        self.gauge_reader = None
        self.gauge_connection_epoch = 0
        self.gauge_sample_context = {}
        self.gauge_protocol_metadata = {}
        self.detail_path = None
        self.summary_path = None
        self.trajectory_writer = None
        self.trajectory_error = None
        self.trajectory_flush_timer = QtCore.QTimer(self)
        self.trajectory_flush_timer.setInterval(1000)
        self.trajectory_flush_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self.trajectory_flush_timer.timeout.connect(self._flush_trajectory)
        self.setWindowTitle("TacAI H3 标定数据采集向导 v1.0")
        self.resize(1100, 900)
        self._build_ui()
        self._refresh_gauge_ports()

    def _build_ui(self):
        self.setStyleSheet("""
            QDialog { background:#071116; color:#e7f1f4; }
            QWidget { font-family:'Segoe UI','Microsoft YaHei'; font-size:10pt; color:#e7f1f4; }
            QLabel { background:transparent; }
            QLabel#introNotice {
                background:#28271e; color:#f1d39c; border:1px solid #5a4b30;
                border-radius:8px; padding:10px 12px; font-weight:600;
            }
            QGroupBox {
                background:#10232c; border:1px solid #24404d; border-radius:10px;
                margin-top:15px; padding-top:10px; font-weight:600;
            }
            QGroupBox::title {
                color:#d9e9ed; subcontrol-origin:margin; subcontrol-position:top left;
                left:13px; padding:0 5px;
            }
            QGroupBox#activeStep { background:#122a32; border-color:#32616a; }
            QLabel#stepTitle { font-size:16pt; font-weight:700; color:#f1d39c; }
            QLabel#gaugeValue { color:#a8c4cb; }
            QLineEdit, QComboBox, QDoubleSpinBox, QSpinBox {
                min-height:30px; background:#0a181f; border:1px solid #31505d;
                border-radius:7px; padding:3px 8px; color:#e7f1f4;
                selection-background-color:#246b70;
            }
            QLineEdit:focus, QComboBox:focus, QDoubleSpinBox:focus, QSpinBox:focus {
                border-color:#45c9d1;
            }
            QLineEdit:disabled, QComboBox:disabled, QDoubleSpinBox:disabled, QSpinBox:disabled {
                background:#0d1b23; border-color:#24404d; color:#8aa2ac;
            }
            QPlainTextEdit {
                background:#0a181f; border:1px solid #24404d; border-radius:8px;
                padding:8px; color:#b7cbd1; selection-background-color:#246b70;
            }
            QPushButton {
                min-height:30px; background:#18313b; border:1px solid #34535f;
                border-radius:7px; padding:3px 12px; color:#e7f1f4;
            }
            QPushButton:hover { background:#213f49; border-color:#4a727e; }
            QPushButton:pressed { background:#11252d; }
            QPushButton:focus { border-color:#45c9d1; }
            QPushButton#primary { background:#176f76; border-color:#329ca4; color:#f2ffff; font-weight:600; }
            QPushButton#primary:hover { background:#21838a; border-color:#45c9d1; }
            QPushButton:disabled, QPushButton#primary:disabled {
                background:#0d1b23; border-color:#24404d; color:#8aa2ac;
            }
            QProgressBar {
                background:#0a181f; border:1px solid #31505d; border-radius:6px;
                min-height:23px; text-align:center; color:#e7f1f4;
            }
            QProgressBar::chunk { background:#45c9d1; border-radius:5px; }
            QScrollArea, QWidget#dialogContent { background:#071116; border:0; }
            QScrollBar:vertical { background:#0a181f; width:9px; margin:0; }
            QScrollBar::handle:vertical { background:#31505d; min-height:28px; border-radius:4px; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
        """)
        outer = QtWidgets.QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        scroll = QtWidgets.QScrollArea(self)
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        content = QtWidgets.QWidget()
        content.setObjectName("dialogContent")
        root = QtWidgets.QVBoxLayout(content)
        root.setContentsMargins(20, 18, 20, 18)
        root.setSpacing(12)

        intro = QtWidgets.QLabel(
            "采集前先在主界面保持空载并完成 RAW 置零。本向导把24路RAW、置零后RAW、固件Fz、"
            "上位机模型、真实参考力、温度和样品信息按同一帧保存。每一步完成后立即写盘。"
        )
        intro.setWordWrap(True)
        intro.setObjectName("introNotice")
        root.addWidget(intro)

        meta_group = QtWidgets.QGroupBox("样品与环境")
        meta = QtWidgets.QGridLayout(meta_group)
        meta.setContentsMargins(16, 17, 16, 13)
        meta.setHorizontalSpacing(14)
        meta.setVerticalSpacing(7)
        self.device_sn = QtWidgets.QLineEdit(); self.device_sn.setPlaceholderText("例如 H3-001")
        self.silicone_batch = QtWidgets.QLineEdit(); self.silicone_batch.setPlaceholderText("硅胶批次/硬度")
        self.magnet_batch = QtWidgets.QLineEdit(); self.magnet_batch.setPlaceholderText("磁铁批次/装配编号")
        self.operator = QtWidgets.QLineEdit(); self.operator.setPlaceholderText("采集人员")
        self.temperature = QtWidgets.QDoubleSpinBox(); self.temperature.setRange(-40, 120); self.temperature.setDecimals(1); self.temperature.setValue(25.0); self.temperature.setSuffix(" ℃")
        self.note = QtWidgets.QLineEdit(); self.note.setPlaceholderText("工装、干扰源、异常等")
        fields = [("设备编号", self.device_sn), ("硅胶信息", self.silicone_batch), ("磁铁/装配", self.magnet_batch),
                  ("采集人员", self.operator), ("当前温度", self.temperature), ("备注", self.note)]
        for index, (label, widget) in enumerate(fields):
            row, col = divmod(index, 3)
            meta.addWidget(QtWidgets.QLabel(label), row * 2, col)
            meta.addWidget(widget, row * 2 + 1, col)
        root.addWidget(meta_group)

        config_group = QtWidgets.QGroupBox("采集方案与保存位置")
        config = QtWidgets.QGridLayout(config_group)
        config.setContentsMargins(16, 17, 16, 13)
        config.setHorizontalSpacing(10)
        config.setVerticalSpacing(8)
        self.preset = QtWidgets.QComboBox()
        self.preset.addItems([
            "快速力值标定（前/中/后，1轮）",
            "完整力值标定（含0.01N，前/中/后，3轮）",
            "8点位置映射（12位置×3档×3轮）",
            "重复性（前/中/后×3档×10次）",
        ])
        self.output_dir = QtWidgets.QLineEdit(str(Path.cwd() / "H3_标定采集数据"))
        browse = QtWidgets.QPushButton("选择目录"); browse.clicked.connect(self._browse_output)
        self.start_session = QtWidgets.QPushButton("创建并开始采集"); self.start_session.setObjectName("primary"); self.start_session.clicked.connect(self._start_session)
        config.addWidget(QtWidgets.QLabel("方案"), 0, 0); config.addWidget(self.preset, 0, 1, 1, 3)
        config.addWidget(QtWidgets.QLabel("保存目录"), 1, 0); config.addWidget(self.output_dir, 1, 1); config.addWidget(browse, 1, 2); config.addWidget(self.start_session, 1, 3)
        root.addWidget(config_group)

        reference_group = QtWidgets.QGroupBox("真实力参考：手动输入或测力计串口")
        reference = QtWidgets.QGridLayout(reference_group)
        reference.setContentsMargins(16, 17, 16, 13)
        reference.setHorizontalSpacing(12)
        reference.setVerticalSpacing(8)
        self.reference_mode = QtWidgets.QComboBox(); self.reference_mode.addItems(["手动输入", "测力计串口自动读取"])
        self.manual_force = QtWidgets.QDoubleSpinBox(); self.manual_force.setRange(-5.0, 100.0); self.manual_force.setDecimals(4); self.manual_force.setSuffix(" N")
        self.gauge_port = QtWidgets.QComboBox(); self.gauge_baud = QtWidgets.QComboBox(); self.gauge_baud.addItems(["1200", "2400", "4800", "9600", "19200", "38400", "57600", "115200"])
        self.gauge_baud.setCurrentText("9600")
        self.gauge_protocol = QtWidgets.QComboBox()
        self.gauge_protocol.addItem("连续ASCII文本", "ascii")
        self.gauge_protocol.addItem("ZP-500N 原程序协议（2400）", "zp500n")
        self.gauge_protocol.currentIndexChanged.connect(self._on_gauge_protocol_changed)
        self.gauge_scale = QtWidgets.QDoubleSpinBox(); self.gauge_scale.setRange(0.000001, 10000.0); self.gauge_scale.setDecimals(6); self.gauge_scale.setValue(1.0); self.gauge_scale.setToolTip("测力计原始数字乘以该系数后得到N；kgf用9.80665，gf用0.00980665，mN用0.001")
        refresh = QtWidgets.QPushButton("刷新"); refresh.clicked.connect(self._refresh_gauge_ports)
        self.gauge_connect = QtWidgets.QPushButton("连接测力计"); self.gauge_connect.clicked.connect(self._toggle_gauge)
        self.gauge_value = QtWidgets.QLabel("测力计：未连接"); self.gauge_value.setObjectName("gaugeValue")
        reference.addWidget(QtWidgets.QLabel("参考方式"), 0, 0); reference.addWidget(self.reference_mode, 0, 1)
        reference.addWidget(QtWidgets.QLabel("手动真实力"), 0, 2); reference.addWidget(self.manual_force, 0, 3)
        reference.addWidget(QtWidgets.QLabel("测力计串口"), 1, 0); reference.addWidget(self.gauge_port, 1, 1); reference.addWidget(self.gauge_baud, 1, 2); reference.addWidget(refresh, 1, 3)
        reference.addWidget(QtWidgets.QLabel("换算到N的倍率"), 2, 0); reference.addWidget(self.gauge_scale, 2, 1); reference.addWidget(self.gauge_connect, 2, 2); reference.addWidget(self.gauge_value, 2, 3)
        reference.addWidget(QtWidgets.QLabel("测力计协议"), 3, 0); reference.addWidget(self.gauge_protocol, 3, 1, 1, 3)
        root.addWidget(reference_group)

        step_group = QtWidgets.QGroupBox("当前采集步骤")
        step_group.setObjectName("activeStep")
        step_layout = QtWidgets.QVBoxLayout(step_group)
        step_layout.setContentsMargins(16, 17, 16, 14)
        step_layout.setSpacing(8)
        self.step_title = QtWidgets.QLabel("尚未开始")
        self.step_title.setObjectName("stepTitle")
        self.step_instruction = QtWidgets.QLabel("先填写样品信息并创建采集任务")
        self.step_instruction.setWordWrap(True)
        self.progress = QtWidgets.QProgressBar(); self.progress.setRange(0, 1000); self.progress.setValue(0)
        buttons = QtWidgets.QHBoxLayout()
        buttons.setSpacing(8)
        self.previous_button = QtWidgets.QPushButton("上一步"); self.previous_button.clicked.connect(self._previous)
        self.capture_button = QtWidgets.QPushButton("载荷稳定，采样3秒"); self.capture_button.setObjectName("primary"); self.capture_button.clicked.connect(self._begin_capture)
        self.skip_button = QtWidgets.QPushButton("跳过此点"); self.skip_button.clicked.connect(self._skip)
        self.stop_button = QtWidgets.QPushButton("结束并保留数据"); self.stop_button.clicked.connect(self._stop_session)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button):
            widget.setEnabled(False); buttons.addWidget(widget)
        buttons.addStretch()
        step_layout.addWidget(self.step_title); step_layout.addWidget(self.step_instruction); step_layout.addWidget(self.progress); step_layout.addLayout(buttons)
        root.addWidget(step_group)

        self.log = QtWidgets.QPlainTextEdit(); self.log.setReadOnly(True); self.log.setMaximumBlockCount(500)
        root.addWidget(self.log, 1)
        scroll.setWidget(content)
        outer.addWidget(scroll)

    def _log(self, message: str):
        self.log.appendPlainText(f"[{time.strftime('%H:%M:%S')}] {message}")

    def _browse_output(self):
        path = QtWidgets.QFileDialog.getExistingDirectory(self, "选择标定数据保存目录", self.output_dir.text())
        if path:
            self.output_dir.setText(path)

    def _refresh_gauge_ports(self):
        current = self.gauge_port.currentText()
        self.gauge_port.clear()
        for port in serial.tools.list_ports.comports():
            self.gauge_port.addItem(port.device)
        if current:
            self.gauge_port.setCurrentText(current)

    def _toggle_gauge(self):
        if self.gauge_reader is not None:
            self.gauge_reader.stop()
            if not self.gauge_reader.wait(1000):
                self._reset_gauge_reference("gauge_disconnect_pending")
                self.gauge_value.setText("测力计串口正在退出，请稍候")
                self._log("测力计线程尚未退出，保留连接对象，暂不允许重新打开串口")
                return
            self.gauge_reader = None
            self._reset_gauge_reference("gauge_disconnected")
            self.gauge_connect.setText("连接测力计"); self.gauge_value.setText("测力计：未连接")
            self._apply_gauge_controls()
            return
        if not self.gauge_port.currentText():
            QtWidgets.QMessageBox.warning(self, "没有串口", "请选择测力计串口；如果测力计不能输出串口数据，请使用手动输入。")
            return
        self._reset_gauge_reference("gauge_new_connection")
        self.gauge_reader = GaugeReader(self.gauge_port.currentText(), int(self.gauge_baud.currentText()),
                                        self.gauge_scale.value(), protocol=self.gauge_protocol.currentData())
        self.gauge_reader.measurement.connect(self._on_gauge)
        self.gauge_reader.status.connect(self._on_gauge_status)
        self.gauge_reader.sample_context.connect(self._on_gauge_sample_context)
        self.gauge_reader.protocol_event.connect(self._on_gauge_protocol_event)
        self.gauge_reader.reference_invalidated.connect(self._on_gauge_reference_invalidated)
        self.gauge_reader.finished.connect(self._on_gauge_finished)
        self.gauge_reader.start(); self.gauge_connect.setText("断开测力计")
        self._apply_gauge_controls()

    def _on_gauge_protocol_changed(self, _index=None):
        if self.gauge_protocol.currentData() == "zp500n":
            self.gauge_baud.setCurrentText("2400")
        self._apply_gauge_controls()

    def _apply_gauge_controls(self):
        zp500n = self.gauge_protocol.currentData() == "zp500n"
        connected = self.gauge_reader is not None
        self.gauge_protocol.setEnabled(not self.session_started and not connected)
        self.gauge_baud.setEnabled(not zp500n and not connected)
        self.gauge_port.setEnabled(not connected)
        self.gauge_scale.setEnabled(not zp500n and not self.session_started and not connected)
        self.gauge_scale.setToolTip(
            "ZP-500N 读取本次已验证的N单位格式；其他单位或格式尚待确认，手动倍率不参与计算。" if zp500n else
            "测力计原始数字乘以该系数后得到N；kgf用9.80665，gf用0.00980665，mN用0.001"
        )

    def _reset_gauge_reference(self, reason: str, *, new_connection: bool = True):
        if new_connection:
            self.gauge_connection_epoch += 1
            self.gauge_protocol_metadata = {}
        self.latest_gauge_n = 0.0; self.latest_gauge_at = 0.0; self.latest_gauge_raw = ""
        self._gauge_seen = False; self.gauge_history.clear(); self.gauge_sample_context = {}
        if self.session_started:
            if not self._trajectory_event("gauge_reference_reset", details={
                "reason": reason, "gauge_connection_epoch": self.gauge_connection_epoch,
            }):
                return
            if self.capturing and self.reference_mode.currentIndex() == 1:
                self._cancel_capture_for_reference(reason)

    def _accept_gauge_sender(self):
        sender = self.sender()
        return sender is None or sender is self.gauge_reader

    def _on_gauge_sample_context(self, context: dict):
        if self._accept_gauge_sender():
            self.gauge_sample_context = dict(context)

    def _on_gauge_reference_invalidated(self, context: dict):
        if not self._accept_gauge_sender():
            return
        reason = context.get("reason", "gauge_reference_invalidated")
        self._reset_gauge_reference(reason, new_connection=False)
        self.gauge_value.setText("测力计参考暂不可用：排空积压后等待新鲜数据")
        self._log(f"测力计读取间断或积压（{reason}）；旧参考已清除，当前点需等待新鲜数据后重新采样")

    def _on_gauge_protocol_event(self, context: dict):
        if not self._accept_gauge_sender():
            return
        if context.get("event") == "zp500n_protocol_state":
            self.gauge_protocol_metadata = dict(context)
            if context.get("source_unit"):
                self.gauge_value.setText(f"ZP-500N：仪器单位 {context['source_unit']}，自动换算为 N")
        if self.session_started:
            self._trajectory_event("gauge_protocol", details=context)

    def _on_gauge(self, value: float, raw: str, received_at: float):
        if not self._accept_gauge_sender():
            return
        context = self.gauge_sample_context
        context_matches = context.get("host_received_at") == received_at and context.get("raw_text") == raw
        protocol_name = getattr(self.gauge_reader, "protocol", self.gauge_protocol.currentData())
        if protocol_name == "zp500n" and (not context_matches or not context.get("source_unit")
                                           or context.get("reference_usable") is False):
            self._log("ZP-500N 测量缺少对应的单位/原始帧见证，未用作参考力")
            return
        context = dict(context) if context_matches else {}
        self.latest_gauge_n, self.latest_gauge_raw, self.latest_gauge_at = value, raw, received_at
        self._gauge_seen = True
        sample = {
            **context,
            "received_at": received_at, "value_n": value, "raw_text": raw,
            "port": getattr(self.gauge_reader, "port", self.gauge_port.currentText()),
            "baud": getattr(self.gauge_reader, "baud", self.gauge_baud.currentText()),
            "scale_to_n": context.get("scale_to_n", getattr(self.gauge_reader, "scale_to_n", self.gauge_scale.value())),
            "protocol": protocol_name, "gauge_connection_epoch": self.gauge_connection_epoch,
        }
        self.gauge_history.append(sample)
        while self.gauge_history and received_at - self.gauge_history[0]["received_at"] > 10.0:
            self.gauge_history.popleft()
        self.gauge_value.setText(f"测力计：{value:.4f} N")
        if self.session_started and self.trajectory_writer is not None:
            try:
                self.trajectory_writer.gauge({
                    "host_received_at_unix_s": received_at,
                    "host_received_time_iso": received_time_iso(received_at),
                    "force_n": value if np.isfinite(value) else None,
                    "scaled_value_repr": repr(value), "is_finite": int(np.isfinite(value)), "raw_text": raw,
                    "reference_mode": "gauge_serial" if self.reference_mode.currentIndex() == 1 else "manual",
                    "gauge_port": sample["port"], "gauge_baud": sample["baud"],
                    "scale_to_n": sample["scale_to_n"],
                    "protocol": sample["protocol"], "source_unit": sample.get("source_unit"),
                    "original_value": sample.get("original_value"), "raw_bytes_hex": sample.get("raw_bytes_hex"),
                    "protocol_metadata_json": audit_json(self.gauge_protocol_metadata),
                    "sample_context_json": audit_json(sample),
                    "gauge_connection_epoch": self.gauge_connection_epoch,
                    "clock_alignment": "host_receipt_only_not_hardware_synchronized",
                })
            except OSError as error:
                self._trajectory_failure(error)

    def _on_gauge_status(self, message: str, _level: str):
        if not self._accept_gauge_sender():
            return
        self._log(message)
        if "失败" in message:
            self._reset_gauge_reference("gauge_read_failure")
            if self.gauge_reader is not None:
                self.gauge_reader.stop()
                if not self.gauge_reader.wait(1000):
                    self.gauge_value.setText("测力计出错，等待串口退出")
                    return
            self.gauge_reader = None; self.gauge_connect.setText("连接测力计")
            self._apply_gauge_controls()

    def _on_gauge_finished(self):
        if self.sender() is not self.gauge_reader:
            return
        self.gauge_reader = None
        self._reset_gauge_reference("gauge_thread_finished")
        self.gauge_connect.setText("连接测力计")
        self.gauge_value.setText("测力计：未连接")
        self._apply_gauge_controls()

    def _start_session(self):
        if self.session_started:
            return
        if not self.device_sn.text().strip():
            QtWidgets.QMessageBox.warning(self, "缺少设备编号", "请先填写设备编号，以免不同板子的标定数据混在一起。")
            return
        output = Path(self.output_dir.text()).expanduser()
        try:
            output.mkdir(parents=True, exist_ok=True)
        except OSError as error:
            QtWidgets.QMessageBox.critical(self, "无法创建目录", str(error)); return
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_sn = re.sub(r"[^0-9A-Za-z_.-]+", "_", self.device_sn.text().strip())
        self.trajectory_error = None
        try:
            self.trajectory_writer = CalibrationTrajectoryWriter(output, self.device_sn.text())
            unique_stamp = f"{stamp}_{self.trajectory_writer.session_id}"
            self.detail_path = output / f"H3_calibration_{safe_sn}_{unique_stamp}_frames.csv"
            self.summary_path = output / f"H3_calibration_{safe_sn}_{unique_stamp}_summary.csv"
            self._write_headers()
        except OSError as error:
            self._trajectory_failure(error)
            return
        self.steps = build_steps(self.preset.currentText())
        self.step_index = 0; self.session_started = True; self.capturing = False
        self.stream_paused = False; self.capture_started_at = None; self.capture_rows = []
        self._reset_gauge_reference("new_calibration_session", new_connection=False)
        if not self.session_started:
            return
        self.start_session.setEnabled(False); self.preset.setEnabled(False)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button): widget.setEnabled(True)
        self._set_session_metadata_locked(True)
        self.trajectory_flush_timer.start()
        if not self._trajectory_event("session_start", details={
            "reference_mode": "gauge_serial" if self.reference_mode.currentIndex() == 1 else "manual",
            "manual_reference_policy": "plateau_reading_only_not_continuous_ground_truth",
            "clock_alignment": "host_receipt_only_not_hardware_synchronized",
            "detail_path": str(self.detail_path), "summary_path": str(self.summary_path),
        }):
            return
        self._show_step()
        if not self.session_started:
            return
        self._log(f"采集任务已创建，共 {len(self.steps)} 步。明细：{self.detail_path}")
        self._log(f"全程轨迹：{self.trajectory_writer.sensor_path}；测力计与事件记录另存同名前缀文件")

    def _set_session_metadata_locked(self, locked: bool):
        for widget in (self.device_sn, self.silicone_batch, self.magnet_batch, self.operator,
                       self.temperature, self.note, self.preset, self.output_dir,
                       self.reference_mode, self.gauge_scale):
            widget.setEnabled(not locked)
        self._apply_gauge_controls()

    def _trajectory_event(self, event: str, *, details=None) -> bool:
        if self.trajectory_writer is None or self.trajectory_writer.closed:
            return True
        step = self._current_step()
        try:
            self.trajectory_writer.event(
                event, step_index=self.step_index + 1 if step is not None else None,
                target_n=step.target_n if step is not None else None, details=details,
            )
            return True
        except OSError as error:
            self._trajectory_failure(error)
            return False

    def _flush_trajectory(self):
        if self.trajectory_writer is None or self.trajectory_writer.closed:
            return
        try:
            self.trajectory_writer.flush()
        except OSError as error:
            self._trajectory_failure(error)

    def _trajectory_failure(self, error):
        """Stop explicitly: a disk error must never be reported as saved data."""
        self.trajectory_error = str(error)
        self.trajectory_flush_timer.stop()
        self.session_started = False; self.capturing = False; self.stream_paused = False
        self.capture_started_at = None; self.capture_rows = []
        self.start_session.setEnabled(True)
        self._set_session_metadata_locked(False)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button):
            widget.setEnabled(False)
        self.progress.setValue(min(999, int(1000 * self.step_index / max(len(self.steps), 1))))
        self.step_title.setText("写盘失败，采集已停止")
        self.step_instruction.setText(
            f"文件保存可能不完整，请保留已有文件并检查磁盘空间与目录权限，再创建新任务。错误：{error}"
        )
        self._log(f"写盘失败：采集已停止，不能确认文件完整保存：{error}")
        if self.trajectory_writer is not None:
            try:
                self.trajectory_writer.close()
            except OSError as close_error:
                self._log(f"关闭轨迹文件也未能完整刷新：{close_error}")

    def _gauge_reference(self, frame_received_at: float):
        if not self.gauge_history:
            return None, None, "gauge_missing", None
        prior = [sample for sample in self.gauge_history if sample["received_at"] <= frame_received_at]
        if not prior:
            return None, None, "gauge_future_event_not_used", None
        sample = max(prior, key=lambda item: item["received_at"])
        age = frame_received_at - sample["received_at"]
        if not np.isfinite(age) or not np.isfinite(sample["value_n"]):
            return None, age, "gauge_invalid_value_not_used", sample
        if age > 1.0:
            return None, age, "gauge_stale_event_not_used", sample
        return sample["value_n"], age, "gauge_latest_prior_host_receipt", sample

    def _cancel_capture_for_reference(self, reason: str):
        discarded = len(self.capture_rows)
        self.capturing = False; self.capture_started_at = None; self.capture_rows = []
        self.progress.setValue(0)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button):
            widget.setEnabled(True)
        if not self._trajectory_event("capture_cancel", details={"reason": reason, "discarded_frames": discarded}):
            return
        self.step_instruction.setText(
            "本次3秒窗口内缺少有效测力计参考，未保存均值；采集任务保留。"
            "请恢复测力计数据、保持当前点载荷稳定，再点击“载荷稳定，采样3秒”重新采集。"
        )
        self._log(f"测力计参考无效（{reason}）：本步窗口已取消，丢弃{discarded}帧，等待手动重采")

    def _write_headers(self):
        detail_header = [
            "local_time_iso", "device_ms", "device_sn", "silicone_batch", "magnet_assembly", "operator",
            "preset", "purpose", "step_index", "step_total", "repeat", "position", "direction", "target_n",
            "reference_mode", "reference_force_n", "temperature_c", "note", "upper_model_n",
            "upper_model_confidence", "upper_model_load_score", "upper_model_soft_gate_rescue",
            "upper_regression_unclamped_n", "upper_regression_clamped_n",
            "upper_direction_invariant_fallback_n", "upper_release_similarity_ratio", "upper_gate_reason",
            "firmware_fx", "firmware_fy", "firmware_fz", "gauge_raw",
            *RAW_COLUMNS, *DELTA_COLUMNS,
        ]
        summary_header = [
            "completed_time_iso", "device_sn", "preset", "purpose", "step_index", "step_total", "repeat",
            "position", "direction", "target_n", "reference_mode", "reference_median_n", "reference_range_n",
            "temperature_c", "sample_count", "upper_model_median_n", "upper_model_load_score_median",
            "upper_model_soft_gate_rescue_frames", "firmware_fz_median_n", "raw_activity_median",
            "upper_regression_unclamped_median_n", "upper_direction_invariant_fallback_median_n",
            "upper_release_similarity_ratio_median", "upper_gate_reasons",
            "quality", "note", *[f"median_{name}" for name in DELTA_COLUMNS],
        ]
        with self.detail_path.open("w", newline="", encoding="utf-8-sig") as output:
            csv.DictWriter(output, fieldnames=detail_header).writeheader()
        with self.summary_path.open("w", newline="", encoding="utf-8-sig") as output:
            csv.DictWriter(output, fieldnames=summary_header).writeheader()

    def _current_step(self) -> CaptureStep | None:
        return self.steps[self.step_index] if 0 <= self.step_index < len(self.steps) else None

    def _show_step(self):
        step = self._current_step()
        if step is None:
            self._finish_session(); return
        self.step_title.setText(
            f"第 {self.step_index + 1}/{len(self.steps)} 步 · {step.position} · {step.target_n:g} N · {step.direction} · 第{step.repeat}轮"
        )
        self.step_instruction.setText(
            "调整测力计到目标载荷并保持位置不动。数值稳定后点击“载荷稳定，采样3秒”；"
            "采样期间不要移动手指、工装、线缆。低于0.1N时务必使用足够分辨率的测力计。"
        )
        self.manual_force.setValue(step.target_n)
        self.progress.setValue(int(1000 * self.step_index / max(len(self.steps), 1)))
        self._trajectory_event("step_selected", details={"position": step.position, "direction": step.direction})

    def _begin_capture(self):
        if not self.session_started or self.capturing or self.stream_paused:
            return
        if self.reference_mode.currentIndex() == 1 and self._gauge_reference(time.time())[0] is None:
            QtWidgets.QMessageBox.warning(self, "没有测力计数据", "最近1秒没有收到有效测力计数值。请检查测力计连接，恢复连续数据后再重采当前点。")
            return
        self.capture_rows = []; self.capture_started_at = time.time(); self.capturing = True
        self.capture_reference_mode = self.reference_mode.currentIndex()
        self.capture_button.setEnabled(False); self.skip_button.setEnabled(False); self.previous_button.setEnabled(False)
        if not self._trajectory_event("capture_start", details={"reference_mode": self.capture_reference_mode}):
            return
        self._log("开始3秒稳定采样")

    def pause_for_stream_gap(self, message: str):
        """Discard only the unfinished window while a recoverable stream gap lasts."""
        if not self.session_started:
            return
        already_paused = self.stream_paused
        discarded = len(self.capture_rows) if self.capturing else 0
        self.capturing = False
        self.capture_started_at = None
        self.capture_rows = []
        self.stream_paused = True
        for widget in (self.previous_button, self.capture_button, self.skip_button):
            widget.setEnabled(False)
        self.stop_button.setEnabled(True)
        self.progress.setValue(0)
        if not self._trajectory_event("stream_pause", details={
            "message": message, "discarded_frames": discarded, "already_paused": already_paused,
        }):
            return
        self.step_instruction.setText(
            "通信暂时中断，本步未完成，不生成均值；此前已完成步骤保留。"
            "等待通信恢复后，确认当前点载荷稳定，再重新采样完整3秒。"
        )
        if already_paused:
            self._log(f"通信仍在恢复：{message}")
        else:
            self._log(f"通信间断：本步未完成，已丢弃本次窗口的{discarded}帧；采集任务保留。{message}")

    def resume_after_stream_gap(self):
        """Offer a fresh manual capture of the same point after new data arrives."""
        if not self.session_started or not self.stream_paused:
            return
        self.stream_paused = False
        self.capturing = False
        self.capture_started_at = None
        self.capture_rows = []
        manual_reference = self.manual_force.value()
        self._show_step()
        if not self.session_started:
            return
        self.manual_force.setValue(manual_reference)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button):
            widget.setEnabled(True)
        self.progress.setValue(0)
        if not self._trajectory_event("stream_resume", details={"requires_manual_recapture": True}):
            return
        self.step_instruction.setText(
            "通信恢复，请保持当前点载荷稳定后点击“载荷稳定，采样3秒”重新采样完整3秒；"
            "此前已完成步骤保留，本步尚未保存。"
        )
        self._log("通信已恢复；停留在当前点，等待手动重新采样完整3秒")

    def feed_frame(
        self,
        frame,
        tare_raw,
        model_fz: float,
        confidence: float,
        model_load_score: float = float("nan"),
        soft_gate_rescue: bool = False,
        regression_unclamped_n: float = float("nan"),
        regression_clamped_n: float = float("nan"),
        invariant_fallback_n: float = float("nan"),
        release_similarity_ratio: float = float("nan"),
        gate_reason: str = "",
        *,
        audit_context=None,
    ):
        if not self.session_started:
            return
        raw = np.asarray(frame.raw, dtype=float).reshape(-1)
        reference = np.zeros_like(raw) if tare_raw is None else np.asarray(tare_raw, dtype=float).reshape(-1)
        delta = raw - reference
        gauge_mode = self.reference_mode.currentIndex() == 1
        gauge_n, gauge_age, gauge_provenance, gauge_sample = self._gauge_reference(frame.received_at)
        reference_n = gauge_n if gauge_mode else self.manual_force.value()
        row = {
            "local_time_iso": datetime.fromtimestamp(frame.received_at).isoformat(timespec="milliseconds"),
            "device_ms": frame.device_ms, "device_sn": self.device_sn.text().strip(),
            "silicone_batch": self.silicone_batch.text().strip(), "magnet_assembly": self.magnet_batch.text().strip(),
            "operator": self.operator.text().strip(), "preset": self.preset.currentText(),
            "purpose": self._current_step().purpose, "step_index": self.step_index + 1, "step_total": len(self.steps),
            "repeat": self._current_step().repeat, "position": self._current_step().position,
            "direction": self._current_step().direction, "target_n": self._current_step().target_n,
            "reference_mode": "gauge_serial" if gauge_mode else "manual", "reference_force_n": reference_n,
            "temperature_c": self.temperature.value(), "note": self.note.text().strip(),
            "upper_model_n": model_fz, "upper_model_confidence": confidence,
            "upper_model_load_score": model_load_score,
            "upper_model_soft_gate_rescue": int(bool(soft_gate_rescue)),
            "upper_regression_unclamped_n": regression_unclamped_n,
            "upper_regression_clamped_n": regression_clamped_n,
            "upper_direction_invariant_fallback_n": invariant_fallback_n,
            "upper_release_similarity_ratio": release_similarity_ratio,
            "upper_gate_reason": gate_reason,
            "firmware_fx": frame.force[0], "firmware_fy": frame.force[1], "firmware_fz": frame.force[2],
            "gauge_raw": gauge_sample["raw_text"] if gauge_mode and gauge_sample is not None else "",
        }
        row.update(dict(zip(RAW_COLUMNS, raw.tolist())))
        row.update(dict(zip(DELTA_COLUMNS, delta.tolist())))
        trajectory_row = {key: value for key, value in row.items() if key != "local_time_iso"}
        context = dict(audit_context or {})
        trajectory_row.update({
            "host_received_at_unix_s": frame.received_at,
            "host_received_time_iso": received_time_iso(frame.received_at),
            "trajectory_phase": "stream_paused" if self.stream_paused else "stable_capture" if self.capturing else "between_steps",
            "capturing": int(self.capturing), "stream_paused": int(self.stream_paused),
            "reference_force_n": gauge_n if gauge_mode else None,
            "reference_provenance": gauge_provenance if gauge_mode else "manual_plateau_reading_not_continuous_reference",
            "manual_plateau_reading_n": None if gauge_mode else self.manual_force.value(),
            "gauge_host_received_at_unix_s": gauge_sample["received_at"] if gauge_mode and gauge_sample is not None else None,
            "gauge_age_s": gauge_age if gauge_mode else None,
            "gauge_raw": gauge_sample["raw_text"] if gauge_mode and gauge_sample is not None else "",
            "gauge_event_json": audit_json(gauge_sample if gauge_mode else None),
            "clock_alignment": "host_receipt_only_not_hardware_synchronized",
            "host_tare_valid": int(tare_raw is not None),
            "host_tare_epoch": context.get("host_tare_epoch"),
            "effective_baseline_epoch": context.get("effective_baseline_epoch"),
            "firmware_id": context.get("firmware_id"),
            "firmware_baseline_epoch": context.get("firmware_baseline_epoch"),
            "manual_tare_raw_json": audit_json(context.get("manual_tare_raw24")),
            "effective_tare_raw_json": audit_json(context.get("effective_tare_raw24", tare_raw)),
            "residual_offset_raw_json": audit_json(context.get("release_offset_raw24")),
            "audit_context_json": audit_json(context),
        })
        if tare_raw is None:
            trajectory_row.update({name: None for name in DELTA_COLUMNS})
        if self.trajectory_writer is not None:
            try:
                self.trajectory_writer.sensor(trajectory_row)
            except OSError as error:
                self._trajectory_failure(error)
                return
        if not self.capturing or self.stream_paused:
            return
        if self.capture_reference_mode != self.reference_mode.currentIndex():
            self._cancel_capture_for_reference("reference_mode_changed")
            return
        if gauge_mode and gauge_n is None:
            self._cancel_capture_for_reference(gauge_provenance)
            return
        self.capture_rows.append(row)
        elapsed = time.time() - self.capture_started_at
        self.progress.setValue(int(1000 * min(elapsed / CAPTURE_SECONDS, 1.0)))
        self.step_instruction.setText(f"正在稳定采样：{elapsed:.1f}/{CAPTURE_SECONDS:.0f} 秒，已收到 {len(self.capture_rows)} 个配对帧")
        if elapsed >= CAPTURE_SECONDS:
            self._complete_capture()

    def _complete_capture(self):
        if not self.capturing or self.stream_paused:
            return
        self.capturing = False
        self.capture_button.setEnabled(True); self.skip_button.setEnabled(True); self.previous_button.setEnabled(True)
        if len(self.capture_rows) < MIN_CAPTURE_FRAMES:
            if not self._trajectory_event("capture_cancel", details={"reason": "insufficient_frames", "frames": len(self.capture_rows)}):
                return
            self._log(f"本步只有 {len(self.capture_rows)} 帧，少于 {MIN_CAPTURE_FRAMES} 帧，未保存；请检查串口并重采")
            self._show_step(); return
        try:
            with self.detail_path.open("a", newline="", encoding="utf-8-sig") as output:
                writer = csv.DictWriter(output, fieldnames=list(self.capture_rows[0].keys()))
                writer.writerows(self.capture_rows)
        except OSError as error:
            self._trajectory_failure(error)
            return
        references = np.asarray([row["reference_force_n"] for row in self.capture_rows], dtype=float)
        models = np.asarray([row["upper_model_n"] for row in self.capture_rows], dtype=float)
        load_scores = np.asarray([row["upper_model_load_score"] for row in self.capture_rows], dtype=float)
        rescue_count = sum(int(row["upper_model_soft_gate_rescue"]) for row in self.capture_rows)
        regression_unclamped = np.asarray([row["upper_regression_unclamped_n"] for row in self.capture_rows], dtype=float)
        invariant_fallback = np.asarray([row["upper_direction_invariant_fallback_n"] for row in self.capture_rows], dtype=float)
        release_ratios = np.asarray([row["upper_release_similarity_ratio"] for row in self.capture_rows], dtype=float)
        gate_reasons = sorted({str(row["upper_gate_reason"]) for row in self.capture_rows if row["upper_gate_reason"]})
        firmware = np.asarray([row["firmware_fz"] for row in self.capture_rows], dtype=float)
        delta = np.asarray([[row[name] for name in DELTA_COLUMNS] for row in self.capture_rows], dtype=float)
        step = self._current_step()
        reference_range = float(np.ptp(references))
        tolerance = max(0.02, 0.02 * max(abs(float(np.median(references))), 1.0))
        quality = "OK" if reference_range <= tolerance else "REFERENCE_UNSTABLE"
        summary = {
            "completed_time_iso": datetime.now().isoformat(timespec="seconds"), "device_sn": self.device_sn.text().strip(),
            "preset": self.preset.currentText(), "purpose": step.purpose, "step_index": self.step_index + 1,
            "step_total": len(self.steps), "repeat": step.repeat, "position": step.position,
            "direction": step.direction, "target_n": step.target_n,
            "reference_mode": self.capture_rows[0]["reference_mode"], "reference_median_n": float(np.median(references)),
            "reference_range_n": reference_range, "temperature_c": self.temperature.value(),
            "sample_count": len(self.capture_rows), "upper_model_median_n": float(np.median(models)),
            "upper_model_load_score_median": float(np.nanmedian(load_scores)),
            "upper_model_soft_gate_rescue_frames": int(rescue_count),
            "firmware_fz_median_n": float(np.median(firmware)),
            "raw_activity_median": float(np.median(np.linalg.norm(delta.reshape(-1, 8, 3), axis=2).sum(axis=1))),
            "upper_regression_unclamped_median_n": float(np.nanmedian(regression_unclamped)),
            "upper_direction_invariant_fallback_median_n": float(np.nanmedian(invariant_fallback)),
            "upper_release_similarity_ratio_median": float(np.nanmedian(release_ratios)),
            "upper_gate_reasons": "|".join(gate_reasons),
            "quality": quality, "note": self.note.text().strip(),
        }
        summary.update({f"median_{name}": value for name, value in zip(DELTA_COLUMNS, np.median(delta, axis=0))})
        try:
            with self.summary_path.open("a", newline="", encoding="utf-8-sig") as output:
                writer = csv.DictWriter(output, fieldnames=list(summary.keys())); writer.writerow(summary)
        except OSError as error:
            self._trajectory_failure(error)
            return
        if not self._trajectory_event("capture_complete", details={"frames": len(self.capture_rows), "quality": quality}):
            return
        self._log(
            f"已保存 {step.position} {step.target_n:g}N {step.direction}：{len(self.capture_rows)}帧，"
            f"参考中位数 {np.median(references):.4f}N，质量 {quality}"
        )
        self.step_index += 1
        self._show_step()

    def _previous(self):
        if self.session_started and not self.capturing and not self.stream_paused and self.step_index > 0:
            self.step_index -= 1; self._show_step(); self._log("返回上一步；重新采集会在文件中追加一组记录")

    def _skip(self):
        if self.session_started and not self.capturing and not self.stream_paused:
            step = self._current_step(); self._log(f"已跳过：{step.position} {step.target_n:g}N {step.direction}")
            if not self._trajectory_event("step_skip"):
                return
            self.step_index += 1; self._show_step()

    def _stop_session(self):
        if self.capturing:
            QtWidgets.QMessageBox.warning(self, "正在采样", "请等待当前3秒采样结束后再停止。")
            return
        self._finish_session(stopped=True)

    def _finish_session(self, stopped: bool = False):
        if not self.session_started:
            return
        if not self._trajectory_event("session_stop" if stopped else "session_complete", details={
            "discarded_capture_frames": len(self.capture_rows) if self.capturing else 0,
            "stream_paused": self.stream_paused,
        }):
            return
        self.trajectory_flush_timer.stop()
        if self.trajectory_writer is not None:
            try:
                self.trajectory_writer.close()
            except OSError as error:
                self._trajectory_failure(error)
                return
        self.session_started = False; self.capturing = False
        self.stream_paused = False; self.capture_started_at = None; self.capture_rows = []
        self.start_session.setEnabled(True); self.preset.setEnabled(True)
        self._set_session_metadata_locked(False)
        for widget in (self.previous_button, self.capture_button, self.skip_button, self.stop_button): widget.setEnabled(False)
        self.progress.setValue(min(999, int(1000 * self.step_index / max(len(self.steps), 1))) if stopped else 1000)
        self.step_title.setText("采集已停止，数据已保存" if stopped else "全部采集完成，数据已保存")
        self.step_instruction.setText(f"明细文件：{self.detail_path}\n汇总文件：{self.summary_path}")
        self._log("采集结束；已有数据均已写入磁盘")

    def closeEvent(self, event):
        if self.capturing:
            QtWidgets.QMessageBox.warning(self, "正在采样", "请先等待当前3秒采样完成。")
            event.ignore(); return
        if self.gauge_reader is not None:
            self.gauge_reader.stop()
            if not self.gauge_reader.wait(1000):
                self._log("测力计串口尚未退出，请稍后再关闭窗口")
                event.ignore(); return
            self.gauge_reader = None
        self._finish_session(stopped=True)
        event.accept()

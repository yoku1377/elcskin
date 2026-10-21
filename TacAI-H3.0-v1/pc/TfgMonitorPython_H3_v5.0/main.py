"""TacAI H3 Python/OpenGL monitor for the dual-button device."""

import csv
import importlib.util
import json
import math
import re
import os
from pathlib import Path
import sys
import time
from collections import deque
from datetime import datetime

# The Qt platform path must be fixed before importing pyqtgraph, because
# pyqtgraph imports PyQt5 as part of its own module initialization.
if getattr(sys, "frozen", False):
    runtime_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
    qt_platform_candidates = (
        runtime_root / "PyQt5" / "Qt5" / "plugins" / "platforms",
        runtime_root / "PyQt5" / "Qt" / "plugins" / "platforms",
        runtime_root / "qt5_plugins" / "platforms",
    )
else:
    pyqt5_spec = importlib.util.find_spec("PyQt5")
    pyqt5_root = Path(pyqt5_spec.origin).parent if pyqt5_spec and pyqt5_spec.origin else None
    qt_platform_candidates = (
        pyqt5_root / "Qt5" / "plugins" / "platforms",
        pyqt5_root / "Qt" / "plugins" / "platforms",
    ) if pyqt5_root else ()
for candidate in qt_platform_candidates:
    if (candidate / "qwindows.dll").exists():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(candidate)
        break

import numpy as np
import pyqtgraph.opengl as gl
import serial
import serial.tools.list_ports
from PyQt5 import QtCore, QtGui, QtWidgets
from h22_point_cloud import CHANNEL_CENTRES, TactilePointCloud
from h22_shell import halo_item, shell_item
from h3_v3_3_force_model import MODEL_ID as FORCE_MODEL_ID, REGION_NAMES
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame, StreamPairer, build_tare, evaluate_raw_force
from firmware_updater import FirmwareUpgradeDialog
from ch347_spi import CH347Spi, list_ch347_devices, USB_OPEN_HELP
from ch341_spi import CH341Spi, list_ch341_devices, USB_OPEN_HELP as CH341_USB_OPEN_HELP
from spi_iap_protocol import SPIIapClient
from local_high_force import (
    LOCAL_FORCE_CSV_COLUMNS, LocalHighForceRuntime, export_row,
    load_local_model,
)
from calibration_acquisition import CalibrationAcquisitionDialog
from automatic_compensation import AutomaticCompensationModel
from baseline_quality import (BASELINE_CSV_COLUMNS, DEFAULT_POLICY, evaluate_window, quality_text)
from force_trace import ForceTrace
from residual_zero import MAX_RESIDUAL_N
from release_baseline import ReleaseBaselineTracker
from consistency_report import generate_consistency_report
from consistency_report_dialog import ConsistencyReportDialog
from test_protocol import (
    ACCURACY_SEQUENCE,
    ACCURACY_TOTAL_POINTS,
    EMI_BASELINE_S,
    EMI_EXPOSURE_S,
    FAST_RECOVERY_LIMIT_N,
    HIGH_FORCE_TRAJECTORIES,
    RECOVERY_STEADY_LIMIT_N,
    TestProtocol,
)


CHANNELS = 8
SPATIAL_ALLOCATION_EXPONENT = 1.35
SPATIAL_ALLOCATION_METHOD = "H3_MAG_NORM_NOISE_FLOOR_P135_V1"
RAW_COUNT = CHANNELS * 3
RANGE_N = (0.0, 35.0)
FIELD_RISE_TIME_S = 0.075
FIELD_RELEASE_TIME_S = 0.120
FIELD_MAX_FRAME_DT_S = 0.050
FIELD_ZERO_SNAP = 0.015
ANIMATION_RELEASE_HOLD_S = 0.0
ANIMATION_ACTIVE_N = 0.03
TARE_FRAME_COUNT = 20
TARE_MIN_FRAMES = 10
TARE_MAX_FORCE_RANGE_N = 0.60
TARE_MAX_FRAME_AGE_S = 1.00
PAIRED_DATA_TIMEOUT_S = 1.00
RAW_ACTIVITY_WARNING = 1500.0
MAGNETIC_COHERENCE_WARNING = 0.85
ZERO_SNAP_N = 0.01
RAW_TARE_MODE = "H3 V3-3 Fz 候选模型（0.2–30 N）"
MONITOR_VERSION = "5.3.28-zp500n-stream"
RELEASE_ZERO_CSV_COLUMNS = ["release_zero_enabled", "release_zero_applied",
                            "release_zero_reason", "release_zero_threshold_n"]
RAW_BASELINE_CSV_COLUMNS = ["effective_baseline_epoch", "manual_reference_model_n", "raw_baseline_update_allowed",
    *[f"S{i}_manual_tare_dB{axis}" for i in range(1,9) for axis in 'xyz'],
    *[f"S{i}_manual_retared_dB{axis}" for i in range(1,9) for axis in 'xyz'],
    *[f"S{i}_release_offset_dB{axis}" for i in range(1,9) for axis in 'xyz']]
HIGH_FORCE_CSV_COLUMNS = [
    "high_force_trajectory", "high_force_session_id", "reference_force_source",
    "protocol_clock_source", "protocol_phase_elapsed_s", "protocol_session_elapsed_s",
]
DIAGNOSTIC_CSV_COLUMNS = [
    "acquisition_diag_available", "firmware_diagnostic_id", "firmware_valid_mask",
    "firmware_error_count", "firmware_baseline_epoch", "acquisition_diag_device_ms",
    "acquisition_diag_status", "host_tare_valid", "firmware_diag_raw_line",
    "host_tare_epoch",
]
FIRMWARE_COMPAT_MODE = "固件原始 Fz 分量（诊断；非合力）"
MODEL_STATUS = FORCE_MODEL_ID
ACCEPTANCE_MODEL_STATUS = "FINAL_H3_VALIDATED_NO_DEADBAND_0P01N"

def clamp(value, low=0.0, high=35.0):
    return max(low, min(high, float(value)))


def dominant_half(raw, tare_raw):
    """RAW identifies the loaded half only; it never calculates Fz."""
    if tare_raw is None:
        return None
    magnitudes = np.linalg.norm((raw - tare_raw).reshape(CHANNELS, 3), axis=1)
    return "front" if magnitudes[:4].sum() >= magnitudes[4:].sum() else "back"


def cell_force_estimate(raw, tare_raw, total_fz, noise_floor=None):
    """Allocate total Fz over the eight physical Hall locations.

    The force value is never inferred here.  Per-cell |Delta-B| only decides
    where an already-known total force is drawn.  A tare-time noise floor keeps
    quiet sensors from stealing the peak marker after a new zero operation.
    """
    if tare_raw is None or total_fz <= 0:
        return np.zeros(CHANNELS, dtype=float)
    magnitudes = np.linalg.norm((raw - tare_raw).reshape(CHANNELS, 3), axis=1)
    if noise_floor is not None:
        floor = np.asarray(noise_floor, dtype=float).reshape(CHANNELS)
        magnitudes = np.maximum(magnitudes - floor, 0.0)
    # The calibrated firmware Fz decides whether a load exists. RAW only
    # distributes that total, so a second local dead zone is inappropriate.
    strengths = magnitudes ** SPATIAL_ALLOCATION_EXPONENT
    total = strengths.sum()
    if total < 1e-9:
        strengths = np.full(CHANNELS, 1.0 / CHANNELS)
    else:
        strengths /= total
    return strengths * total_fz


def legacy_map_to_physical(frame):
    """Collapse a legacy 4x3 map into the real 4x2 sensor geometry."""
    legacy = np.asarray(frame, dtype=float).reshape(4, 3, 3)
    physical = np.empty((4, 2, 3), dtype=float)
    physical[:, 0] = legacy[:, 0] + 0.5 * legacy[:, 1]
    physical[:, 1] = legacy[:, 2] + 0.5 * legacy[:, 1]
    return physical


def firmware_fz_after_tare(firmware_fz, tare_fz=0.0):
    """Return authoritative NO8 firmware Fz after a display tare."""
    value = float(firmware_fz) - float(tare_fz)
    if abs(value) < ZERO_SNAP_N:
        value = 0.0
    return clamp(value, *RANGE_N)


def raw_activity_metrics(raw, tare_raw=None):
    """Return total RAW activity and directional coherence (0..1).

    A moving external magnetic field often moves all Hall channels in nearly
    the same direction.  Local silicone deformation normally produces a less
    coherent spatial pattern.  The score is diagnostic only.
    """
    reference = np.zeros(RAW_COUNT, dtype=float) if tare_raw is None else np.asarray(tare_raw, dtype=float)
    vectors = (np.asarray(raw, dtype=float) - reference).reshape(CHANNELS, 3)
    magnitudes = np.linalg.norm(vectors, axis=1)
    activity = float(magnitudes.sum())
    coherence = 0.0 if activity <= 1e-9 else float(np.linalg.norm(vectors.sum(axis=0)) / activity)
    return activity, float(np.clip(coherence, 0.0, 1.0))


def spatial_map_for_total(raw, tare_raw, total_fz, noise_floor=None):
    """Build a display-only map at the eight real Hall sensor locations."""
    total_fz = clamp(total_fz, *RANGE_N)
    if total_fz <= 0.0:
        return np.zeros((4, 2, 3), dtype=float), "当前动画总力为 0，动画归零"

    reference = np.zeros(RAW_COUNT, dtype=float) if tare_raw is None else np.asarray(tare_raw, dtype=float)
    cell_forces = cell_force_estimate(raw, reference, total_fz, noise_floor)
    force_map = np.zeros((4, 2, 3), dtype=float)
    force_map[..., 2] = cell_forces.reshape(4, 2)
    return force_map, "4×2 RAW 通道视图（局部 ΔB 分配，固件 Fz 控制有无）"


def color_for_force(force, maximum=8.0):
    t = clamp(force / maximum, 0.0, 1.0)
    if t < 0.65:
        p = t / 0.65
        return (0.12 + 0.12 * p, 0.64 + 0.20 * p, 0.76 - 0.12 * p, 1.0)
    p = (t - 0.65) / 0.35
    return (0.24 + 0.70 * p, 0.84 - 0.16 * p, 0.64 - 0.42 * p, 1.0)


def chamfered_outline(cx, cy, half_width, half_height, corner, z):
    """Eight-point outline in display coordinates, not mechanical dimensions."""
    return np.asarray([
        [cx - half_width + corner, cy - half_height, z],
        [cx + half_width - corner, cy - half_height, z],
        [cx + half_width, cy - half_height + corner, z],
        [cx + half_width, cy + half_height - corner, z],
        [cx + half_width - corner, cy + half_height, z],
        [cx - half_width + corner, cy + half_height, z],
        [cx - half_width, cy + half_height - corner, z],
        [cx - half_width, cy - half_height + corner, z],
    ], dtype=np.float32)


def chamfered_top_mesh(cx, cy, half_width, half_height, corner, z):
    vertices = chamfered_outline(cx, cy, half_width, half_height, corner, z)
    faces = np.asarray([[0, index, index + 1] for index in range(1, 7)], dtype=np.uint32)
    return gl.MeshData(vertexes=vertices, faces=faces)


def chamfered_side_mesh(cx, cy, half_width, half_height, corner, bottom, top, base_color=None):
    lower = chamfered_outline(cx, cy, half_width, half_height, corner, bottom)
    upper = chamfered_outline(cx, cy, half_width, half_height, corner, top)
    faces, face_colors = [], []
    light = np.asarray((-0.52, 0.85), dtype=float)
    for index in range(8):
        next_index = (index + 1) % 8
        faces.extend([[index, next_index, next_index + 8], [index, next_index + 8, index + 8]])
        if base_color is not None:
            edge = upper[next_index, :2] - upper[index, :2]
            normal = np.asarray((edge[1], -edge[0]), dtype=float)
            normal /= max(float(np.linalg.norm(normal)), 1e-9)
            tone = 0.78 + 0.18 * float(np.dot(normal, light))
            shaded = (*[channel * tone for channel in base_color[:3]], base_color[3])
            face_colors.extend((shaded, shaded))
    return gl.MeshData(
        vertexes=np.vstack((lower, upper)),
        faces=np.asarray(faces, dtype=np.uint32),
        faceColors=np.asarray(face_colors, dtype=np.float32) if base_color is not None else None,
    )


def merge_static_meshes(meshes):
    """Batch one material across the eight display cells into one GL item."""
    vertices, faces, face_colors, offset = [], [], [], 0
    for mesh in meshes:
        mesh_vertices = mesh.vertexes()
        vertices.append(mesh_vertices)
        faces.append(mesh.faces() + offset)
        if mesh.faceColors() is not None:
            face_colors.append(mesh.faceColors())
        offset += len(mesh_vertices)
    if face_colors and len(face_colors) != len(meshes):
        raise ValueError("Static meshes must use either all or no face colors")
    return gl.MeshData(
        vertexes=np.vstack(vertices), faces=np.vstack(faces),
        faceColors=np.vstack(face_colors) if face_colors else None,
    )


def sensor_label_texture(positions):
    """One static RGBA atlas for the eight real locations and deck orientation."""
    width, height = 512, 1024
    image = QtGui.QImage(width, height, QtGui.QImage.Format_RGBA8888)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
    label_font = QtGui.QFont("Segoe UI")
    label_font.setPixelSize(42)
    label_font.setWeight(QtGui.QFont.DemiBold)
    painter.setFont(label_font)
    painter.setPen(QtGui.QColor(190, 221, 226, 240))
    for index, (x, y) in enumerate(positions):
        px = (x + 1.35) * width / 2.7
        py = (2.35 - (y - 0.47)) * height / 4.7
        painter.save()
        painter.translate(px, py)
        painter.rotate(-90)
        painter.drawText(
            QtCore.QRectF(-50, -26, 100, 52),
            QtCore.Qt.AlignCenter, f"S{index + 1}",
        )
        painter.restore()
    orientation_font = QtGui.QFont("Segoe UI")
    orientation_font.setPixelSize(44)
    orientation_font.setWeight(QtGui.QFont.DemiBold)
    painter.setFont(orientation_font)
    painter.setPen(QtGui.QColor(164, 206, 215, 236))
    for text, y in (("前区", 2.13), ("后区", -2.13)):
        painter.save()
        painter.translate(width / 2, (2.35 - y) * height / 4.7)
        painter.rotate(-90)
        painter.drawText(QtCore.QRectF(-55, -28, 110, 56), QtCore.Qt.AlignCenter, text)
        painter.restore()
    painter.end()
    rgba = np.frombuffer(image.bits().asstring(image.byteCount()), dtype=np.uint8)
    rgba = rgba.reshape(height, image.bytesPerLine() // 4, 4)[:, :width]
    return np.flipud(rgba).transpose(1, 0, 2).copy()


def membrane_sheen_texture():
    """A restrained, static grazing reflection on the physical membrane."""
    width, height = 384, 672
    image = QtGui.QImage(width, height, QtGui.QImage.Format_RGBA8888)
    image.fill(QtCore.Qt.transparent)
    painter = QtGui.QPainter(image)
    painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
    chamfer_x = width * 0.12 / 2.6
    chamfer_y = height * 0.12 / 4.6
    path = QtGui.QPainterPath()
    points = (
        (chamfer_x, 0), (width - chamfer_x, 0),
        (width, chamfer_y), (width, height - chamfer_y),
        (width - chamfer_x, height), (chamfer_x, height),
        (0, height - chamfer_y), (0, chamfer_y),
    )
    path.moveTo(*points[0])
    for point in points[1:]:
        path.lineTo(*point)
    path.closeSubpath()
    sheen = QtGui.QLinearGradient(0, height * 0.12, width, height * 0.88)
    sheen.setColorAt(0.0, QtGui.QColor(143, 201, 211, 28))
    sheen.setColorAt(0.35, QtGui.QColor(112, 171, 180, 11))
    sheen.setColorAt(0.72, QtGui.QColor(37, 93, 109, 3))
    sheen.setColorAt(1.0, QtGui.QColor(3, 21, 29, 17))
    painter.fillPath(path, sheen)
    painter.end()
    rgba = np.frombuffer(image.bits().asstring(image.byteCount()), dtype=np.uint8)
    rgba = rgba.reshape(height, image.bytesPerLine() // 4, 4)[:, :width]
    return np.flipud(rgba).transpose(1, 0, 2).copy()


class RawForceReader(QtCore.QThread):
    paired_received = QtCore.pyqtSignal(object)
    diagnostic_received = QtCore.pyqtSignal(object)
    stream_fault = QtCore.pyqtSignal(str, str, float)
    stream_gap = QtCore.pyqtSignal(str, str, float)
    luo_force_map_received = QtCore.pyqtSignal(object, float)
    status = QtCore.pyqtSignal(str, str)

    def __init__(self, port, baudrate):
        super().__init__()
        self.port, self.baudrate, self.running = port, baudrate, True
        self.pairer = StreamPairer(max_pending=64)

    def stop(self):
        self.running = False

    def run(self):
        try:
            serial_port = serial.Serial(self.port, self.baudrate, timeout=0.1)
        except Exception as error:
            self.status.emit(f"串口打开失败：{error}", "error")
            return
        self.status.emit(f"已连接 {self.port} @ {self.baudrate}", "success")
        while self.running:
            try:
                line = serial_port.readline().decode("utf-8", errors="ignore").strip()
                if not line:
                    continue
                now = time.time()
                fields = [field.strip() for field in line.split(",")]
                if fields and fields[0] in {"RAW", "FORCE", "DIAG"}:
                    paired = self.pairer.feed_line(line, now)
                    if fields[0] == "DIAG":
                        self.diagnostic_received.emit(self.pairer.last_diagnostic)
                    if paired is not None:
                        self.paired_received.emit(paired)
                else:
                    # Exact Luo protocol: 4 rows × 3 columns × Fx/Fy/Fz.
                    # The original platform ignores non-numeric labels, takes
                    # the first 36 numbers, reshapes them and multiplies by 9.8.
                    numbers = []
                    for field in fields:
                        try:
                            numbers.append(float(field))
                        except ValueError:
                            continue
                    if len(numbers) >= 36:
                        frame = np.asarray(numbers[:36], dtype=float).reshape(4, 3, 3) * 9.8
                        self.luo_force_map_received.emit(frame, now)
            except (ValueError, IndexError, OverflowError) as error:
                self.pairer.discard_pending()
                self.stream_fault.emit(str(error), line, time.time())
                continue
            except Exception as error:
                self.status.emit(f"串口读取失败：{error}", "error")
                break
        serial_port.close()


class SpiForceReader(RawForceReader):
    """Poll complete SPI snapshots and reuse the UART pairing/quality path."""
    bridge_name = "CH347"

    def __init__(self, device_index, clock_hz):
        super().__init__(str(device_index), clock_hz)
        self.device_index = int(device_index)
        self.clock_hz = int(clock_hz)
        self._spi_recovering = False
        self._spi_has_sample = False
        self._spi_log_path = None

    def _open_spi(self):
        return CH347Spi(self.device_index, self.clock_hz)

    def _clock_description(self):
        return f"{self.clock_hz} Hz"

    def _record_spi_event(self, record):
        try:
            if self._spi_log_path is None:
                base = (Path(sys.executable).resolve().parent if getattr(sys, "frozen", False)
                        else Path(__file__).resolve().parent)
                directory = Path(os.environ.get("TACAI_STREAM_LOG_DIR", base / "logs" / "stream"))
                directory.mkdir(parents=True, exist_ok=True)
                self._spi_log_path = directory / (
                    datetime.now().strftime("%Y%m%d_%H%M%S_") + str(time.time_ns()) + "_spi.log")
            with self._spi_log_path.open("a", encoding="utf-8") as output:
                output.write(json.dumps(dict(time=datetime.now().isoformat(timespec="milliseconds"),
                                              adapter=self.bridge_name, index=self.device_index,
                                              monitor_version=MONITOR_VERSION,
                                              snapshot_ack_guard=True,
                                              detail=record), ensure_ascii=False) + "\n")
        except OSError:
            pass  # Diagnostic storage cannot change the SPI transaction.

    def _snapshot_trace(self, record):
        event = record.get("event")
        if event not in ("snapshot_retry", "snapshot_recovered", "timeout", "transport_error"):
            return
        self._record_spi_event(record)
        if event == "snapshot_retry" and not self._spi_recovering:
            self._spi_recovering = True
            message = (f"SPI 快照持续返回旧序号 {record.get('old_sequence')}，"
                       f"正在用新序号 {record.get('next_sequence')} 自动恢复；采集间断已记录")
            self.pairer.discard_pending()
            if self._spi_has_sample:
                # A bounded snapshot retry is a recoverable acquisition gap.
                # Fatal transport/diagnostic faults retain the stop-session path.
                self.stream_gap.emit(message, "", time.time())
            self.status.emit(message, "info")

    def run(self):
        try:
            with self._open_spi() as spi:
                summary = getattr(spi, "configuration_summary", None)
                if isinstance(summary, str):
                    self.status.emit(summary, "info")
                client = SPIIapClient(spi, trace=self._snapshot_trace)
                self.status.emit(f"{self.bridge_name} #{self.device_index} USB 已打开，等待传感器 SPI 响应", "info")
                last_key = None
                while self.running:
                    try:
                        sample = client.snapshot(timeout=0.6)
                    except Exception as error:
                        raise RuntimeError(
                            f"{self.bridge_name} USB 已打开，但未取得有效 SPI 快照：{error}。"
                            "已停止显示当前测量，请查看运行日志；详细采集诊断保存在程序同目录 logs/stream"
                        ) from error
                    key = (sample.device_ms, sample.frame_counter, sample.error_count,
                           sample.baseline_epoch, sample.valid_mask, sample.status)
                    if key == last_key:
                        self.msleep(40)
                        continue
                    if last_key is None or self._spi_recovering:
                        label = "SPI 采集已恢复" if self._spi_recovering else "已连接传感器"
                        self.status.emit(f"{label} · {self.bridge_name} #{self.device_index} · SPI · {self._clock_description()}", "success")
                        self._spi_recovering = False
                    self._spi_has_sample = True
                    last_key = key
                    now = time.time()
                    mask = sample.valid_mask if sample.status & 1 else 0
                    lines = [f"DIAG,{sample.device_ms},{sample.firmware_id},{mask},"
                             f"{sample.error_count},{sample.baseline_epoch}"]
                    if mask == 255 and sample.baseline_epoch > 0:
                        lines.extend((
                            f"RAW,{sample.device_ms}," + ",".join(format(v, ".9g") for v in sample.raw),
                            f"FORCE,{sample.device_ms}," + ",".join(format(v, ".9g") for v in sample.force),
                        ))
                    for line in lines:
                        paired = self.pairer.feed_line(line, now)
                        if line.startswith("DIAG,"):
                            self.diagnostic_received.emit(self.pairer.last_diagnostic)
                        if paired is not None:
                            self.paired_received.emit(paired)
                    self.msleep(40)
        except Exception as error:
            self.pairer.discard_pending()
            self.status.emit(f"SPI 采集失败：{error}", "error")


class CH341ForceReader(SpiForceReader):
    bridge_name = "CH341A"

    def __init__(self, device_index):
        super().__init__(device_index, 0)

    def _open_spi(self):
        return CH341Spi(self.device_index)

    def _clock_description(self):
        return "硬件固定时钟"


class CH347Probe(QtCore.QThread):
    detected = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def run(self):
        try:
            devices = list_ch347_devices(cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.detected.emit(devices)
        except Exception as error:
            self.failed.emit(str(error))


class CH341Probe(QtCore.QThread):
    detected = QtCore.pyqtSignal(object)
    failed = QtCore.pyqtSignal(str)

    def run(self):
        try:
            devices = list_ch341_devices(cancelled=self.isInterruptionRequested)
            if not self.isInterruptionRequested():
                self.detected.emit(devices)
        except Exception as error:
            self.failed.emit(str(error))


class ForceField(gl.GLViewWidget):
    def __init__(self, title):
        super().__init__()
        # Request 4x multisampling for the entire 3D scene (meshes as well as
        # lines).  QOpenGLWidget may negotiate fewer samples on older GPUs;
        # setFormat must run before the widget is shown/context is created.
        surface_format = self.format()
        surface_format.setSamples(4)
        self.setFormat(surface_format)
        self.setBackgroundColor((7, 17, 23))
        self._sync_h22_fov()
        self.setCameraPosition(pos=QtGui.QVector3D(0.0, 0.0, 0.0),
                               distance=5.15, elevation=13.3, azimuth=242)
        self.title = title
        self.last_zones = np.zeros(8, dtype=float)
        self._target_zones = np.zeros(8, dtype=float)
        self._display_zones = np.zeros(8, dtype=float)
        self._zone_velocity = np.zeros(8, dtype=float)
        self._last_tick_s = time.perf_counter()
        self._setup_scene()
        self._animation_timer = QtCore.QTimer(self)
        self._animation_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self._animation_timer.start()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._sync_h22_fov()
        # Keep the H2.2 vertical field legible as the panel changes aspect ratio.
        self.setCameraPosition(distance=self._fit_camera_distance())

    def _sync_h22_fov(self):
        # Three.js uses vertical FOV; pyqtgraph uses horizontal FOV.
        aspect = max(self.width(), 1) / max(self.height(), 1)
        self.opts["fov"] = math.degrees(2.0 * math.atan(
            aspect * math.tan(math.radians(38.0 / 2.0))
        ))

    def _fit_camera_distance(self):
        view_width = max(self.width(), 1)
        view_height = max(self.height(), 1)
        return max(5.15, 4.60 * view_height / view_width)

    def reset_camera(self):
        """Restore the readable, fully fitted default camera after orbit/zoom."""
        self.setCameraPosition(pos=QtGui.QVector3D(0.0, 0.0, 0.0),
                               distance=self._fit_camera_distance(),
                               elevation=13.3, azimuth=242)

    def _setup_scene(self):
        # Reuse the old H2.2 visual composition: MC-Tac reference shell,
        # almost invisible halo, and sparse bright point cloud.
        self.addItem(shell_item())
        self.addItem(halo_item())
        self.point_cloud = TactilePointCloud()
        self.addItem(self.point_cloud.item)
        self.positions = [np.array([x, -0.28, y]) for x, y in CHANNEL_CENTRES]

    def _update_force_map(self, frame, source):
        """Set an eight-point target; a 60 Hz spring renders it independently."""
        frame = np.asarray(frame, dtype=float).reshape(4, 2, 3)
        vectors = frame.reshape(8, 3)
        magnitudes = np.maximum(vectors[:, 2], 0.0)
        self.last_zones = vectors[:, 2].copy()
        self._target_zones = magnitudes.copy()
        return source

    @staticmethod
    def _spring_step(current, velocity, target, dt, smooth_time):
        """Critically damped, frame-rate-independent SmoothDamp step."""
        smooth_time = max(float(smooth_time), 1e-3)
        omega = 2.0 / smooth_time
        x = omega * dt
        decay = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = current - target
        temp = (velocity + omega * change) * dt
        next_velocity = (velocity - omega * temp) * decay
        output = target + (change + temp) * decay
        return output, next_velocity

    def _advance_animation(self):
        now = time.perf_counter()
        dt = min(max(now - self._last_tick_s, 1.0 / 240.0), FIELD_MAX_FRAME_DT_S)
        self._last_tick_s = now
        rising = self._target_zones >= self._display_zones
        smooth_time = np.where(rising, FIELD_RISE_TIME_S, FIELD_RELEASE_TIME_S)
        output = np.empty_like(self._display_zones)
        velocity = np.empty_like(self._zone_velocity)
        for index in range(8):
            output[index], velocity[index] = self._spring_step(
                self._display_zones[index], self._zone_velocity[index],
                self._target_zones[index], dt, smooth_time[index],
            )
        settled = (self._target_zones <= FIELD_ZERO_SNAP) & (output <= FIELD_ZERO_SNAP)
        output[settled] = 0.0
        velocity[settled] = 0.0
        self._display_zones = np.maximum(output, 0.0)
        self._zone_velocity = velocity
        self._render_field()

    def _render_field(self):
        magnitudes = self._display_zones
        self.point_cloud.set_loads(magnitudes)
        self.update()

    def update_reconstructed_map(self, frame):
        """Render the eight physical Hall positions."""
        return self._update_force_map(frame, "4×2 RAW 通道响应（主模型合力空间分配）")

    def update_luo_force_map(self, frame):
        """Render a legacy map after collapsing it to physical 4x2 geometry."""
        return self._update_force_map(legacy_map_to_physical(frame), "旧 4×3 帧折算到 4×2 通道视图")


class ButtonPanel(QtWidgets.QFrame):
    def __init__(self, name, source):
        super().__init__()
        self.setObjectName("buttonPanel")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel(name)
        title.setObjectName("fieldTitle")
        self.source_label = QtWidgets.QLabel(source)
        self.source_label.setObjectName("source")
        header.addWidget(title)
        header.addStretch()
        header.addWidget(self.source_label)
        layout.addLayout(header)
        self.view = ForceField(name)
        self.view.setMinimumSize(480, 410)
        layout.addWidget(self.view, 1)
        self.fz_label = QtWidgets.QLabel("0.00 N")
        self.fz_label.setObjectName("fz")
        self.detail_label = QtWidgets.QLabel("等待 RAW / FORCE")
        self.detail_label.setObjectName("detail")
        layout.addWidget(self.fz_label)
        layout.addWidget(self.detail_label)

    def update(self, force_map, fz, detail):
        self.view.update_reconstructed_map(force_map)
        self.fz_label.setText(f"Fz  {fz:.2f} N")
        self.detail_label.setText(detail)


class UnifiedPanel(QtWidgets.QFrame):
    def __init__(self):
        super().__init__()
        self.setObjectName("buttonPanel")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(9, 9, 9, 9)
        header = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("触觉点云 · 8 通道输入")
        title.setObjectName("fieldTitle")
        self.source_label = QtWidgets.QLabel("等待 RAW / FORCE")
        self.source_label.setObjectName("source")
        header.addWidget(title)
        self.reset_view_button = QtWidgets.QToolButton()
        self.reset_view_button.setText("回正视角")
        self.reset_view_button.setToolTip("恢复传感膜默认视角与缩放")
        self.reset_view_button.setCursor(QtCore.Qt.PointingHandCursor)
        self.reset_view_button.setStyleSheet(
            "QToolButton { color:#afd7de; background:#112b35; border:1px solid #285666; "
            "border-radius:5px; padding:4px 9px; } "
            "QToolButton:hover { background:#1c4250; border-color:#49a0b2; } "
            "QToolButton:focus { border-color:#6bcee0; }"
        )
        header.addWidget(self.reset_view_button)
        header.addStretch()
        header.addWidget(self.source_label)
        layout.addLayout(header)
        self.view = ForceField("统一力场")
        self.reset_view_button.clicked.connect(self.view.reset_camera)
        self.view.setMinimumSize(520, 280)
        layout.addWidget(self.view, 1)
        legend = QtWidgets.QHBoxLayout()
        legend.setSpacing(7)
        legend_low = QtWidgets.QLabel("低")
        legend_high = QtWidgets.QLabel("高")
        legend_swatch = QtWidgets.QFrame()
        legend_swatch.setFixedSize(90, 7)
        legend_swatch.setStyleSheet(
            "background:qlineargradient(x1:0,y1:0,x2:1,y2:0,"
            "stop:0 #0b7b8a,stop:0.28 #33c79e,stop:0.62 #e0cf40,stop:1 #f76326);"
            "border:0; border-radius:3px;"
        )
        legend.addWidget(legend_low)
        legend.addWidget(legend_swatch)
        legend.addWidget(legend_high)
        legend.addStretch()
        layout.addLayout(legend)
        self.detail_label = QtWidgets.QLabel("点云颜色与起伏表示 8 通道 Fz 显示分配的插值强弱，并非实测位移或密集测力阵列；准确力值以右侧 N 读数为准。通道与前后朝向待实机逐点核验。")
        self.detail_label.setObjectName("detail")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)

    def update_reconstructed_map(self, frame):
        source = self.view.update_reconstructed_map(frame)
        self.source_label.setText(source)
        return source, self.view.last_zones.copy()

    def update_luo_force_map(self, frame):
        source = self.view.update_luo_force_map(frame)
        self.source_label.setText(source)
        return source, self.view.last_zones.copy()


class Monitor(QtWidgets.QMainWindow):
    def __init__(self):
        super().__init__()
        self.reader = None
        self.device_probe = None
        self.spi_devices = None
        self.last_connection_error = ""
        self.calibration_dialog = None
        self.raw = np.zeros(RAW_COUNT, dtype=float)
        self.tare_raw = None
        self.tare_noise_floor = np.zeros(CHANNELS, dtype=float)
        self.firmware_force = np.zeros(3, dtype=float)
        self.firmware_tare = 0.0
        self.firmware_tared_fz = 0.0
        self.device_fz = 0.0
        self.model_raw_fz = 0.0
        self.model_confidence = 0.0
        self.model_load_score = float("nan")
        self.model_soft_gate_rescue = False
        self.model_regression_unclamped = 0.0
        self.model_regression_clamped = 0.0
        self.model_invariant_fallback = 0.0
        self.model_release_ratio = float("inf")
        self.model_gate_reason = "NO_TARE"
        self.preload_compensation_blend = 0.0
        self.model_region_weights = np.full(4, 0.25, dtype=float)
        self.model_region_index = 0
        self.raw_activity = 0.0
        self.magnetic_coherence = 0.0
        self.force_display_source = "等待固件 FORCE"
        self.quality_flags = ("NO_TARE",)
        self.luo_force_map = None
        self.reconstructed_force_map = np.zeros((4, 2, 3), dtype=float)
        self.animation_force_map = np.zeros((4, 2, 3), dtype=float)
        self.last_animation_active_at = 0.0
        self.front_fz = self.back_fz = 0.0
        self.active_half = None
        self.last_device_ms = 0
        self.last_zones = np.zeros(8, dtype=float)
        self.last_zone_source = "等待 RAW / FORCE"
        self.last_animation_source = "等待完整配对帧"
        self.recording = False
        self.records = []
        self.paired_frames = deque(maxlen=600)
        self.force_trace = ForceTrace()
        self.host_tare_quality = None
        self.last_paired_at = 0.0
        self._stream_gap_context = None
        self.data_stale = True
        self.stale_logged = False
        self.protocol = TestProtocol()
        self.high_force_session_started_mono = None
        self.high_force_session_id = ""
        self.high_force_last_received_mono = None
        self.high_force_reference_point = None
        self.high_force_reference_entered = False
        self.tare_valid_for_new_test = False
        self.provisional_high_force_enabled = True
        self.acquisition_diagnostic = None
        self.acquisition_diag_status = "UNAVAILABLE"
        self.acquisition_diag_raw_line = ""
        self.host_tare_epoch = 0
        self.release_zero_enabled = True
        self.raw_baseline_tracker = ReleaseBaselineTracker()
        self.raw_baseline_result = None
        self.raw_baseline_sample_key = None
        self.manual_reference_model_n = 0.0
        self.local_force_runtime = LocalHighForceRuntime()
        self.local_force_model = None
        self.back_force_model = None
        self.back25_consistency_model = None
        self.back25_smooth_model = None
        self.back25_legacy_model = None
        self.local_force_selection_index = 0
        self.local_force_result = None
        self.numeric_zone_forces = None
        profile_root = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
        profile_path = profile_root / "local_force_profile.json"
        self.local_force_load_error = ""
        if profile_path.is_file():
            try:
                self.local_force_model = load_local_model(profile_path)
                if getattr(self.local_force_model,'profile','front') != 'front':
                    raise ValueError('前区参数文件中的校准位置不匹配')
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.local_force_model = None
                self.local_force_load_error = str(error)
        self.back_force_load_error = ""
        back_profile_path = profile_root / "back_force_profile.json"
        if back_profile_path.is_file():
            try:
                self.back_force_model = load_local_model(back_profile_path)
                if getattr(self.back_force_model,'profile','front') != 'back':
                    raise ValueError('后区参数文件中的校准位置不匹配')
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.back_force_model = None
                self.back_force_load_error = str(error)
        self.back25_consistency_load_error = ""
        back25_profile_path = profile_root / "back25_consistency_profile.json"
        if back25_profile_path.is_file():
            try:
                self.back25_consistency_model = load_local_model(back25_profile_path)
                if getattr(self.back25_consistency_model, 'profile', 'front') != 'back':
                    raise ValueError('后25 N总力和区域参数文件中的校准位置不匹配')
            except (ValueError, OSError, KeyError, TypeError) as error:
                self.back25_consistency_model = None
                self.back25_consistency_load_error = str(error)
        else:
            self.back25_consistency_load_error = "缺少 back25_consistency_profile.json 参数文件"
        self.back25_smooth_load_error = ""
        smooth_path = profile_root / "back25_smooth_profile.json"
        updated_path = profile_root / "back25_domain_profile.json"
        if updated_path.is_file():
            try:
                self.back25_legacy_model = load_local_model(smooth_path)
            except (ValueError, OSError, KeyError, TypeError):
                self.back25_legacy_model = None
            smooth_path = updated_path
        try:
            self.back25_smooth_model = load_local_model(smooth_path)
            if getattr(self.back25_smooth_model, 'profile', None) != 'back':
                raise ValueError('后25 N平滑区域参数位置不匹配')
        except (ValueError, OSError, KeyError, TypeError) as error:
            self.back25_smooth_model = None
            self.back25_smooth_load_error = str(error)
        self.automatic_compensation_model=None
        self.automatic_compensation_load_error=''
        try:
            self.automatic_compensation_model=AutomaticCompensationModel(self.local_force_model,self.back25_smooth_model)
        except (ValueError,TypeError) as error:
            self.automatic_compensation_load_error=str(error)
        self.active_force_mode = RAW_TARE_MODE
        self.setWindowTitle("TacAI H3 v5.3.28 ZP500N Stream")
        self.resize(1500, 950)
        self.setMinimumSize(1100, 760)
        self._build_ui_v524()
        self.reference_force.lineEdit().textEdited.connect(self._on_reference_force_edited)
        self.reference_force.valueChanged.connect(self._on_reference_force_edited)
        self._refresh_ports()
        self.timeout_timer = QtCore.QTimer(self)
        self.timeout_timer.timeout.connect(self._check_data_timeout)
        self.timeout_timer.start(250)
        self._activate_default_back25_correction()

    def _activate_default_back25_correction(self):
        """Enable automatic calibrated-profile selection after the UI is ready."""
        if self.automatic_compensation_model is None:
            self._write_log(
                "自动补偿未能默认开启：" + self.automatic_compensation_load_error,
                "error",
            )
            self.local_force_box.setToolTip(
                "自动补偿参数不可用，当前使用原值。" + self.automatic_compensation_load_error
            )
            return
        self.local_force_box.setCurrentIndex(6)
        self._write_log(
            "启动默认已开启自动补偿；仅在前/后模型中唯一匹配时应用。基线异常、范围外或模型冲突时保留原值并提示复核。",
            "info",
        )
        if len(self.automatic_compensation_model.models)<2:
            self._write_log('自动补偿仅部分参数可用：'+','.join(self.automatic_compensation_model.models)+
                            '；其他位置无法自动补偿，请检查参数文件', 'error')

    def _build_ui(self):
        self.setStyleSheet("""
            QMainWindow { background:#061018; color:#e8f3ff; }
            QWidget { font-family:'Segoe UI','Microsoft YaHei'; font-size:10pt; color:#e8f3ff; }
            QFrame#buttonPanel { background:#0b1d2b; border:1px solid #25516a; border-radius:9px; }
            QLabel#fieldTitle { font-size:15pt; font-weight:700; color:#dff7ff; }
            QLabel#source { color:#7ed9a1; font-size:9pt; } QLabel#fz { font-size:22pt; font-weight:700; color:#ffe788; }
            QLabel#detail { color:#8eacbd; font-size:9pt; } QPushButton { background:#12364a; border:1px solid #3b7894; border-radius:5px; padding:6px 10px; color:#eefbff; }
            QPushButton:hover { background:#1d5169; } QPushButton#primary { background:#0879a2; border-color:#24b8dd; }
            QPushButton#danger { background:#572b3a; border-color:#b25270; } QComboBox,QLineEdit { background:#0a1d2a; border:1px solid #28576d; border-radius:5px; padding:5px; color:#e5f4ff; }
            QTextEdit { background:#071722; border:1px solid #1d4255; color:#a8c6d8; font-family:Consolas; font-size:9pt; }
            QFrame#dataPanel { background:#091a26; border:1px solid #224b61; border-radius:7px; }
            QLabel#dataTitle { color:#8edcf3; font-size:9pt; font-weight:700; }
            QLabel#dataValue { color:#dbeefa; font-family:Consolas; font-size:9pt; }
            QLabel#zoneValue { background:#0d2735; border:1px solid #28566b; border-radius:4px; color:#d7edf8; font-family:Consolas; font-size:8pt; padding:3px 5px; }
        """)
        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        top = QtWidgets.QHBoxLayout()
        title = QtWidgets.QLabel("TacAI H3.0 Python Monitor v5.2.4 · V3-3 方向连续响应 / 一致性报告 / 安全升级")
        title.setStyleSheet("font-size:16pt;font-weight:700;color:#63d9ff;")
        self.status = QtWidgets.QLabel("未连接")
        self.status.setStyleSheet("color:#a6c2d2;")
        top.addWidget(title); top.addStretch(); top.addWidget(self.status)
        layout.addLayout(top)

        controls = QtWidgets.QHBoxLayout()
        self.port_box = QtWidgets.QComboBox(); self.baud_box = QtWidgets.QComboBox()
        self.baud_box.addItems(["115200", "230400", "460800", "921600"])
        refresh = QtWidgets.QPushButton("刷新串口"); refresh.clicked.connect(self._refresh_ports)
        self.connect_button = QtWidgets.QPushButton("连接串口"); self.connect_button.setObjectName("primary"); self.connect_button.clicked.connect(self._toggle_connection)
        self.tare_button = QtWidgets.QPushButton("空载 RAW 置零（20 个配对帧）"); self.tare_button.clicked.connect(self._tare)
        calibration = QtWidgets.QPushButton("标定数据采集向导"); calibration.clicked.connect(self._open_calibration)
        consistency = QtWidgets.QPushButton("一致性评价 Excel"); consistency.clicked.connect(self._open_consistency_report)
        upgrade = QtWidgets.QPushButton("固件升级（.tacai，无需 BOOT0）"); upgrade.clicked.connect(self._open_upgrade)
        self.force_mode_box = QtWidgets.QComboBox()
        self.force_mode_box.addItems([RAW_TARE_MODE, FIRMWARE_COMPAT_MODE])
        self.force_mode_box.currentTextChanged.connect(self._on_force_mode_changed)
        controls.addWidget(QtWidgets.QLabel("串口")); controls.addWidget(self.port_box); controls.addWidget(QtWidgets.QLabel("波特率")); controls.addWidget(self.baud_box)
        controls.addWidget(refresh); controls.addWidget(self.connect_button); controls.addWidget(self.tare_button); controls.addWidget(calibration); controls.addWidget(consistency); controls.addWidget(upgrade)
        controls.addWidget(QtWidgets.QLabel("主值来源")); controls.addWidget(self.force_mode_box); controls.addStretch()
        layout.addLayout(controls)

        force_summary = QtWidgets.QHBoxLayout()
        self.total_readout = QtWidgets.QLabel("合力（标量模型）：0.00 N · 请连接后执行空载 RAW 置零")
        self.front_readout = QtWidgets.QLabel("前区估计：0.00 N")
        self.back_readout = QtWidgets.QLabel("后区估计：0.00 N")
        self.total_readout.setObjectName("fz")
        force_summary.addWidget(self.total_readout); force_summary.addStretch(); force_summary.addWidget(self.front_readout); force_summary.addWidget(self.back_readout)
        layout.addLayout(force_summary)
        self.unified_panel = UnifiedPanel()
        layout.addWidget(self.unified_panel, 1)

        data_panel = QtWidgets.QFrame(); data_panel.setObjectName("dataPanel")
        data_layout = QtWidgets.QHBoxLayout(data_panel); data_layout.setContentsMargins(9, 7, 9, 7)
        raw_col = QtWidgets.QVBoxLayout()
        raw_title = QtWidgets.QLabel("24 路 RAW ΔB（原始趋势）"); raw_title.setObjectName("dataTitle")
        self.raw_readout = QtWidgets.QLabel("等待 RAW 帧"); self.raw_readout.setObjectName("dataValue")
        raw_col.addWidget(raw_title); raw_col.addWidget(self.raw_readout)
        force_col = QtWidgets.QVBoxLayout()
        force_title = QtWidgets.QLabel("力语义：标量合力为主值 / 固件 Fx·Fy·Fz 为未完成三轴标定的原始分量"); force_title.setObjectName("dataTitle")
        self.force_readout = QtWidgets.QLabel("等待 FORCE 帧"); self.force_readout.setObjectName("dataValue")
        force_col.addWidget(force_title); force_col.addWidget(self.force_readout)
        zones_col = QtWidgets.QVBoxLayout()
        zones_title = QtWidgets.QLabel("S1–S8 位置分配 Fz（N）"); zones_title.setObjectName("dataTitle")
        zones_grid = QtWidgets.QGridLayout(); zones_grid.setHorizontalSpacing(4); zones_grid.setVerticalSpacing(4)
        self.zone_labels = []
        for index in range(8):
            label = QtWidgets.QLabel(f"S{index + 1}  0.00")
            label.setObjectName("zoneValue")
            self.zone_labels.append(label)
            zones_grid.addWidget(label, index // 2, index % 2)
        zones_col.addWidget(zones_title); zones_col.addLayout(zones_grid)
        data_layout.addLayout(raw_col, 2); data_layout.addLayout(force_col, 3); data_layout.addLayout(zones_col, 4)
        layout.addWidget(data_panel)

        protocol_row = QtWidgets.QHBoxLayout()
        self.protocol_mode = QtWidgets.QComboBox()
        self.protocol_mode.addItems([
            "前/中/后同模型精度/迟滞 0→30→0 N",
            "长按恢复 30 N×500 s→0 N×120 s",
            "电磁抗扰 空载基线30 s→干扰60 s",
            "高力连续标定 A（固定前/后位置）",
            "高力连续标定 B（不同加载顺序）",
        ])
        self.test_position_box = QtWidgets.QComboBox(); self.test_position_box.addItems(["前", "中", "后"])
        self.reference_force = QtWidgets.QDoubleSpinBox(); self.reference_force.setRange(0.0, 40.0); self.reference_force.setDecimals(3); self.reference_force.setSuffix(" N 测力计")
        protocol_start = QtWidgets.QPushButton("开始测试（锁定置零）"); protocol_start.clicked.connect(self._start_protocol)
        self.protocol_action = QtWidgets.QPushButton("载荷稳定，采样 3 秒"); self.protocol_action.clicked.connect(self._protocol_action)
        protocol_stop = QtWidgets.QPushButton("结束测试"); protocol_stop.clicked.connect(self._stop_protocol)
        self.protocol_status = QtWidgets.QLabel("测试未开始")
        for widget in (self.protocol_mode, QtWidgets.QLabel("测试位置"), self.test_position_box, self.reference_force, protocol_start, self.protocol_action, protocol_stop, self.protocol_status): protocol_row.addWidget(widget)
        protocol_row.addStretch()
        layout.addLayout(protocol_row)

        record = QtWidgets.QHBoxLayout()
        self.record_state = QtWidgets.QLabel("○ 已停止 · Frame 0")
        self.record_state.setStyleSheet("color:#a6c2d2; font-weight:700;")
        start = QtWidgets.QPushButton("开始记录"); start.setObjectName("primary"); start.clicked.connect(self._start_recording)
        stop = QtWidgets.QPushButton("停止记录"); stop.setObjectName("danger"); stop.clicked.connect(self._stop_recording)
        clear = QtWidgets.QPushButton("清空本轮"); clear.clicked.connect(self._clear_recording)
        self.position = QtWidgets.QLineEdit(); self.position.setPlaceholderText("位置 / 工况，例如：后_10N")
        self.note = QtWidgets.QLineEdit(); self.note.setPlaceholderText("备注（可选）")
        mark = QtWidgets.QPushButton("标记位置"); mark.clicked.connect(self._mark)
        export = QtWidgets.QPushButton("导出 CSV"); export.clicked.connect(self._export)
        for widget in (self.record_state, start, stop, clear, self.position, self.note, mark, export): record.addWidget(widget)
        layout.addLayout(record)
        self.log = QtWidgets.QTextEdit(); self.log.setReadOnly(True); self.log.setFixedHeight(95); layout.addWidget(self.log)

    def _build_ui_v524(self):
        """Build an operator-first spatial layout without removing workflows."""
        self.setStyleSheet("""
            QMainWindow { background:#071116; }
            QWidget { color:#e7f1f4; font-family:'Segoe UI','Microsoft YaHei'; font-size:10pt; }
            QFrame#appBar {
                background:#111e24; border:1px solid #30454d; border-radius:9px;
            }
            QFrame#controlBar { background:#101e25; border:1px solid #293f49; border-radius:9px; }
            QFrame#summaryPanel { background:#0e1d24; border:1px solid #2b414a; border-radius:9px; }
            QFrame#buttonPanel { background:#0b1a21; border:1px solid #2b414a; border-radius:9px; }
            QLabel#brandTitle { color:#f5f9fa; font-size:17pt; font-weight:650; }
            QLabel#brandSub { color:#9bb5be; font-size:9pt; }
            QLabel#connectionStatus { color:#c5d8de; background:#1b2c34; border:1px solid #3a5059; border-radius:7px; padding:7px 12px; }
            QLabel#toolbarGroup { color:#8faab4; font-size:9pt; font-weight:600; }
            QLabel#sectionTitle { color:#abc4cd; font-size:10pt; font-weight:600; }
            QLabel#forceValue { color:#f7fbfc; font-size:39pt; font-weight:650; }
            QLabel#forceCaption { color:#9db5bf; font-size:9pt; }
            QLabel#statusBanner { background:#17313b; border-radius:8px; color:#c3dbe0; padding:11px 12px; }
            QLabel#metricValue { color:#eaf4f6; font-size:16pt; font-weight:600; }
            QLabel#metricLabel, QLabel#detail { color:#a0b8c1; font-size:9pt; }
            QLabel#fieldTitle { color:#e0edf0; font-size:12pt; font-weight:600; }
            QLabel#source { color:#73ced2; font-size:9pt; }
            QLabel#dataTitle { color:#a7c2ca; font-size:10pt; font-weight:600; }
            QLabel#dataValue { color:#d7e7eb; font-family:Consolas,'Microsoft YaHei'; font-size:9pt; }
            QLabel#zoneValue { background:#1b2b32; border-radius:5px; color:#d7e8eb; font-family:Consolas; font-size:9pt; padding:7px 9px; }
            QPushButton { min-height:34px; background:#1b2c34; border:1px solid #3a5662; border-radius:6px; padding:3px 12px; color:#e4f0f2; }
            QPushButton:hover { background:#22424e; border-color:#4c7784; }
            QPushButton:pressed { background:#10262f; }
            QPushButton:focus { border:2px solid #45c9d1; }
            QPushButton:disabled { color:#718891; background:#102028; border-color:#243b45; }
            QPushButton#primary { background:#1c747c; border-color:#45b9c2; color:#f5ffff; font-weight:600; }
            QPushButton#primary:hover { background:#21838a; border-color:#45c9d1; }
            QPushButton#danger { background:#3b262d; border-color:#79515c; color:#f2d6dc; }
            QComboBox, QLineEdit, QDoubleSpinBox { min-height:34px; background:#091920; border:1px solid #31505e; border-radius:7px; padding:2px 9px; color:#e3eff1; selection-background-color:#2b7880; }
            QComboBox:focus, QLineEdit:focus, QDoubleSpinBox:focus { border:2px solid #45c9d1; }
            QTabWidget::pane { background:#0d1d25; border:1px solid #284653; border-radius:9px; top:-1px; }
            QTabBar::tab { background:#0d1d25; color:#9db4bd; padding:10px 18px; border-bottom:2px solid transparent; }
            QTabBar::tab:selected { color:#f1f8f9; background:#18333e; border-bottom-color:#45c9d1; }
            QTabBar::tab:hover { color:#e4f0f2; background:#15303a; }
            QTextEdit { background:#091920; border:0; color:#c0d5db; font-family:Consolas,'Microsoft YaHei'; font-size:9pt; padding:7px; }
            QSplitter::handle { background:#10252e; width:8px; }
            QScrollBar:vertical { background:#0b1b22; width:10px; margin:0; }
            QScrollBar::handle:vertical { background:#365764; min-height:30px; border-radius:4px; }
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical { background:#0b1b22; }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical { height:0; }
            QToolTip { background:#18313b; color:#e7f1f4; border:1px solid #3d6675; padding:5px; }
        """)

        root = QtWidgets.QWidget()
        self.setCentralWidget(root)
        layout = QtWidgets.QVBoxLayout(root)
        layout.setContentsMargins(14, 12, 14, 12)
        layout.setSpacing(10)

        app_bar = QtWidgets.QFrame(); app_bar.setObjectName("appBar")
        app_layout = QtWidgets.QHBoxLayout(app_bar); app_layout.setContentsMargins(16, 10, 14, 10)
        brand = QtWidgets.QVBoxLayout(); brand.setSpacing(1)
        brand_title = QtWidgets.QLabel("TacAI H3.0 触觉标定工作台"); brand_title.setObjectName("brandTitle")
        brand_sub = QtWidgets.QLabel("v5.3.28 ZP-500N 连续数据适配 · UART / CH341 / CH347 · 原值追溯"); brand_sub.setObjectName("brandSub")
        brand.addWidget(brand_title); brand.addWidget(brand_sub)
        self.status = QtWidgets.QLabel("未连接"); self.status.setObjectName("connectionStatus")
        app_layout.addLayout(brand); app_layout.addStretch(); app_layout.addWidget(self.status)
        layout.addWidget(app_bar)

        control_bar = QtWidgets.QFrame(); control_bar.setObjectName("controlBar")
        control_layout = QtWidgets.QVBoxLayout(control_bar)
        control_layout.setContentsMargins(15, 10, 15, 10); control_layout.setSpacing(8)
        transport_row = QtWidgets.QHBoxLayout()
        self.transport_box = QtWidgets.QComboBox()
        self.transport_box.addItems(["UART 串口", "CH347F SPI", "CH341A SPI"])
        self.transport_box.setAccessibleName("通信接口")
        self.transport_box.currentIndexChanged.connect(self._transport_changed)
        transport_row.addWidget(QtWidgets.QLabel("通信接口"))
        transport_row.addWidget(self.transport_box)
        self.spi_hint = QtWidgets.QLabel("SPI：CS0 · Mode 0 · MSB · 3.3 V；采集需 SPI 应用固件，SPI 升级另需双接口 Bootloader")
        self.spi_hint.setWordWrap(True)
        self.spi_hint.setVisible(False)
        transport_row.addWidget(self.spi_hint, 1)
        transport_row.addStretch()
        control_layout.addLayout(transport_row)
        device_row = QtWidgets.QHBoxLayout(); device_row.setSpacing(8)
        self.port_box = QtWidgets.QComboBox(); self.port_box.setMinimumWidth(76)
        self.baud_box = QtWidgets.QComboBox(); self.baud_box.addItems(["115200", "230400", "460800", "921600"]); self.baud_box.setMinimumWidth(86)
        refresh = self.refresh_button = QtWidgets.QPushButton("刷新")
        refresh.clicked.connect(self._detect_devices)
        self.connect_button = QtWidgets.QPushButton("连接设备"); self.connect_button.setObjectName("primary"); self.connect_button.clicked.connect(self._toggle_connection)
        self.tare_button = QtWidgets.QPushButton("空载置零"); self.tare_button.clicked.connect(self._tare)
        calibration = QtWidgets.QPushButton("标定采集"); calibration.clicked.connect(self._open_calibration)
        consistency = QtWidgets.QPushButton("一致性报告"); consistency.clicked.connect(self._open_consistency_report)
        upgrade = QtWidgets.QPushButton("固件升级"); upgrade.clicked.connect(self._open_upgrade)
        self.high_force_correction_box = QtWidgets.QCheckBox("10–30 N 高力候选修正")
        self.high_force_correction_box.setChecked(True)
        self.high_force_correction_box.setToolTip("仅针对已标定正压高力段；可在未记录、未测试时关闭以回放上一版曲线。每帧 CSV 会注明开关状态。")
        self.high_force_correction_box.toggled.connect(self._on_high_force_correction_changed)
        self.release_zero_box = QtWidgets.QCheckBox("松手零点补偿")
        self.release_zero_box.setChecked(self.release_zero_enabled)
        self.release_zero_box.setToolTip("按卸载模式推定松手后，更新RAW工作零点；下一次小力从新零点计。无法保证区分完全松手与部分卸载；手动零点和原始RAW保留，精密标定时关闭。")
        self.release_zero_box.toggled.connect(self._on_release_zero_changed)
        self.force_mode_box = QtWidgets.QComboBox(); self.force_mode_box.addItems([RAW_TARE_MODE, FIRMWARE_COMPAT_MODE]); self.force_mode_box.currentTextChanged.connect(self._on_force_mode_changed)
        self.force_mode_box.setMinimumWidth(250); self.force_mode_box.setMaximumWidth(350)
        device_label = QtWidgets.QLabel("设备连接"); device_label.setObjectName("toolbarGroup")
        port_label = self.port_label = QtWidgets.QLabel("串口"); port_label.setObjectName("toolbarGroup")
        baud_label = self.baud_label = QtWidgets.QLabel("波特率"); baud_label.setObjectName("toolbarGroup")
        source_label = QtWidgets.QLabel("主值来源"); source_label.setObjectName("toolbarGroup")
        for widget in (device_label, port_label, self.port_box, baud_label, self.baud_box, refresh, self.connect_button, self.tare_button):
            device_row.addWidget(widget)
        device_row.addStretch(); device_row.addWidget(source_label); device_row.addWidget(self.force_mode_box)
        control_layout.addLayout(device_row)
        workflow_row = QtWidgets.QHBoxLayout(); workflow_row.setSpacing(8)
        workflow_label = QtWidgets.QLabel("标定与维护"); workflow_label.setObjectName("toolbarGroup")
        for widget in (workflow_label, calibration, consistency, upgrade, self.high_force_correction_box, self.release_zero_box):
            workflow_row.addWidget(widget)
        self.local_force_box = QtWidgets.QComboBox()
        self.local_force_box.addItems(["局部校正：关闭", "前按键非线性校正（候选）", "后25 N总力校正（候选）", "后25 N总力＋区域（二次测试候选）", "后25 N专项补偿（本批更新候选）", "后25 N平滑区域补偿（旧参数对比）", "自动补偿：前/后校准模型（默认）"])
        self.local_force_box.model().item(1).setEnabled(self.local_force_model is not None)
        self.local_force_box.model().item(2).setEnabled(self.back_force_model is not None)
        self.local_force_box.model().item(3).setEnabled(self.back25_consistency_model is not None)
        self.local_force_box.model().item(4).setEnabled(self.back25_smooth_model is not None)
        self.local_force_box.model().item(5).setEnabled(self.back25_legacy_model is not None)
        self.local_force_box.model().item(6).setEnabled(self.automatic_compensation_model is not None)
        if self.back25_smooth_load_error:
            self.local_force_box.setItemData(4, "不可用：" + self.back25_smooth_load_error, QtCore.Qt.ToolTipRole)
        if self.back25_consistency_load_error:
            self.local_force_box.setItemData(3, "不可用：" + self.back25_consistency_load_error, QtCore.Qt.ToolTipRole)
        self.local_force_box.setToolTip(
            "启动默认开启自动补偿：在已有前/后校准模型中唯一匹配时应用，冲突或范围外时保留原值并提示。"
            "仅使用冻结参数，开发整轮留出尚未全部达标。"
            "选择模式不等于当前帧已应用，越界或历史不足时显示旁路原因。"
            "按实际加载位置选择前区或后区。前区校正分区磁响应和总力；后25 N仅校正总力，保留原区域占比。"
            "后25 N总力＋区域校正针对第二次测试的标称25 N、同落点实验；20 N及以下保持原模型。"
            "新版平滑区域补偿使用非线性历史磁响应模型，保留旧版选项供比较。"
            "加载前先选模式，完全空载置零并保留至少2秒空载，再加载。原值和校正依据同时保存；新实测尚待验收。"
        )
        self.local_force_box.currentIndexChanged.connect(self._on_local_force_changed)
        workflow_row.addWidget(self.local_force_box)
        workflow_row.addStretch()
        control_layout.addLayout(workflow_row)
        self.baseline_quality_label = QtWidgets.QLabel(quality_text(None))
        self.baseline_quality_label.setWordWrap(True)
        self.baseline_quality_label.setTextFormat(QtCore.Qt.PlainText)
        self.baseline_quality_label.setToolTip("检查静置RAW的波动、突变及采样连续性；稳定性通过仍需确认实际完全卸载。异常基线不会用于专项补偿，原始数据继续保留。")
        control_layout.addWidget(self.baseline_quality_label)
        self.connection_error_label = QtWidgets.QLabel()
        self.connection_error_label.setWordWrap(True)
        self.connection_error_label.setTextFormat(QtCore.Qt.PlainText)
        self.connection_error_label.setTextInteractionFlags(QtCore.Qt.TextSelectableByMouse)
        self.connection_error_label.setStyleSheet("color:#ff9baa; padding:3px 0;")
        self.connection_error_label.setVisible(False)
        control_layout.addWidget(self.connection_error_label)
        layout.addWidget(control_bar)

        content = QtWidgets.QSplitter(QtCore.Qt.Horizontal)
        self.unified_panel = UnifiedPanel()
        content.addWidget(self.unified_panel)

        summary = QtWidgets.QFrame(); summary.setObjectName("summaryPanel"); summary.setMinimumHeight(555)
        summary_layout = QtWidgets.QVBoxLayout(summary); summary_layout.setContentsMargins(18, 18, 18, 16); summary_layout.setSpacing(12)
        caption = QtWidgets.QLabel("实时合力"); caption.setObjectName("sectionTitle")
        self.total_readout = QtWidgets.QLabel("0.00 N"); self.total_readout.setObjectName("forceValue")
        self.force_caption = QtWidgets.QLabel("请连接设备并在完全空载时执行置零"); self.force_caption.setObjectName("forceCaption"); self.force_caption.setWordWrap(True)
        self.status_banner = QtWidgets.QLabel("等待设备数据"); self.status_banner.setObjectName("statusBanner"); self.status_banner.setWordWrap(True)
        summary_layout.addWidget(caption); summary_layout.addWidget(self.total_readout); summary_layout.addWidget(self.force_caption)
        summary_layout.addSpacing(3); summary_layout.addWidget(self.status_banner)

        metric_row = QtWidgets.QHBoxLayout(); metric_row.setSpacing(12)
        for label_text, attr in (("前区估计", "front_readout"), ("后区估计", "back_readout")):
            box = QtWidgets.QWidget()
            box_layout = QtWidgets.QVBoxLayout(box); box_layout.setContentsMargins(4, 7, 4, 7); box_layout.setSpacing(4)
            label = QtWidgets.QLabel(label_text); label.setObjectName("metricLabel")
            value = QtWidgets.QLabel("0.00 N"); value.setObjectName("metricValue")
            setattr(self, attr, value); box_layout.addWidget(label); box_layout.addWidget(value); metric_row.addWidget(box, 1)
            if attr == "front_readout":
                divider = QtWidgets.QFrame(); divider.setFrameShape(QtWidgets.QFrame.VLine)
                divider.setStyleSheet("color:#2b4a57;"); metric_row.addWidget(divider)
        summary_layout.addLayout(metric_row)

        inspector = QtWidgets.QTabWidget()
        inspector.setMinimumHeight(242)
        overview = QtWidgets.QWidget(); overview_layout = QtWidgets.QVBoxLayout(overview); overview_layout.setContentsMargins(10, 10, 10, 10); overview_layout.setSpacing(8)
        raw_title = QtWidgets.QLabel("RAW 响应"); raw_title.setObjectName("dataTitle")
        self.raw_readout = QtWidgets.QLabel("等待 RAW 帧"); self.raw_readout.setObjectName("dataValue")
        self.raw_readout.setWordWrap(True)
        zones_title = QtWidgets.QLabel("S1–S8 空间分配"); zones_title.setObjectName("dataTitle")
        zones_grid = QtWidgets.QGridLayout(); zones_grid.setSpacing(5); self.zone_labels = []
        for index in range(8):
            label = QtWidgets.QLabel(f"S{index + 1}  0.00 N"); label.setObjectName("zoneValue"); self.zone_labels.append(label)
            label.setMinimumHeight(27)
            zones_grid.addWidget(label, index // 2, index % 2)
        overview_layout.addWidget(raw_title); overview_layout.addWidget(self.raw_readout); overview_layout.addSpacing(4); overview_layout.addWidget(zones_title); overview_layout.addLayout(zones_grid); overview_layout.addStretch()
        diagnostic = QtWidgets.QWidget(); diagnostic_layout = QtWidgets.QVBoxLayout(diagnostic); diagnostic_layout.setContentsMargins(10, 10, 10, 10)
        self.force_readout = QtWidgets.QLabel("等待 FORCE 帧"); self.force_readout.setObjectName("dataValue"); self.force_readout.setWordWrap(True); self.force_readout.setAlignment(QtCore.Qt.AlignTop | QtCore.Qt.AlignLeft)
        diagnostic_layout.addWidget(self.force_readout); diagnostic_layout.addStretch()
        inspector.addTab(overview, "传感器概览"); inspector.addTab(diagnostic, "算法诊断")
        summary_layout.addWidget(inspector, 1)
        summary_scroll = QtWidgets.QScrollArea()
        summary_scroll.setFrameShape(QtWidgets.QFrame.NoFrame)
        summary_scroll.setStyleSheet("QScrollArea { background:#071116; border:0; } QScrollArea > QWidget { background:#071116; }")
        summary_scroll.setWidgetResizable(True)
        summary_scroll.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        summary_scroll.setMinimumWidth(350); summary_scroll.setMaximumWidth(440)
        summary_scroll.setWidget(summary)
        content.addWidget(summary_scroll); content.setStretchFactor(0, 5); content.setStretchFactor(1, 2); content.setSizes([1050, 420])
        layout.addWidget(content, 1)

        workflow = QtWidgets.QTabWidget(); workflow.setMinimumHeight(164); workflow.setMaximumHeight(174)
        test_page = QtWidgets.QWidget(); test_layout = QtWidgets.QVBoxLayout(test_page)
        test_layout.setContentsMargins(16, 10, 16, 10); test_layout.setSpacing(8)
        protocol_setup = QtWidgets.QHBoxLayout(); protocol_setup.setSpacing(9)
        self.protocol_mode = QtWidgets.QComboBox(); self.protocol_mode.addItems(["前/中/后同模型精度/迟滞 0→30→0 N", "长按恢复 30 N×500 s→0 N×120 s", "电磁抗扰 空载基线30 s→干扰60 s", "高力连续标定 A（固定前/后位置）", "高力连续标定 B（不同加载顺序）"])
        self.protocol_mode.setMinimumWidth(340)
        self.test_position_box = QtWidgets.QComboBox(); self.test_position_box.addItems(["前", "中", "后"])
        self.reference_force = QtWidgets.QDoubleSpinBox(); self.reference_force.setRange(0.0, 40.0); self.reference_force.setDecimals(3); self.reference_force.setSuffix(" N")
        protocol_start = QtWidgets.QPushButton("开始测试"); protocol_start.setObjectName("primary"); protocol_start.clicked.connect(self._start_protocol)
        self.protocol_action = QtWidgets.QPushButton("载荷稳定，采样 3 秒"); self.protocol_action.clicked.connect(self._protocol_action)
        protocol_stop = QtWidgets.QPushButton("结束测试"); protocol_stop.clicked.connect(self._stop_protocol)
        self.protocol_status = QtWidgets.QLabel("测试未开始"); self.protocol_status.setObjectName("forceCaption")
        self.protocol_status.setWordWrap(True)
        for widget in (QtWidgets.QLabel("测试方案"), self.protocol_mode, QtWidgets.QLabel("位置"), self.test_position_box, QtWidgets.QLabel("参考力"), self.reference_force):
            protocol_setup.addWidget(widget)
        protocol_setup.addStretch(); test_layout.addLayout(protocol_setup)
        protocol_actions = QtWidgets.QHBoxLayout(); protocol_actions.setSpacing(8)
        for widget in (protocol_start, self.protocol_action, protocol_stop): protocol_actions.addWidget(widget)
        protocol_actions.addStretch(); protocol_actions.addWidget(self.protocol_status)
        test_layout.addLayout(protocol_actions)

        record_page = QtWidgets.QWidget(); record_layout = QtWidgets.QVBoxLayout(record_page)
        record_layout.setContentsMargins(16, 10, 16, 10); record_layout.setSpacing(8)
        self.record_state = QtWidgets.QLabel("已停止 · Frame 0"); self.record_state.setObjectName("forceCaption")
        start = QtWidgets.QPushButton("开始记录"); start.setObjectName("primary"); start.clicked.connect(self._start_recording)
        stop = QtWidgets.QPushButton("停止记录"); stop.setObjectName("danger"); stop.clicked.connect(self._stop_recording)
        clear = QtWidgets.QPushButton("清空本轮"); clear.clicked.connect(self._clear_recording)
        self.position = QtWidgets.QLineEdit(); self.position.setPlaceholderText("位置 / 工况，例如：后_10N")
        self.note = QtWidgets.QLineEdit(); self.note.setPlaceholderText("备注（可选）")
        mark = QtWidgets.QPushButton("标记位置"); mark.clicked.connect(self._mark)
        export = QtWidgets.QPushButton("导出 CSV"); export.clicked.connect(self._export)
        record_actions = QtWidgets.QHBoxLayout(); record_actions.setSpacing(8)
        for widget in (self.record_state, start, stop, clear): record_actions.addWidget(widget)
        record_actions.addStretch(); record_actions.addWidget(export)
        record_fields = QtWidgets.QHBoxLayout(); record_fields.setSpacing(8)
        record_fields.addWidget(self.position, 2); record_fields.addWidget(self.note, 3); record_fields.addWidget(mark)
        record_layout.addLayout(record_actions); record_layout.addLayout(record_fields)

        log_page = QtWidgets.QWidget(); log_layout = QtWidgets.QVBoxLayout(log_page); log_layout.setContentsMargins(6, 6, 6, 6)
        self.log = QtWidgets.QTextEdit(); self.log.setReadOnly(True); log_layout.addWidget(self.log)
        workflow.addTab(test_page, "测试流程"); workflow.addTab(record_page, "记录与导出"); workflow.addTab(log_page, "运行日志")
        layout.addWidget(workflow)

    def _write_log(self, message, level="info"):
        colors = {"info":"#a8c6d8", "success":"#63e3a0", "error":"#ff738f"}
        self.log.append(f'<span style="color:{colors.get(level, colors["info"])}">[{time.strftime("%H:%M:%S")}] {message}</span>')

    def _update_record_state(self):
        frame_count = len(self.records)
        if self.recording:
            self.record_state.setText(f"正在记录 · Frame {frame_count}")
            self.record_state.setStyleSheet("color:#63e3a0; font-weight:700;")
        else:
            self.record_state.setText(f"已停止 · Frame {frame_count}")
            self.record_state.setStyleSheet("color:#a6c2d2; font-weight:700;")

    def _transport_changed(self):
        transport = self.transport_box.currentIndex()
        spi = transport in (1, 2)
        self.spi_devices = None
        self.port_label.setText("CH341A 序号" if transport == 2 else "CH347 序号" if spi else "串口")
        self.baud_label.setText("SPI 时钟 Hz" if spi else "波特率")
        self.baud_box.clear()
        self.baud_box.addItems(["硬件固定"] if transport == 2 else ["468750", "937500"] if spi else ["115200", "230400", "460800", "921600"])
        self.baud_box.setEnabled(transport != 2)
        self.spi_hint.setText("CH341A：模块设为 I2C/SPI，两组电压跳帽均设 3.3 V；使用 CS0 / SCK / MOSI / MISO / GND"
                              if transport == 2 else "SPI：CS0 · Mode 0 · MSB · 3.3 V；采集需 SPI 应用固件，SPI 升级另需双接口 Bootloader")
        self.spi_hint.setVisible(spi)
        self.refresh_button.setText("检测设备" if spi else "刷新")
        self._set_connection_error("")
        self._refresh_ports()

    def _refresh_ports(self):
        current = self.port_box.currentText()
        self.port_box.clear()
        if self.transport_box.currentIndex() in (1, 2):
            if self.spi_devices is None:
                self.port_box.addItem("0")
                self.port_box.setToolTip("序号 0 尚未检测；请先点击“检测设备”确认 USB 同步接口")
            else:
                for device in self.spi_devices:
                    self.port_box.addItem(str(device.index))
                    self.port_box.setItemData(self.port_box.count() - 1,
                                              device.diagnostic_summary, QtCore.Qt.ToolTipRole)
                self.port_box.setToolTip("由所选适配器 SDK 实际检测到的 SPI 接口序号")
        else:
            for port in serial.tools.list_ports.comports(): self.port_box.addItem(port.device)
        if current: self.port_box.setCurrentText(current)

    def _set_connection_error(self, message):
        self.last_connection_error = message
        self.connection_error_label.setText(message)
        self.connection_error_label.setVisible(bool(message))
        self.status.setToolTip(message)

    def _detect_devices(self):
        if self.transport_box.currentIndex() not in (1, 2):
            self._refresh_ports()
            return
        if self.reader is not None or self.device_probe is not None:
            self._write_log("请先结束当前连接或检测，再检测 SPI 适配器", "info")
            return
        self._set_connection_error("")
        ch341 = self.transport_box.currentIndex() == 2
        self.status.setText("检测 CH341A…" if ch341 else "检测 CH347…")
        self.device_probe = CH341Probe(self) if ch341 else CH347Probe(self)
        self.device_probe.detected.connect(self._on_devices_detected)
        self.device_probe.failed.connect(self._on_device_probe_error)
        self.device_probe.finished.connect(self._on_device_probe_finished)
        for control in (self.transport_box, self.port_box, self.baud_box, self.connect_button, self.refresh_button):
            control.setEnabled(False)
        self.device_probe.start()

    def _on_devices_detected(self, devices):
        for device in devices:
            self._write_log(device.diagnostic_summary, "info")
        self.spi_devices = [device for device in devices if device.supports_spi]
        self._refresh_ports()
        bridge = "CH341A" if self.transport_box.currentIndex() == 2 else "CH347"
        if self.spi_devices:
            self.status.setText(f"已检测到 {bridge}")
            self._write_log("检测到 SPI 候选接口：" + "、".join(
                f"#{d.index} {d.product}" + (f" IF{d.interface_number}" if d.interface_number is not None else "")
                for d in self.spi_devices), "success")
        else:
            help_text = CH341_USB_OPEN_HELP if bridge == "CH341A" else USB_OPEN_HELP
            message = (f"检测到 {bridge}，但未识别到 SPI 类型；各接口的类型和排除原因已记录，请查看运行日志"
                       if devices else f"未检测到可打开的 {bridge}。" + help_text)
            self._on_device_probe_error(message)

    def _on_device_probe_error(self, message):
        self.spi_devices = []
        self._refresh_ports()
        self.status.setText("设备检测失败")
        self._set_connection_error(message)
        self._write_log(message, "error")

    def _on_device_probe_finished(self):
        probe = self.device_probe
        self.device_probe = None
        for control in (self.transport_box, self.port_box, self.baud_box, self.connect_button, self.refresh_button):
            control.setEnabled(True)
        self.baud_box.setEnabled(self.transport_box.currentIndex() != 2)
        if probe is not None:
            probe.deleteLater()

    def _open_upgrade(self):
        if self.device_probe is not None:
            self._write_log("设备检测进行中，请完成后再升级", "info")
            return
        port = self.port_box.currentText()
        if not port:
            self._set_connection_error("尚未选择设备；请先检测并选择可用接口")
            return
        if self.reader:
            self._toggle_connection()
            if self.reader is not None:
                return
        transport = self.transport_box.currentIndex()
        spi = transport in (1, 2)
        default_image = Path(__file__).resolve().parents[2] / "release" / "spi_rxirq_v5_20261016" / "TacAI_H3_App_SPI_rxirq_v5.tacai"
        local_image = Path(__file__).resolve().parent / "TacAI_H3_App_SPI_rxirq_v5.tacai"
        if local_image.is_file():
            default_image = local_image
        if getattr(sys, "frozen", False):
            default_image = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "TacAI_H3_App_SPI_rxirq_v5.tacai"
        FirmwareUpgradeDialog(port, self, default_image_path=default_image if default_image.exists() else None,
                              transport="ch341" if transport == 2 else "spi" if spi else "uart",
                              clock_hz=0 if transport == 2 else int(self.baud_box.currentText())).exec_()
        self._refresh_ports()
        self._write_log("固件升级窗口已关闭；设备启动后请重新连接并核对采集诊断与 RAW/FORCE", "info")

    def _open_calibration(self):
        if self.protocol.active:
            self._write_log("测试流程正在进行；请先结束并导出本轮，再开始标定采集", "error")
            return
        if self.reader is None or self.data_stale:
            QtWidgets.QMessageBox.warning(self, "尚无连续数据", "请先连接设备串口，并确认 RAW/FORCE 持续更新。")
            return
        if self.tare_raw is None:
            QtWidgets.QMessageBox.warning(self, "尚未空载置零", "请先完全卸载并静置，然后执行“空载 RAW 置零”。")
            return
        if self.calibration_dialog is None:
            self.calibration_dialog = CalibrationAcquisitionDialog(self)
            self.calibration_dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
            self.calibration_dialog.destroyed.connect(lambda: setattr(self, "calibration_dialog", None))
        self.calibration_dialog.show()
        self.calibration_dialog.raise_()
        self.calibration_dialog.activateWindow()

    def _open_consistency_report(self):
        dialog = ConsistencyReportDialog(self)
        dialog.setAttribute(QtCore.Qt.WA_DeleteOnClose, True)
        dialog.show()
        dialog.raise_()
        dialog.activateWindow()

    def _toggle_connection(self):
        if self.device_probe is not None:
            return
        if self.reader:
            self.reader.stop()
            if not self.reader.wait(2000):
                self._write_log("正在结束当前采集事务，请稍后再试", "error")
                return
            self._save_force_trace("USER_DISCONNECT")
            self.reader = None
            self.transport_box.setEnabled(True)
            self.port_box.setEnabled(True)
            self.baud_box.setEnabled(self.transport_box.currentIndex() != 2)
            self.refresh_button.setEnabled(True)
            self._set_connection_error("")
            was_protocol_active = self.protocol.active
            self.status.setText("未连接"); self.connect_button.setText("连接设备")
            self._invalidate_host_tare()
            self.last_paired_at = 0.0
            self.data_stale = True
            self.acquisition_diag_status = "DISCONNECTED"
            self.force_display_source = "未连接：当前测量不可用，重连后需空载置零"
            self._zero_stale_display(("STREAM_DISCONNECTED",))
            self._append_record("STREAM_DISCONNECTED", self.last_device_ms, time.time())
            if was_protocol_active:
                self.recording = False
                self._update_record_state()
            self._update_protocol_ui(None)
            self.acquisition_diagnostic = None
            return
        port = self.port_box.currentText()
        if not port:
            self._set_connection_error("尚未选择设备；请先点击“检测设备”或“刷新”")
            self.status.setText("未发现设备")
            self._write_log(self.last_connection_error, "error")
            return
        self._set_connection_error("")
        self.status.setText("正在连接…")
        transport = self.transport_box.currentIndex()
        self.reader = (CH341ForceReader(int(port)) if transport == 2 else
                       SpiForceReader(int(port), int(self.baud_box.currentText())) if transport == 1 else
                       RawForceReader(port, int(self.baud_box.currentText())))
        self.transport_box.setEnabled(False)
        self.port_box.setEnabled(False)
        self.baud_box.setEnabled(False)
        self.refresh_button.setEnabled(False)
        self.luo_force_map = None
        self.paired_frames.clear()
        self.tare_raw = None
        self.tare_noise_floor.fill(0.0)
        self.tare_valid_for_new_test = False
        self.firmware_tare = 0.0
        self.firmware_tared_fz = 0.0
        self.raw.fill(0.0)
        self.firmware_force.fill(0.0)
        self.device_fz = 0.0
        self.animation_force_map.fill(0.0)
        self.last_animation_active_at = 0.0
        self.last_paired_at = 0.0
        self.data_stale = True
        self.stale_logged = False
        self.quality_flags = ("NO_TARE",)
        self.acquisition_diagnostic = None
        self.acquisition_diag_status = "UNAVAILABLE"
        self.acquisition_diag_raw_line = ""
        self.reader.paired_received.connect(self._on_paired)
        self.reader.diagnostic_received.connect(self._on_diagnostic)
        self.reader.stream_fault.connect(self._on_stream_fault)
        self.reader.stream_gap.connect(self._on_stream_gap)
        self.reader.luo_force_map_received.connect(self._on_luo_force_map)
        self.reader.status.connect(self._on_status)
        self.reader.finished.connect(self._on_reader_finished)
        self.reader.start(); self.connect_button.setText("断开设备")

    def _on_status(self, message, level):
        if self.sender() is not None and self.sender() is not self.reader:
            return
        self.status.setText("连接失败" if level == "error" else
                            "已连接" if level == "success" else "正在连接…")
        self.status.setToolTip(message)
        if level == "error":
            self._set_connection_error(message)
            self._on_stream_fault(message, "", time.time())
            self._invalidate_host_tare()
        else:
            self._write_log(message, level)

    def _on_reader_finished(self):
        if self.sender() is not self.reader:
            return
        self._save_force_trace("STREAM_FINISHED")
        self.reader = None
        self.transport_box.setEnabled(True)
        self.port_box.setEnabled(True)
        self.baud_box.setEnabled(self.transport_box.currentIndex() != 2)
        self.refresh_button.setEnabled(True)
        self._invalidate_host_tare()
        self.last_paired_at = 0.0
        self.data_stale = True
        self.acquisition_diag_status = "DISCONNECTED"
        self.force_display_source = (self.last_connection_error or
                                     "连接已结束：当前测量不可用，重连后需空载置零")
        self._zero_stale_display(("STREAM_DISCONNECTED",))
        self.connect_button.setText("连接设备")
        self.status.setText("连接失败" if self.last_connection_error else "未连接")

    def _invalidate_host_tare(self):
        self.raw_baseline_tracker.reset_reference()
        self._reset_local_force("HOST_TARE_INVALIDATED")
        self.host_tare_quality = None
        self.paired_frames.clear()
        self.tare_raw = None
        self.tare_noise_floor.fill(0.0)
        self.tare_valid_for_new_test = False
        self.firmware_tare = 0.0
        self._interrupt_protocol()

    def _interrupt_protocol(self):
        self._stream_gap_context = None
        if self.protocol.active:
            self.protocol.stop()
            self.tare_valid_for_new_test = False
            self.tare_button.setEnabled(True)
            self._update_protocol_ui(None)
            self._write_log("采集状态中断，当前自动测试已停止；原始记录继续保留，需重新开始该测试", "error")
        dialog = self.calibration_dialog
        if dialog is not None and getattr(dialog, "session_started", False):
            # Completed steps are already saved. Never complete a stable step
            # from samples on opposite sides of an acquisition failure.
            dialog._finish_session(stopped=True)
            message = "采集诊断异常：标定已停止，本步未完成、不生成均值；此前已完成步骤保留，当前步骤需重新采集。"
            dialog._log(message)
            if not getattr(dialog, "trajectory_error", None):
                dialog.step_instruction.setText(message)

    def _calibration_session_active(self):
        return (self.calibration_dialog is not None
                and getattr(self.calibration_dialog, "session_started", False))

    def _on_stream_gap(self, message, raw_line, received_at):
        """Pause only the incomplete step, preserving the current RAW coordinates."""
        if self.sender() is not None and self.sender() is not self.reader:
            return
        diag = self.acquisition_diagnostic
        if self._stream_gap_context is None:
            if diag is None or not diag.valid or self.last_paired_at <= 0.0:
                self._on_stream_fault(message, raw_line, received_at)
                return
            self._stream_gap_context = dict(
                firmware_id=diag.firmware_id, baseline_epoch=diag.baseline_epoch,
                device_ms=self.last_device_ms, received_at=self.last_paired_at,
                host_tare_epoch=self.host_tare_epoch,
                work_baseline_epoch=self.raw_baseline_tracker.baseline_epoch,
            )
        self.paired_frames.clear()
        if self.protocol.active:
            self.protocol.stop()
            self.tare_valid_for_new_test = False
            self.tare_button.setEnabled(True)
            self._update_protocol_ui(None)
            self._write_log("采集间断，当前连续测试已停止；已保存的数据保留", "error")
        if self._calibration_session_active():
            self.calibration_dialog.pause_for_stream_gap(message)
        self._reset_release_zero()  # Discard candidates; retain the established RAW offset.
        self.data_stale = True
        self.acquisition_diag_status = "STREAM_GAP"
        self.acquisition_diag_raw_line = raw_line
        self.force_display_source = f"采集短暂间断：{message}；等待新的完整有效帧"
        # This is a display cache clear, not a local-model or tare reset.
        self.local_force_result = None
        self.numeric_zone_forces = None
        self.device_fz = self.model_raw_fz = self.manual_reference_model_n = 0.0
        self.model_confidence = self.front_fz = self.back_fz = 0.0
        self.reconstructed_force_map = np.zeros((4, 2, 3), dtype=float)
        self.animation_force_map.fill(0.0)
        self.last_animation_active_at = 0.0
        self.quality_flags = ("STREAM_GAP",)
        self._render(self.last_device_ms)
        self._append_record("STREAM_GAP", self.last_device_ms, received_at)
        self._write_log(self.force_display_source, "info")

    def _accept_stream_gap_frame(self, frame):
        """Return recovery only for a fresh pair in the original device epoch."""
        context = self._stream_gap_context
        if context is None:
            return False
        diag = frame.diagnostic
        same_coordinates = (
            diag is not None and diag.valid and not diag.reset_reason and not frame.reset_reason
            and diag.device_ms == frame.device_ms
            and diag.firmware_id == context["firmware_id"]
            and diag.baseline_epoch == context["baseline_epoch"]
            and self.host_tare_epoch == context["host_tare_epoch"]
            and self.raw_baseline_tracker.baseline_epoch == context["work_baseline_epoch"]
        )
        if not same_coordinates or frame.device_ms < context["device_ms"]:
            self._interrupt_protocol()
            self._invalidate_host_tare()
            return False
        if max(frame.received_at, time.time()) - context["received_at"] > PAIRED_DATA_TIMEOUT_S:
            self._interrupt_protocol()
            self.data_stale = True
            self.acquisition_diag_status = "TIMEOUT"
            self.force_display_source = "通信间断超过 1 秒，标定会话已结束；此前数据保留"
            self._zero_stale_display()
            self._append_record("DATA_TIMEOUT", self.last_device_ms, time.time())
            self._write_log(self.force_display_source, "error")
            return None
        if frame.device_ms == context["device_ms"] or frame.received_at <= context["received_at"]:
            return None  # A late duplicate cannot unpause or refresh freshness.
        self._stream_gap_context = None
        return True

    def _on_diagnostic(self, diag):
        if self.sender() is not None and self.sender() is not self.reader:
            return
        if not isinstance(diag, AcquisitionDiagnostic):
            return
        old = self.acquisition_diagnostic
        self.luo_force_map = None
        self.acquisition_diagnostic = diag
        self.acquisition_diag_raw_line = diag.raw_line
        if old is not None and not diag.reset_reason and diag.error_count > old.error_count:
            self._reset_local_force("ACQUISITION_ERROR_COUNT_INCREASE")
            delta = diag.error_count - old.error_count
            saved_flags = self.quality_flags
            self.quality_flags = ("ACQUISITION_ERROR_COUNT_INCREASE",)
            self.acquisition_diag_status = "ERROR_COUNT_INCREASE"
            self._append_record("DIAG_ERROR_COUNT", diag.device_ms, diag.received_at)
            self.quality_flags = saved_flags
            self._interrupt_protocol()
            self._write_log(f"采集诊断：固件累计失败通道数新增 {delta}（现为 {diag.error_count}）；不是坏帧数，已保留诊断事件", "error")
        if diag.reset_reason:
            self._invalidate_host_tare()
            self.data_stale = True
            self.acquisition_diag_status = diag.reset_reason
            self.force_display_source = "警告：设备状态变化，旧置零失效；请停止并导出本轮，完全卸载重新置零，再清空本轮开始新记录"
            self._zero_stale_display(tuple(diag.reset_reason.split("|")))
            self._append_record("DIAG_RESET", diag.device_ms, diag.received_at)
            self._write_log(self.force_display_source, "error")
        if not diag.valid:
            self._interrupt_protocol()
            self.paired_frames.clear()
            self.data_stale = True
            flags = ["ACQUISITION_INVALID"]
            if diag.baseline_epoch == 0:
                self._invalidate_host_tare()
                flags.append("NO_FIRMWARE_BASELINE")
            missing = "/".join(f"S{i+1}" for i in range(8) if not diag.valid_mask & (1 << i))
            previous_status = self.acquisition_diag_status
            self.acquisition_diag_status = "NO_FIRMWARE_BASELINE" if diag.baseline_epoch == 0 else "INVALID_CHANNELS"
            self.force_display_source = (f"警告：采集无效（{missing or '固件基线尚未建立'}）；"
                                         f"累计失败通道数 {diag.error_count}，本周期不产生力值")
            self._zero_stale_display(tuple(flags))
            self._append_record("DIAG_INVALID", diag.device_ms, diag.received_at)
            if previous_status != self.acquisition_diag_status:
                self._write_log(self.force_display_source, "error")
        elif not diag.reset_reason:
            self.acquisition_diag_status = "VALID"

    def _on_stream_fault(self, message, raw_line, received_at):
        if self.sender() is not None and self.sender() is not self.reader:
            return
        self._save_force_trace("STREAM_FAULT")
        self.paired_frames.clear()
        self._interrupt_protocol()
        self.data_stale = True
        self.acquisition_diag_status = "PROTOCOL_ERROR"
        self.acquisition_diag_raw_line = raw_line
        self.force_display_source = f"警告：采集通信异常：{message}；当前测量不可用"
        self._zero_stale_display(("DIAGNOSTIC_PROTOCOL_ERROR",))
        self._append_record("STREAM_ERROR", self.last_device_ms, received_at)
        self._write_log(self.force_display_source, "error")

    def _on_force_mode_changed(self, _mode):
        if self.recording or self.protocol.active or self._calibration_session_active():
            with QtCore.QSignalBlocker(self.force_mode_box):
                self.force_mode_box.setCurrentText(self.active_force_mode)
            self._write_log("记录或测试进行中不能切换总力来源；请先结束本轮", "error")
            return
        self.active_force_mode = _mode
        self._reset_local_force("VALUE_SOURCE_CHANGED")
        if self.luo_force_map is None and self.last_paired_at > 0.0 and not self.data_stale:
            self._update_force_display()
            self._render(self.last_device_ms)
        self._write_log(f"总力来源切换为：{self.force_mode_box.currentText()}", "info")

    def _active_model_status(self):
        base = MODEL_STATUS if self.provisional_high_force_enabled else f"{MODEL_STATUS}_CORRECTION_DISABLED"
        if self.local_force_runtime.profile=='auto' and self.automatic_compensation_model is not None:
            return f'{base}|LOCAL_AUTO:{self.automatic_compensation_model.signature}'
        if self.local_force_runtime.model is self.back25_legacy_model and self.back25_legacy_model is not None:
            return f"{base}|LOCAL_BACK25_LEGACY:{self.back25_legacy_model.signature}"
        if self.local_force_runtime.model is self.back25_smooth_model and self.back25_smooth_model is not None:
            return f"{base}|LOCAL_BACK25_SMOOTH:{self.back25_smooth_model.signature}"
        if self.local_force_runtime.profile == "front" and self.local_force_model is not None:
            return f"{base}|LOCAL_FRONT:{self.local_force_model.signature}"
        if (self.local_force_runtime.profile == "back" and self.back25_consistency_model is not None
                and self.local_force_runtime.model is self.back25_consistency_model):
            return f"{base}|LOCAL_BACK25_CONSISTENCY:{self.back25_consistency_model.signature}"
        if self.local_force_runtime.profile == "back" and self.back_force_model is not None:
            return f"{base}|LOCAL_BACK:{self.back_force_model.signature}"
        return base

    def _local_force_area(self):
        if self.local_force_runtime.profile=='auto':
            local=self._current_local_force_result()
            selected=(local.support or {}).get('selected_profile') if local is not None else None
            return {'front':'自动前区','back':'自动后25 N'}.get(selected,'自动')
        if self.local_force_runtime.profile == "front":
            return "前区局部"
        if self.local_force_runtime.model is self.back25_smooth_model and self.back25_smooth_model is not None:
            return "后25 N平滑区域补偿"
        if self.local_force_runtime.model is self.back25_legacy_model and self.back25_legacy_model is not None:
            return "后25 N旧参数区域补偿"
        if self.local_force_runtime.model is self.back25_consistency_model and self.back25_consistency_model is not None:
            return "后25 N总力＋区域"
        return "后25 N总力"

    @staticmethod
    def _local_force_reason_text(reason):
        if str(reason).startswith('BASELINE_'):
            return quality_text(dict(reason=reason))
        return {
            "AUTO_NO_SUPPORTED_PROFILE": "没有匹配的校准模型；核对基线、15/20 N加载历史及参数适用范围",
            "AUTO_AMBIGUOUS_PROFILES": "前后模型同时匹配，已暂停自动补偿，请复核加载位置",
            "AUTO_MODEL_FAILURE": "自动补偿模型输出异常，已保留原值，请检查参数",
            "AUTO_FORCE_SATURATED": "原模型达到35 N上限，无法可靠自动补偿",
            "LOW_FORCE_IDENTITY": "20 N及以下保持原模型",
            "MISSING_PAST_LOAD_ANCHOR": "缺少加载历史，请先选模式、空载置零静置2秒后重新加载",
            "MISSING_PAST15_ANCHOR": "缺少15 N加载历史，请先选模式、空载置零静置2秒后重新加载",
            "MISSING_PAST_DUAL_LOAD_ANCHORS": "缺少15 N或20 N加载历史，请重新从空载加载",
            "MISSING_PAST_DUAL_ANCHORS": "缺少15 N或20 N加载历史，请重新从空载加载",
            "ANCHOR_NOT_YET_AVAILABLE": "加载历史尚未就绪",
            "OUTSIDE_INPUT_NEIGHBORHOOD": "当前磁状态超出校准范围",
            "OUTSIDE_BACK25_CALIBRATION_DOMAIN": "当前磁状态超出本批后25 N校准范围",
            "INCONSISTENT_PAST15_COORDINATES": "加载历史与当前置零不一致，请重新空载置零加载",
            "INVALID_OR_MISSING_PAST_INPUT": "加载历史无效或不完整，请重新从空载加载",
            "OUTSIDE_BACK25_VALIDATED_FORCE_RANGE": "当前力值超出后25 N校准范围",
            "ORIGINAL_HIGH_FORCE_CORRECTION_DISABLED": "高力曲线关闭或当前为固件诊断主值",
            "NONFINITE_MODEL_OUTPUT": "校正输出异常，请重新置零检查",
            "NO_MODEL": "校正参数不可用",
            "NO_HOST_TARE": "尚未空载置零",
            "INVALID_FRAME": "采集帧无效",
        }.get(reason, reason)

    def _local_zone_source(self):
        local = self._current_local_force_result()
        if local is not None and not local.applied:
            return "4×2 八区估计分配（校正旁路，使用原模型磁响应占比）"
        if self.local_force_runtime.profile=='auto':
            return f'4×2 八区估计分配（{self._local_force_area()}补偿；原RAW单独保留）'
        if self.local_force_runtime.profile == "back":
            if self.local_force_runtime.model is self.back25_legacy_model and self.back25_legacy_model is not None:
                return "4×2 八区估计分配（旧参数区域补偿；原RAW单独保留）"
            if self.local_force_runtime.model is self.back25_smooth_model and self.back25_smooth_model is not None:
                return "4×2 八区估计分配（后25 N平滑区域补偿；原RAW单独保留）"
            if self.local_force_runtime.model is self.back25_consistency_model and self.back25_consistency_model is not None:
                return "4×2 八区估计分配（后25 N总力＋区域校正；原RAW单独保留）"
            return "4×2 八区估计分配（后区总力校正；原磁响应占比保留）"
        return "4×2 八区估计分配（局部磁状态校正；原RAW单独保留）"

    def _reset_local_force(self, reason):
        self.local_force_runtime.reset(reason)
        self.local_force_result = None
        self.numeric_zone_forces = None
        self._reset_release_zero()

    def _reset_release_zero(self):
        """Discard interaction history without changing an established RAW offset."""
        self.raw_baseline_tracker.reset_cycle()
        self.raw_baseline_result = None
        self.raw_baseline_sample_key = None

    def _effective_tare_raw(self):
        if self.tare_raw is None:return None
        return self.raw_baseline_tracker.effective_tare(self.tare_raw)

    def _raw_baseline_reference_epoch(self, diag=None):
        return (self.host_tare_epoch,diag.firmware_id if diag else 'LEGACY_DIAG_UNAVAILABLE',
                diag.baseline_epoch if diag else None)

    def _local_runtime_epoch(self, diag=None):
        return (*self._raw_baseline_reference_epoch(diag),self.raw_baseline_tracker.baseline_epoch)

    def _raw_baseline_updates_allowed(self):
        return (self.release_zero_enabled and self.tare_raw is not None
                and self.force_mode_box.currentText()==RAW_TARE_MODE
                and not self.recording and not self.protocol.active
                and not self._calibration_session_active())

    def _current_raw_baseline_result(self):
        key=(self.last_device_ms,self.last_paired_at,
             self._raw_baseline_reference_epoch(self.acquisition_diagnostic))
        return self.raw_baseline_result if self.raw_baseline_sample_key==key else None

    def _on_release_zero_changed(self, enabled):
        if (self.recording or self.protocol.active or
                self._calibration_session_active()):
            with QtCore.QSignalBlocker(self.release_zero_box):
                self.release_zero_box.setChecked(self.release_zero_enabled)
            self._write_log("记录、测试或标定采集中不能切换松手零点补偿，请先结束本轮", "error")
            return
        self.release_zero_enabled=bool(enabled)
        if not enabled:self.raw_baseline_tracker.reset_reference()
        self._reset_local_force('RAW_BASELINE_SETTING_CHANGED')
        if self.luo_force_map is None and self.tare_raw is not None and not self.data_stale:
            self._update_force_display()
            self._render(self.last_device_ms)
        self._write_log("松手零点补偿："+("开启，按卸载模式更新RAW工作零点" if enabled else "关闭，恢复手动空载零点"),"info")

    def _update_raw_baseline(self, paired_frame, work_result):
        """Observe an accepted frame and, on commit, rebase the full model input."""
        diag=paired_frame.diagnostic
        epoch=self._raw_baseline_reference_epoch(diag)
        key=(paired_frame.device_ms,paired_frame.received_at,epoch)
        if key==self.raw_baseline_sample_key:return False
        self.raw_baseline_result=self.raw_baseline_tracker.feed(
            paired_frame.raw,work_result.fz_n,paired_frame.received_at,
            manual_tare24=self.tare_raw,epoch=epoch,
            valid=not paired_frame.reset_reason and (diag is None or (diag.valid and not diag.reset_reason)),
            enabled=self.release_zero_enabled,allow_update=self._raw_baseline_updates_allowed())
        self.raw_baseline_sample_key=key
        if not self.raw_baseline_result.changed:return False
        # Reset only the local model: a normal interaction reset must preserve
        # the newly committed working offset and its source evidence.
        self.local_force_runtime.reset('RAW_WORK_BASELINE_CHANGED')
        self.local_force_result=None
        self.numeric_zone_forces=None
        committed={time:raw for time,raw in self.raw_baseline_tracker.last_commit_frames}
        past=[frame for frame in self.paired_frames
              if frame.received_at in committed and frame.received_at<paired_frame.received_at
              and np.array_equal(frame.raw,committed[frame.received_at])
              and not frame.reset_reason and (frame.diagnostic is None or
                  (frame.diagnostic.valid and not frame.diagnostic.reset_reason))
              and self._raw_baseline_reference_epoch(frame.diagnostic)==epoch]
        self._warm_start_tare_history(past)
        message=('推定松手后RAW工作零点已更新' if self.raw_baseline_result.reason=='RELEASE_RAW_BASELINE_UPDATED'
                 else '设备基线变化，RAW工作零点已恢复手动参考')
        self._write_log(f"{message}（工作基线 {self.raw_baseline_tracker.baseline_epoch}）；手动零点和原始信号保留",'info')
        return True

    def _current_local_force_result(self):
        result = self.local_force_result
        if result is None or (result.source_token != self.last_device_ms
                              or result.timestamp != self.last_paired_at
                              or result.original_total_n != self.model_raw_fz):
            return None
        return result

    def _on_local_force_changed(self, index):
        if self.recording or self.protocol.active or self._calibration_session_active():
            with QtCore.QSignalBlocker(self.local_force_box):
                self.local_force_box.setCurrentIndex(self.local_force_selection_index)
            self._write_log("记录或测试中不能更改局部校正参数，请先结束并导出本轮", "error")
            return
        model = (self.local_force_model if index == 1 else self.back_force_model if index == 2
                 else self.back25_consistency_model if index == 3 else self.back25_smooth_model if index == 4
                 else self.back25_legacy_model if index == 5 else self.automatic_compensation_model if index==6 else None)
        profile = "front" if index == 1 else "back" if index in (2, 3, 4, 5) else 'auto' if index==6 else "off"
        if index in (1, 2, 3, 4, 5, 6) and model is None:
            with QtCore.QSignalBlocker(self.local_force_box):
                self.local_force_box.setCurrentIndex(self.local_force_selection_index)
            self._write_log("没有可用的局部校准参数，请核对参数文件", "error")
            return
        self.local_force_runtime.configure(
            model=model, profile=profile
        )
        self.local_force_selection_index = index
        self.local_force_result = None
        self.numeric_zone_forces = None
        self._reset_release_zero()
        if self.tare_raw is not None and not self.data_stale:
            self._update_force_display()
            self._render(self.last_device_ms)
        message = ("自动补偿已开启；前后模型唯一匹配时自动应用，冲突或越界时保留原值并提示" if index==6 else
                   "前按键非线性校准候选已选择" if index == 1 else
                   "后25 N本批更新候选已选择；开发整轮留出未全部达标，新实测待验收" if index == 4 else
                   "后25 N旧参数对比已选择" if index == 5 else
                   "后25 N总力＋区域校准候选已选择；仅用于标称25 N同落点实验，20 N及以下保持原模型" if index == 3 else
                   "后25 N总力校准候选已选择；八区原磁响应占比保留")
        self._write_log(message+"；下次加载前空载置零并保留至少2秒空载数据，加载状态自动采集。新实测待验收。" if index else "局部校正已关闭，使用原模型", "info")

    def _on_high_force_correction_changed(self, enabled):
        if self.recording or self.protocol.active or self._calibration_session_active():
            with QtCore.QSignalBlocker(self.high_force_correction_box):
                self.high_force_correction_box.setChecked(self.provisional_high_force_enabled)
            self._write_log("记录或测试进行中不能切换高力曲线；请先结束本轮", "error")
            return
        self.provisional_high_force_enabled = bool(enabled)
        self._reset_local_force("ORIGINAL_CURVE_CHANGED")
        if self.luo_force_map is None and self.last_paired_at > 0.0 and not self.data_stale:
            self._update_force_display()
            self._render(self.last_device_ms)
        self._write_log("10–30 N 高力候选修正：" + ("开启" if enabled else "关闭，回放上一版曲线"), "info")

    def _tare(self):
        if (self.protocol.active or self.recording or
                self._calibration_session_active()):
            self._write_log("记录或测试进行中禁止再次置零；请先停止并导出本轮，再完全卸载后重新置零", "error")
            return
        if len(self.paired_frames) < TARE_MIN_FRAMES:
            self._write_log(f"置零需要至少 {TARE_MIN_FRAMES} 个完整 RAW+FORCE 配对帧，请保持空载后稍候再试", "error")
            return
        now = time.time()
        if now - self.paired_frames[-1].received_at > TARE_MAX_FRAME_AGE_S:
            self._write_log("置零未执行：完整配对帧已停止更新，请检查 RAW/FORCE 串口输出", "error")
            return
        latest=self.paired_frames[-1].received_at
        recent=[f for f in self.paired_frames if latest-f.received_at<=2.+1e-6]
        # At lower sample rates the twenty tare frames can span over two
        # seconds. Check every frame used by either operation, in source order.
        checked_frames=list(self.paired_frames)[-max(len(recent),TARE_FRAME_COUNT):]
        # A manually confirmed empty-load window may seed the new runtime, but
        # frames on opposite sides of a device/diagnostic reset must never mix.
        epochs={(f.diagnostic.firmware_id, f.diagnostic.baseline_epoch)
                if f.diagnostic is not None else None for f in checked_frames}
        if (len(epochs)!=1 or any(f.reset_reason or (f.diagnostic is not None
                and (f.diagnostic.reset_reason or not f.diagnostic.valid)) for f in checked_frames)
                or any(b.device_ms<=a.device_ms for a,b in zip(checked_frames,checked_frames[1:]))):
            self.tare_valid_for_new_test=False
            message='置零未执行：空载帧跨越设备状态变化，请等待连续有效空载帧后重试；已有置零基线未被替换'
            self.baseline_quality_label.setText(message)
            self._write_log(message,'error')
            return
        quality=evaluate_window([f.raw for f in recent],[f.received_at for f in recent],now=latest)
        if quality['status']!='READY':
            self.tare_valid_for_new_test=False
            self.baseline_quality_label.setText('置零未执行：'+quality_text(quality))
            self._write_log('置零未执行：'+quality_text(quality)+'；已有置零基线未被替换', 'error')
            return
        try:
            result = build_tare(self.paired_frames)
        except ValueError as error:
            self._write_log(f"置零未执行：{error}", "error")
            return
        # Preserve the preceding press/release cycle before replacing its zero.
        self._save_force_trace("BEFORE_MANUAL_TARE")
        self.tare_raw = result.raw.copy()
        self.host_tare_quality = quality
        tare_samples = np.stack([item.raw for item in list(self.paired_frames)[-TARE_FRAME_COUNT:]])
        tare_deviation = np.linalg.norm(
            (tare_samples - self.tare_raw).reshape(tare_samples.shape[0], CHANNELS, 3), axis=2
        )
        per_sensor_median = np.median(tare_deviation, axis=0)
        per_sensor_mad = np.median(np.abs(tare_deviation - per_sensor_median), axis=0)
        self.tare_noise_floor = np.maximum(20.0, per_sensor_median + 4.0 * per_sensor_mad)
        self.firmware_tare = float(result.firmware_force[2])
        self.host_tare_epoch += 1
        self.raw_baseline_tracker.reset_reference()
        self._reset_local_force("TARE_CHANGED")
        self._warm_start_tare_history(recent)
        self.tare_valid_for_new_test = True
        self.animation_force_map.fill(0.0)
        self.last_animation_active_at = 0.0
        self._update_force_display()
        self._render(self.last_device_ms)
        if self.records:
            self.records.append(self._record_base("TARE_CHANGED", self.last_device_ms, now))
            self._update_record_state()
            self._write_log("空载基线已更新；旧记录保留，请先导出并清空本轮，再开始新记录", "info")
        self._write_log(
            f"RAW 置零完成：{result.frame_count} 个配对帧中位数；RAW 最大偏离 {result.raw_max_deviation_norm:.1f}；"
            f"固件 Fz 对照基线 {self.firmware_tare:.3f} N，稳态范围 {result.force_range_n:.3f} N；"
            f"8 点位置噪声门限 {self.tare_noise_floor.min():.1f}–{self.tare_noise_floor.max():.1f}",
            "success",
        )

    def _warm_start_tare_history(self, frames):
        """Retain measured empty-load history after an explicit successful tare.

        Use the actual model under the new coordinates, never a fabricated zero
        force. Only its final continuous low-output suffix can seed the latch.
        This does not replay acquisition, recording, or protocol callbacks.
        """
        low_frames=[]
        reference=self._effective_tare_raw()
        for frame in frames:
            estimate=evaluate_raw_force(frame.raw,reference,
                use_provisional_high_force_correction=self.provisional_high_force_enabled)
            if estimate.fz_n>.05:
                low_frames.clear()
            else:
                low_frames.append((frame,estimate.fz_n))
        for frame,force in low_frames:
            diag=frame.diagnostic
            epoch=self._local_runtime_epoch(diag)
            self.local_force_result=self.local_force_runtime.feed(
                frame.raw,reference,self.tare_noise_floor,force,frame.received_at,
                source_token=frame.device_ms,epoch=epoch,
                valid=not frame.reset_reason and (diag is None or diag.valid),
                provisional_enabled=self.provisional_high_force_enabled
                    and self.force_mode_box.currentText()==RAW_TARE_MODE)

    def _update_force_display(self, paired_frame=None):
        """Run the full model in working RAW coordinates; retain manual diagnostics."""
        effective_tare=self._effective_tare_raw()
        self.firmware_tared_fz = firmware_fz_after_tare(self.firmware_force[2], self.firmware_tare)
        if self.tare_raw is None:
            self.manual_reference_model_n=0.0
            self.model_raw_fz = 0.0
            self.model_confidence = 0.0
            self.model_load_score = float("nan")
            self.model_soft_gate_rescue = False
            self.model_regression_unclamped = 0.0
            self.model_regression_clamped = 0.0
            self.model_invariant_fallback = 0.0
            self.model_release_ratio = float("inf")
            self.model_gate_reason = "NO_TARE"
            self.model_region_weights = np.full(4, 0.25, dtype=float)
            self.model_region_index = 0
            self.raw_activity, self.magnetic_coherence = raw_activity_metrics(self.raw, None)
            self.quality_flags = ("NO_TARE",)
            if self.force_mode_box.currentText() == RAW_TARE_MODE:
                self.device_fz = 0.0
                self.force_display_source = "未建立 RAW 基线：请空载静置后点击 RAW 置零"
            else:
                self.device_fz = self.firmware_tared_fz
                self.force_display_source = "固件 FORCE 兼容显示（尚未执行上位机 RAW 置零）"
        else:
            manual_result=evaluate_raw_force(self.raw,self.tare_raw,
                use_provisional_high_force_correction=self.provisional_high_force_enabled)
            self.manual_reference_model_n=manual_result.fz_n
            result = evaluate_raw_force(
                self.raw, effective_tare,
                use_provisional_high_force_correction=self.provisional_high_force_enabled,
            )
            if paired_frame is not None and self._update_raw_baseline(paired_frame,result):
                effective_tare=self._effective_tare_raw()
                result=evaluate_raw_force(self.raw,effective_tare,
                    use_provisional_high_force_correction=self.provisional_high_force_enabled)
            self.model_raw_fz = result.fz_n
            self.model_region_weights = result.region_weights
            self.model_confidence = result.confidence
            self.model_load_score = result.load_score
            self.model_soft_gate_rescue = result.soft_gate_rescue
            self.model_regression_unclamped = result.regression_unclamped_n
            self.model_regression_clamped = result.regression_clamped_n
            self.model_invariant_fallback = result.invariant_fallback_n
            self.model_release_ratio = result.release_similarity_ratio
            self.model_gate_reason = result.gate_reason
            self.model_region_index = result.best_region
            self.raw_activity = result.activity
            self.magnetic_coherence = result.coherence
            flags = list(result.flags)
            if MODEL_STATUS != ACCEPTANCE_MODEL_STATUS:
                flags.append("MODEL_VALIDATION_PENDING")
            if not self.provisional_high_force_enabled:
                flags.append("HIGH_FORCE_CORRECTION_OFF")
            if self.firmware_force[2] >= 34.95:
                flags.append("FIRMWARE_SATURATED")
            if np.any(self.raw_baseline_tracker.offset_raw24!=0.):
                flags.append('RELEASE_BASELINE_COMPENSATED')
            baseline_result=self._current_raw_baseline_result()
            if baseline_result is not None and baseline_result.changed:
                flags.append('RELEASE_BASELINE_UPDATED')
            self.quality_flags = tuple(dict.fromkeys(flags)) or ("OK",)
            if self.force_mode_box.currentText() == RAW_TARE_MODE:
                self.device_fz = result.fz_n
                self.force_display_source = "上位机 H3 V3-3 方向连续响应模型（正压已标定；斜压待定角验证；高力修正" + ("开启" if self.provisional_high_force_enabled else "关闭") + "）"
            else:
                self.device_fz = self.firmware_tared_fz
                self.force_display_source = "固件 FORCE－置零偏置（兼容诊断链路）"

            # Only the accepted _on_paired callback passes a frame. UI redisplay,
            # tare and selector callbacks never advance the causal state twice.
            if paired_frame is not None:
                diag = paired_frame.diagnostic
                epoch = self._local_runtime_epoch(diag)
                self.local_force_result = self.local_force_runtime.feed(
                    self.raw, effective_tare, self.tare_noise_floor, result.fz_n,
                    paired_frame.received_at, source_token=paired_frame.device_ms,
                    epoch=epoch,
                    valid=not paired_frame.reset_reason and (diag is None or diag.valid),
                    provisional_enabled=self.provisional_high_force_enabled
                    and self.force_mode_box.currentText() == RAW_TARE_MODE,
                )
            local = self._current_local_force_result()
            quality=local.evidence.get('baseline_quality') if local is not None else None
            if quality is not None:
                flag=('BASELINE_QUALITY_REJECTED' if quality['status']=='REJECTED' else
                      'BASELINE_QUALITY_PENDING' if quality['status']!='READY' else None)
                if flag:self.quality_flags=tuple(dict.fromkeys((*self.quality_flags,flag)))
            local_selected = self.local_force_runtime.profile in ("front","back","auto") and self.force_mode_box.currentText() == RAW_TARE_MODE
            if local_selected and result.fz_n>20. and (local is None or not local.applied):
                self.quality_flags=tuple(dict.fromkeys((*self.quality_flags,'LOCAL_CORRECTION_NOT_APPLIED')))
            if local_selected and local is not None and local.total_n is not None:
                self.device_fz = local.total_n
                self.numeric_zone_forces = local.zones.copy()
                area = self._local_force_area()
                self.force_display_source += (
                    f"；{area}校正已应用" if local.applied else
                    f"；{area}校正旁路（{self._local_force_reason_text(local.reason)}）"
                )
                if local.applied and self.local_force_runtime.profile=='auto':
                    self.force_display_source+=f'；本帧总力修正 {local.total_n-local.original_total_n:+.3f} N'
                if 'OUTSIDE_BACK25_VALIDATED_FORCE_RANGE' in local.warnings:
                    self.force_display_source += "；超出后25 N已验证范围"
                if 'RBF_CALIBRATION_PRIOR_OUTSIDE_NEIGHBORHOOD' in local.warnings:
                    self.force_display_source += "；后区状态外推，需复核"
            else:
                self.numeric_zone_forces = None
                if local_selected:
                    reason = (self._local_force_reason_text(local.reason) if local is not None else
                              "等待完整加载历史，请先空载置零静置2秒")
                    self.force_display_source += f"；{self._local_force_area()}校正旁路（{reason}）"

        # Use the selected value source for both the numeric result and the
        # animation.  The legacy firmware Fz is no longer allowed to inflate or
        # suppress a calibrated host-model animation.
        animation_total = self.device_fz
        target_map, animation_source = spatial_map_for_total(
            self.raw, effective_tare, animation_total, self.tare_noise_floor
        )
        if self.numeric_zone_forces is not None:
            target_map[..., 2] = self.numeric_zone_forces.reshape(4, 2)
            animation_source = self._local_zone_source()
        now = time.time()
        if animation_total > ANIMATION_ACTIVE_N:
            self.last_animation_active_at = now
            self.animation_force_map = target_map
        elif now - self.last_animation_active_at <= ANIMATION_RELEASE_HOLD_S:
            animation_source += f"；释放保持 {int(ANIMATION_RELEASE_HOLD_S * 1000)} ms"
        else:
            self.animation_force_map.fill(0.0)
        self.reconstructed_force_map = self.animation_force_map.copy()
        self.front_fz = float(self.reconstructed_force_map[:2, :, 2].sum())
        self.back_fz = float(self.reconstructed_force_map[2:, :, 2].sum())
        self.active_half = None if animation_total <= 0.0 else ("front" if self.front_fz >= self.back_fz else "back")
        warnings = []
        if 'BASELINE_QUALITY_REJECTED' in self.quality_flags:
            warnings.append('静置基线检查未通过，专项补偿暂停；请完全卸载静置后重新加载')
        if "MAGNETIC_COHERENCE" in self.quality_flags:
            warnings.append(f"疑似共模磁干扰 {self.magnetic_coherence:.0%}")
        if "MODEL_LOW_CONFIDENCE" in self.quality_flags:
            warnings.append(f"模型低置信度 {self.model_confidence:.0%}")
        if "MODEL_SOFT_GATE_RESCUE" in self.quality_flags:
            warnings.append("分类门槛异常，已保留连续力值而非清零")
        if "MODEL_DIRECTION_INVARIANT_FALLBACK" in self.quality_flags:
            warnings.append("斜压/未知方向：已保留响应，当前数值仅作连续性估计")
        if "MODEL_CLASSIFIER_IGNORED_CONTINUITY" in self.quality_flags:
            warnings.append("分类器与回归冲突：已保留回归值")
        if "MODEL_KNOWN_RELEASE_ZERO" in self.quality_flags:
            warnings.append("匹配已采集卸载恢复形态，已归零")
        if "UPPER_MODEL_SATURATED" in self.quality_flags or "FIRMWARE_SATURATED" in self.quality_flags:
            warnings.append("达到 35 N 上限，真实力可能更大")
        if "MODEL_VALIDATION_PENDING" in self.quality_flags:
            warnings.append("V3-3 模型待独立轮验证；0.2 N 以下尚未标定")
        if self.tare_raw is not None and self.model_raw_fz <= 0.05 and self.raw_activity >= RAW_ACTIVITY_WARNING:
            warnings.append("RAW 明显响应但模型≈0，该位置未可靠覆盖")
        if warnings:
            self.force_display_source = "警告：" + "；".join(warnings) + "；" + self.force_display_source
        self.last_animation_source = animation_source

    def _on_paired(self, frame):
        if self.sender() is not None and self.sender() is not self.reader:
            return
        if not isinstance(frame, PairedFrame):
            return
        if frame.diagnostic is not None and not frame.diagnostic.valid:
            self._on_diagnostic(frame.diagnostic)
            return
        gap_recovered = self._accept_stream_gap_frame(frame)
        if gap_recovered is None:
            return
        if frame.reset_reason:
            self._invalidate_host_tare()
            self.data_stale = True
            self.acquisition_diag_status = frame.reset_reason
            self.force_display_source = "警告：设备时间重新开始，旧置零失效；请停止并导出本轮，完全卸载重新置零，再清空本轮开始新记录"
            self._zero_stale_display((frame.reset_reason,))
            self._append_record("DIAG_RESET", frame.device_ms, frame.received_at)
            self._write_log(self.force_display_source, "error")
        self.acquisition_diagnostic = frame.diagnostic
        self.acquisition_diag_status = "VALID" if frame.diagnostic is not None else "UNAVAILABLE"
        self.acquisition_diag_raw_line = frame.diagnostic.raw_line if frame.diagnostic else ""
        self.raw = frame.raw.copy()
        self.firmware_force = frame.force.copy()
        self.paired_frames.append(frame)
        self.last_device_ms = frame.device_ms
        self.last_paired_at = frame.received_at
        if self.luo_force_map is not None:
            # A new accepted RAW/FORCE pair is the current stream; retire the
            # previous 12-zone map so it cannot be exported as this frame.
            self.luo_force_map = None
            self._reset_local_force("RAW_FORCE_STREAM_SELECTED")
        if self.protocol.kind == "high_force_continuous":
            self.high_force_last_received_mono = time.monotonic()
        self.data_stale = False
        self.stale_logged = False
        if self.luo_force_map is None:
            self._update_force_display(frame)
        if frame.diagnostic is None:
            self.quality_flags = tuple(dict.fromkeys((*self.quality_flags, "DIAG_UNAVAILABLE")))
            self.force_display_source += "；采集诊断不可用（旧固件兼容模式）"
        no_host_tare = self.tare_raw is None and self.force_mode_box.currentText() == RAW_TARE_MODE
        if gap_recovered and self._calibration_session_active():
            self.calibration_dialog.resume_after_stream_gap()
        if self.calibration_dialog is not None and self.calibration_dialog.isVisible() and not no_host_tare:
            self.calibration_dialog.feed_frame(
                frame,
                self._effective_tare_raw(),
                self.model_raw_fz,
                self.model_confidence,
                self.model_load_score,
                self.model_soft_gate_rescue,
                self.model_regression_unclamped,
                self.model_regression_clamped,
                self.model_invariant_fallback,
                self.model_release_ratio,
                self.model_gate_reason,
                audit_context=self._calibration_audit_context(frame),
            )
        self._render(frame.device_ms)
        self._capture_force_trace(frame)
        self._append_record("RAW_NO_TARE" if no_host_tare else "PAIRED", frame.device_ms, frame.received_at)
        if no_host_tare:
            return
        interference_flags = {
            "MAGNETIC_COHERENCE", "MODEL_LOW_CONFIDENCE", "DATA_TIMEOUT",
            "MODEL_VALIDATION_PENDING",
        }
        if self.protocol.kind == "high_force_continuous":
            interference_flags.discard("MODEL_VALIDATION_PENDING")
        quality_ok = not self.data_stale and not any(flag in interference_flags for flag in self.quality_flags)
        event = self.protocol.feed(
            time.monotonic() if self.protocol.kind == "high_force_continuous" else frame.received_at,
            self.device_fz if self.force_mode_box.currentText() == RAW_TARE_MODE else self.model_raw_fz,
            self.model_confidence,
            self.firmware_force[2],
            quality_ok=quality_ok,
        )
        if event is not None:
            self._update_protocol_ui(event)
        elif self.protocol.active and self.protocol.kind in {"long_hold_recovery", "emi_immunity", "high_force_continuous"}:
            self._update_protocol_ui(None)

    def _calibration_audit_context(self, frame):
        """Copy the accepted frame's actual coordinates and selection for replay."""
        effective_tare = self._effective_tare_raw()
        diag = frame.diagnostic
        return dict(
            monitor_version=MONITOR_VERSION,
            frame_received_at_host=frame.received_at,
            manual_tare_raw24=None if self.tare_raw is None else self.tare_raw.tolist(),
            effective_tare_raw24=None if effective_tare is None else effective_tare.tolist(),
            release_offset_raw24=self.raw_baseline_tracker.offset_raw24.tolist(),
            host_tare_epoch=self.host_tare_epoch,
            effective_baseline_epoch=self.raw_baseline_tracker.baseline_epoch,
            manual_reference_model_n=self.manual_reference_model_n,
            selected_force_n=self.device_fz,
            force_display_source=self.force_display_source,
            quality_flags=list(self.quality_flags),
            firmware_id=diag.firmware_id if diag is not None else None,
            valid_mask=diag.valid_mask if diag is not None else None,
            acquisition_errors=diag.error_count if diag is not None else None,
            firmware_baseline_epoch=diag.baseline_epoch if diag is not None else None,
        )

    def _capture_force_trace(self, frame):
        """Bounded local evidence; observation never changes force or tare."""
        local=self._current_local_force_result()
        baseline=self._current_raw_baseline_result()
        effective=self._effective_tare_raw()
        diag=frame.diagnostic
        self.force_trace.append(dict(
            received_at=frame.received_at, device_ms=frame.device_ms,
            transport=self.transport_box.currentText(),
            host_tare_epoch=self.host_tare_epoch,
            raw24=frame.raw, tare_raw24=effective,
            delta_raw24=None if effective is None else frame.raw-effective,
            manual_tare_raw24=self.tare_raw,
            manual_delta_raw24=None if self.tare_raw is None else frame.raw-self.tare_raw,
            manual_reference_model_n=self.manual_reference_model_n,
            effective_tare_raw24=effective,
            release_offset_raw24=self.raw_baseline_tracker.offset_raw24,
            effective_baseline_epoch=self.raw_baseline_tracker.baseline_epoch,
            raw_baseline_update_allowed=self._raw_baseline_updates_allowed(),
            raw_baseline_changed=bool(baseline and baseline.changed),
            raw_baseline_reason=baseline.reason if baseline else self.raw_baseline_tracker.last_reason,
            firmware_force_xyz=frame.force, selected_force_n=self.device_fz,
            original_model_force_n=self.model_raw_fz,
            force_mode=self.force_mode_box.currentText(),
            gate_reason=self.model_gate_reason, load_score=self.model_load_score,
            release_similarity_ratio=self.model_release_ratio,
            regression_unclamped_n=self.model_regression_unclamped,
            direction_invariant_fallback_n=self.model_invariant_fallback,
            quality_flags=self.quality_flags,
            baseline_quality=local.evidence.get('baseline_quality') if local else None,
            firmware_id=diag.firmware_id if diag else None,
            firmware_baseline_epoch=diag.baseline_epoch if diag else None,
            firmware_valid_mask=diag.valid_mask if diag else None,
            reset_reason=frame.reset_reason,
            release_zero_enabled=self.release_zero_enabled,
            release_zero_applied=False,
            release_zero_reason='DISPLAY_CLIP_REMOVED_RAW_BASELINE_MODE',
            release_zero_threshold_n=MAX_RESIDUAL_N,
        ))

    def _save_force_trace(self, reason):
        if not len(self.force_trace):
            return None
        path=self.force_trace.save(reason,dict(
            monitor_version=MONITOR_VERSION, force_model=FORCE_MODEL_ID,
            physical_unload_inferred=False, automatic_zero=False,
            automatic_zero_scope='MANUAL_TARE',release_zero_enabled=self.release_zero_enabled,
            release_zero_scope='RAW_WORK_BASELINE',
            raw_work_baseline_auto_update_enabled=self.release_zero_enabled))
        if path is not None:
            self.force_trace.clear()
            self._write_log(f"力值诊断已保存：{path}（原始信号及置零前读数保留）", "info")
        else:
            self._write_log("力值诊断未能写入文件；采集继续，诊断缓存保留", "error")
        return path

    def _on_luo_force_map(self, frame, received_at):
        """Prefer the already-computed original 4×3 force field whenever present."""
        if self.sender() is not None and self.sender() is not self.reader:
            return
        if self.acquisition_diagnostic is not None:
            return
        self._reset_local_force("LEGACY_12_ZONE_MAP")
        self.luo_force_map = np.asarray(frame, dtype=float).reshape(4, 3, 3)
        self.last_paired_at = received_at
        self.data_stale = False
        self.stale_logged = False
        vectors = self.luo_force_map.reshape(12, 3)
        self.device_fz = float(vectors[:, 2].sum())
        self.model_raw_fz = self.device_fz
        self.model_confidence = 0.0
        self.quality_flags = ("LEGACY_MAP_UNCALIBRATED",)
        self.force_display_source = "罗博士原版 36 分量 Fz 求和（仅兼容显示，不是 H3 合力标定）"
        # UI labels retain the v4.2 layout; the halves are only a row-based
        # summary of the original 4×3 map and do not alter its force vectors.
        self.front_fz = float(self.luo_force_map[:2, :, 2].sum())
        self.back_fz = float(self.luo_force_map[2:, :, 2].sum())
        self.active_half = "front" if self.front_fz >= self.back_fz else "back"
        self._render(self.last_device_ms)
        self._append_record("LUO_4X3", self.last_device_ms, received_at)

    def _zero_stale_display(self, flags=("DATA_TIMEOUT",)):
        self._reset_local_force("|".join(flags))
        self.device_fz = 0.0
        self.model_raw_fz = 0.0
        self.manual_reference_model_n = 0.0
        self.model_confidence = 0.0
        self.front_fz = 0.0
        self.back_fz = 0.0
        self.reconstructed_force_map = np.zeros((4, 2, 3), dtype=float)
        self.animation_force_map.fill(0.0)
        self.last_animation_active_at = 0.0
        self.quality_flags = flags
        if self.luo_force_map is not None:
            self.luo_force_map = np.zeros((4, 3, 3), dtype=float)
        self._render(self.last_device_ms)

    def _check_data_timeout(self):
        if self.reader is None or self.last_paired_at <= 0.0:
            return
        elapsed = (time.monotonic() - self.high_force_last_received_mono
                   if self.protocol.kind == "high_force_continuous" and self.high_force_last_received_mono is not None
                   else time.time() - self.last_paired_at)
        if elapsed <= PAIRED_DATA_TIMEOUT_S:
            return
        if not self.data_stale or self._stream_gap_context is not None:
            self.paired_frames.clear()
            self._interrupt_protocol()
            self.data_stale = True
            self.acquisition_diag_status = "TIMEOUT"
            self.force_display_source = "数据超时：超过 1 秒未收到完整帧，当前测量不可用"
            self._zero_stale_display()
            self._append_record("DATA_TIMEOUT", self.last_device_ms, time.time())
        if not self.stale_logged:
            self._write_log("数据超时：RAW/FORCE 未形成完整配对帧，已清除旧力值", "error")
            self.stale_logged = True

    def _render(self, device_ms):
        local=self._current_local_force_result()
        quality=local.evidence.get('baseline_quality') if local is not None else None
        baseline_text=quality_text(quality)
        if (quality and quality.get('reason')=='BASELINE_INSUFFICIENT_HISTORY'
                and self.tare_raw is not None and self.model_raw_fz>.05):
            baseline_text='补偿未取得加载前空载历史：确认完全卸载后点击空载置零（静置不会自动改零点）'
        if np.any(self.raw_baseline_tracker.offset_raw24!=0.):
            baseline_text+='；RAW工作零点补偿中；手动零点和原始信号保留'
        self.baseline_quality_label.setText(baseline_text)
        self.baseline_quality_label.setStyleSheet('color:#b7e1cc; padding:3px 0;' if quality and quality['status']=='READY'
                                                 else 'color:#f6d394; padding:3px 0;')
        prefix = "合力（标量模型）" if self.force_mode_box.currentText() == RAW_TARE_MODE else "固件原始 Fz 分量"
        missing_force = self.data_stale or (self.luo_force_map is None and self.tare_raw is None and self.force_mode_box.currentText() == RAW_TARE_MODE)
        self.total_readout.setText("— N" if missing_force else f"{self.device_fz:.2f} N")
        self.force_caption.setText(f"{prefix} · device_ms {device_ms}")
        self.status_banner.setText(self.force_display_source)
        warning_active = self.force_display_source.startswith("警告：")
        self.status_banner.setStyleSheet(
            "background:#40311f;color:#f6d394;border:1px solid #8a7042;border-radius:8px;padding:10px 11px;"
            if warning_active else
            "background:#17313b;color:#c3dbe0;border:1px solid #31515f;border-radius:8px;padding:10px 11px;"
        )
        if self.luo_force_map is not None:
            source, zones = self.unified_panel.update_luo_force_map(self.luo_force_map)
        else:
            source, zones = self.unified_panel.update_reconstructed_map(self.reconstructed_force_map)
        if self.luo_force_map is None and self.numeric_zone_forces is not None and not missing_force:
            # Numeric/CSV values use this frame's result, independently of the
            # point-cloud's low-force animation threshold.
            zones = self.numeric_zone_forces.copy()
            self.front_fz = float(zones[:4].sum())
            self.back_fz = float(zones[4:].sum())
            source = self._local_zone_source()
        self.front_readout.setText("— N" if missing_force else f"{self.front_fz:.2f} N")
        self.back_readout.setText("— N" if missing_force else f"{self.back_fz:.2f} N")
        self.last_zones = zones
        self.last_zone_source = source
        magnitudes = np.linalg.norm(self.raw.reshape(CHANNELS, 3), axis=1)
        effective=self._effective_tare_raw()
        delta = self.raw - effective if effective is not None else self.raw
        delta_magnitudes = np.linalg.norm(delta.reshape(CHANNELS, 3), axis=1)
        self.raw_readout.setText(
            f"Σ|ΔB| {delta_magnitudes.sum():.1f}   最大 S{int(np.argmax(delta_magnitudes)) + 1} {delta_magnitudes.max():.1f}\n"
            f"当前 Σ|B| {magnitudes.sum():.1f}   device_ms {device_ms}"
        )
        fx, fy, fz = self.firmware_force
        self.force_readout.setText(
            f"当前主值（{self.force_mode_box.currentText()}） {self.device_fz:.3f} N\n"
            f"工作零点模型合力（校正前）：{self.model_raw_fz:.3f} N；模型置信度 {self.model_confidence:.0%}；模型状态 {self._active_model_status()}\n"
            f"手动零点模型对照：{self.manual_reference_model_n:.3f} N；工作基线 {self.raw_baseline_tracker.baseline_epoch}\n"
            f"回归原值 {self.model_regression_unclamped:.3f} N → 裁限 {self.model_regression_clamped:.3f} N；"
            f"方向备用 {self.model_invariant_fallback:.3f} N；门控 {self.model_gate_reason}；"
            f"加载分数 {self.model_load_score:.3f}；卸载相似比 {self.model_release_ratio:.3f}\n"
            f"固件原始分量（禁止求模）：Fx {fx:.3f} · Fy {fy:.3f} · Fz {fz:.3f}；Fz 对照置零后 {self.firmware_tared_fz:.3f} N\n"
            f"RAW 活动 {self.raw_activity:.0f}；方向一致度 {self.magnetic_coherence:.0%}；质量标记 {'|'.join(self.quality_flags)}\n"
            f"{self.force_display_source}；动画：{source}"
        )
        diag = self.acquisition_diagnostic
        diagnostic_text = (f"采集诊断 {self.acquisition_diag_status} · {diag.firmware_id} · "
                           f"有效掩码 {diag.valid_mask}/255 · 累计失败通道数 {diag.error_count} · 基线 {diag.baseline_epoch}"
                           if diag is not None else "采集诊断不可用（旧固件或尚未收到 DIAG）")
        if missing_force:
            self.force_readout.setText(f"当前测量不可用：{self.force_display_source}\n{diagnostic_text}")
        else:
            self.force_readout.setText(self.force_readout.text() + "\n" + diagnostic_text)
        # Long calibration signatures must not expand the whole scroll panel.
        # Only display text is wrapped; CSV and model signatures stay exact.
        self.force_readout.setText(re.sub(r"([A-Za-z0-9_:|]{32})(?=[A-Za-z0-9_:|])", r"\1\n", self.force_readout.text()))
        if self.data_stale:
            self.raw_readout.setText("当前 RAW 不可用，等待完整有效配对帧")
        for index, value in enumerate(zones):
            self.zone_labels[index].setText(f"S{index + 1}  — N" if missing_force else f"S{index + 1}  {value:5.2f} N")

    def _record_base(self, event, device_ms, received_at):
        reference = self._effective_tare_raw()
        if reference is None:reference=np.zeros(RAW_COUNT,dtype=float)
        delta_raw = self.raw - reference
        context = self.protocol.context()
        row = [event, datetime.fromtimestamp(received_at).isoformat(timespec="milliseconds"), device_ms, self.device_fz, self.force_mode_box.currentText(), self.model_raw_fz, self._active_model_status(), self.model_regression_unclamped, self.model_regression_clamped, self.model_invariant_fallback, self.model_load_score, self.model_release_ratio, self.model_gate_reason, self.firmware_tared_fz, self.model_confidence, self.raw_activity, self.magnetic_coherence, REGION_NAMES[self.model_region_index], self.active_half or "none", self.front_fz, self.back_fz, self.firmware_force[0], self.firmware_force[1], self.firmware_force[2], self.firmware_tare, context.protocol, context.phase, context.point_index, context.nominal_force_n, context.reference_force_n, int(context.stable_sample), context.test_position, context.acceptance, "STALE" if self.data_stale else "PAIRED", "|".join(self.quality_flags), self.last_zone_source, self.position.text(), self.note.text(), *self.last_zones.tolist(), *self.raw.tolist(), *delta_raw.tolist()]
        # Append replay parameters; retain all existing CSV column positions.
        replayable = event == "PAIRED" and self.luo_force_map is None and not self.data_stale
        unavailable = event in {"DIAG_INVALID", "DIAG_RESET", "DIAG_ERROR_COUNT", "STREAM_ERROR", "STREAM_GAP", "DATA_TIMEOUT", "STREAM_DISCONNECTED", "RAW_NO_TARE", "TARE_CHANGED", "RAW_BASELINE_UPDATED"} or self.data_stale
        if unavailable:
            for index in [3, 5, *range(7, 12), *range(13, 24), *range(38, 46)]:
                row[index] = ""
            row[12] = self.acquisition_diag_status
            row[30] = 0
            row[33] = "NO_TARE" if event == "RAW_NO_TARE" else "INVALID"
            if event == "RAW_NO_TARE":
                row[70:94] = [""] * 24
            else:
                row[46:94] = [""] * 48
        diag = self.acquisition_diagnostic
        high_force = self.protocol.kind == "high_force_continuous"
        protocol_now = time.monotonic() if high_force else None
        phase_start = self.protocol.sample_started_at
        high_force_metadata = [
            getattr(self.protocol, "high_force_trajectory", "") if high_force else "",
            self.high_force_session_id if high_force else "",
            "MANUAL_PLATEAU_READING" if high_force and context.reference_force_n is not None else "",
            "MONOTONIC" if high_force else "",
            protocol_now - phase_start if high_force and context.phase == "hold_reference" and phase_start is not None else "",
            protocol_now - self.high_force_session_started_mono if high_force and self.high_force_session_started_mono is not None else "",
        ]
        return row + [SPATIAL_ALLOCATION_METHOD if replayable else "NOT_APPLICABLE",
                      SPATIAL_ALLOCATION_EXPONENT, ANIMATION_ACTIVE_N,
                      int(self.tare_raw is not None), *self.tare_noise_floor.tolist(), MONITOR_VERSION,
                      *high_force_metadata,
                      int(diag is not None), diag.firmware_id if diag else "",
                      diag.valid_mask if diag else "", diag.error_count if diag else "",
                      diag.baseline_epoch if diag else "", diag.device_ms if diag else "",
                      self.acquisition_diag_status, int(self.tare_raw is not None), self.acquisition_diag_raw_line,
                      self.host_tare_epoch] + export_row(
                          self._current_local_force_result() if replayable
                          and self.force_mode_box.currentText() == RAW_TARE_MODE else None
                      ) + self._baseline_csv_row(replayable) + self._release_zero_csv_row(replayable) + self._raw_baseline_csv_row(replayable)

    def _release_zero_csv_row(self, replayable):
        if not replayable:return ['']*len(RELEASE_ZERO_CSV_COLUMNS)
        # Preserve the v22 column positions while making the removed scalar
        # display clipping explicit. Working-baseline fields follow separately.
        return [int(self.release_zero_enabled),0,'DISPLAY_CLIP_REMOVED_RAW_BASELINE_MODE',MAX_RESIDUAL_N]

    def _raw_baseline_csv_row(self, replayable):
        state=[self.raw_baseline_tracker.baseline_epoch,
               self.manual_reference_model_n if replayable else '',int(self._raw_baseline_updates_allowed())]
        if not replayable or self.tare_raw is None:
            return state+['']*72
        return state+[*self.tare_raw.tolist(),*(self.raw-self.tare_raw).tolist(),
                      *self.raw_baseline_tracker.offset_raw24.tolist()]

    def _baseline_csv_row(self, replayable):
        import json
        local=self._current_local_force_result() if replayable else None
        quality=local.evidence.get('baseline_quality') if local is not None else None
        encode=lambda value:json.dumps(value,ensure_ascii=False,separators=(',',':'),allow_nan=False) if value is not None else ''
        return [DEFAULT_POLICY.policy_id, quality.get('status','') if quality else '',
                quality.get('reason','') if quality else '',encode(quality),encode(self.host_tare_quality)]

    def _baseline_start_allowed(self):
        local=self._current_local_force_result()
        quality=local.evidence.get('baseline_quality') if local is not None else None
        if (self.force_mode_box.currentText()==RAW_TARE_MODE and quality is not None
                and (quality.get('status')=='REJECTED' or
                     self.local_force_runtime.latch.quiet.loaded and quality.get('status')!='READY')):
            self._write_log('无法开始本轮：'+quality_text(quality)+'；已有原始记录仍可导出', 'error')
            return False
        return True

    def _append_record(self, event, device_ms, received_at):
        if self.recording:
            self.records.append(self._record_base(event, device_ms, received_at))
            self._update_record_state()

    def _start_recording(self):
        if self.recording:
            self._write_log("已处于记录中", "info")
            return
        if self.protocol.kind == "high_force_continuous" and not self.protocol.active:
            self._write_log("高力连续会话已结束，不能继续追加；请导出并清空，再开始新记录", "error")
            return
        if self.data_stale or self.last_paired_at <= 0.0:
            self._write_log("无法开始记录：尚未收到持续更新的完整 RAW+FORCE 配对帧", "error")
            return
        if self.force_mode_box.currentText() == RAW_TARE_MODE and self.tare_raw is None:
            self._write_log("无法开始记录：RAW 重建模式必须先在空载稳定后执行 RAW 置零", "error")
            return
        if self._recording_configuration_conflict():
            self._write_log("已有记录使用另一置零基线或力模型；请先导出并清空本轮，再开始新记录，不能混合统计", "error")
            return
        local=self._current_local_force_result()
        notices=[]
        quality=local.evidence.get('baseline_quality') if local is not None else None
        if (self.force_mode_box.currentText()==RAW_TARE_MODE and quality is not None
                and (quality.get('status')=='REJECTED' or
                     self.local_force_runtime.latch.quiet.loaded and quality.get('status')!='READY')):
            notices.append(quality_text(quality))
        if (self.force_mode_box.currentText()==RAW_TARE_MODE and self.local_force_runtime.profile in ('front','back','auto')
                and self.model_raw_fz>20. and (local is None or not local.applied)):
            reason=self._local_force_reason_text(local.reason) if local is not None else '当前帧缺少校正结果'
            notices.append('当前补偿未应用：'+reason)
        self.recording = True
        self._update_record_state()
        self._write_log(f"开始记录：将在当前本轮 Frame {len(self.records)} 后继续追加", "success")
        if notices:
            self._write_log('记录提示：'+'；'.join(notices)+'；记录已开始，当前显示值、原始值及补偿状态均保留', 'info')

    def _recording_configuration_conflict(self):
        epoch_index = self._csv_header().index("host_tare_epoch")
        return any(row[0] == "PAIRED" and (
            row[4] != self.force_mode_box.currentText() or row[6] != self._active_model_status()
            or len(row) <= epoch_index or row[epoch_index] != self.host_tare_epoch
        ) for row in self.records)

    def _stop_recording(self):
        if self.protocol.active and self.protocol.kind == "high_force_continuous":
            self._stop_protocol()
            return
        self.recording = False
        self._update_record_state()
        self._write_log(f"停止记录：本轮共 {len(self.records)} Frame", "info")

    def _clear_recording(self):
        if self.protocol.active:
            self._write_log("测试流程正在进行；请先结束并导出，再清空本轮", "error")
            return
        if self.records and QtWidgets.QMessageBox.question(self, "清空本轮", "清空当前轮次的全部记录？") != QtWidgets.QMessageBox.Yes: return
        self.records = []; self.recording = False; self._update_record_state()
        self.protocol = TestProtocol()
        self.high_force_session_started_mono = None
        self.high_force_session_id = ""
        self.high_force_last_received_mono = None
        self.high_force_reference_point = None
        self.high_force_reference_entered = False
        self._update_protocol_ui(None)
        self._write_log("已清空本轮记录", "info")

    def _mark(self):
        self.records.append(self._record_base("MARK", self.last_device_ms, time.time()))
        self._update_record_state()

    def _start_protocol(self):
        if self.protocol.active:
            self._write_log("已有测试正在进行", "error")
            return
        if self.calibration_dialog is not None and getattr(self.calibration_dialog, "session_started", False):
            self._write_log("标定采集正在进行；请先结束并导出，再开始测试流程", "error")
            return
        high_force = self.protocol_mode.currentIndex() in {3, 4}
        if high_force and (self.test_position_box.currentText() not in {"前", "后"} or self.force_mode_box.currentText() != RAW_TARE_MODE):
            self._write_log("高力连续标定请选择固定前/后位置，并使用上位机 RAW 模型主值", "error")
            return
        if high_force and self.records:
            self._write_log("高力连续标定需单独一份完整记录；请先导出并清空已有本轮数据", "error")
            return
        if self._recording_configuration_conflict():
            self._write_log("已有记录使用另一置零基线或力模型；请先导出并清空本轮，再开始新测试", "error")
            return
        if self.tare_raw is None or not self.tare_valid_for_new_test:
            self._write_log("每个独立测试开始前必须重新空载 RAW 置零一次", "error")
            return
        if self.data_stale or self.last_paired_at <= 0.0:
            self._write_log("无法开始测试：没有持续更新的完整配对帧", "error")
            return
        if not self._baseline_start_allowed():return
        now = time.time()
        if self.protocol_mode.currentIndex() in {1, 2} and MODEL_STATUS != ACCEPTANCE_MODEL_STATUS:
            self._write_log(
                "当前是 V3-3 三轮实测模型（0.2–30 N），尚未做独立验证；0.01 N 回零/电磁抗扰不会被判为合格",
                "error",
            )
        self.tare_valid_for_new_test = False
        self.tare_button.setEnabled(False)
        self.recording = True
        if self.protocol_mode.currentIndex() == 0:
            self.protocol.start_accuracy(now)
            self.reference_force.setValue(float(ACCURACY_SEQUENCE[0]))
            self._write_log("前/中/后同模型精度/迟滞开始：共 183 点，整轮同一模型、同一 RAW 基线，中途禁止置零", "success")
        elif self.protocol_mode.currentIndex() == 1:
            self.protocol.start_long_recovery(now, self.test_position_box.currentText())
            self.reference_force.setValue(30.0)
            self._write_log(f"长按恢复开始：位置 {self.test_position_box.currentText()}；30 N 保持 500 秒；卸载后禁止再次置零", "success")
        elif self.protocol_mode.currentIndex() == 2:
            self.protocol.start_emi(now, self.test_position_box.currentText())
            self.reference_force.setValue(0.0)
            self._write_log("电磁抗扰开始：先记录 30 秒无干扰空载基线；请在备注填写后续干扰源", "success")
        else:
            trajectory = "A" if self.protocol_mode.currentIndex() == 3 else "B"
            self.high_force_session_started_mono = time.monotonic()
            self.high_force_session_id = datetime.now().isoformat(timespec="milliseconds")
            self.high_force_last_received_mono = self.high_force_session_started_mono
            self.high_force_reference_point = None
            self.high_force_reference_entered = False
            self.protocol.start_high_force(self.high_force_session_started_mono, self.test_position_box.currentText(), trajectory)
            self._write_log(f"高力连续标定{trajectory}开始：固定{self.test_position_box.currentText()}位置；全程记录等待、持载和卸载，整条序列禁止置零。每步稳定后输入测力计实测读数并确认；测力计请另行全程记录。手填参考不是逐帧测力计真值，本流程不判定达标。", "success")
        self._update_record_state()
        self._update_protocol_ui("started")

    def _on_reference_force_edited(self, _value):
        if self.protocol.active and self.protocol.kind == "high_force_continuous" and self.protocol.phase == "waiting_for_load":
            self.high_force_reference_entered = True

    def _protocol_action(self):
        now = time.monotonic() if self.protocol.kind == "high_force_continuous" else time.time()
        try:
            if self.protocol.kind == "high_force_continuous":
                if not self.high_force_reference_entered or not self.reference_force.hasAcceptableInput():
                    raise ValueError("每个新平台请重新填写测力计实测读数，再开始保持")
                self.reference_force.interpretText()
                self.protocol.begin_high_force_step(self.reference_force.value(), now)
                self.high_force_reference_entered = False
                self._write_log(f"已确认稳定，开始保持：手填测力计读数 {self.reference_force.value():.3f} N；保留原始传感器力值", "info")
            elif self.protocol.kind == "accuracy_hysteresis_front_middle_rear":
                self.protocol.begin_accuracy_sample(self.reference_force.value(), now)
                self._write_log("开始 3 秒稳定窗口；保存测力计真实合力和软件合力", "info")
            elif self.protocol.kind == "long_hold_recovery":
                self.protocol.confirm_unloaded(now)
                self._write_log("已标记快速卸载到 0 N；开始空载恢复 120 秒，禁止置零", "info")
            elif self.protocol.kind == "emi_immunity":
                self.protocol.confirm_emi_on(now, self.note.text())
                self._write_log("开始 60 秒带干扰记录；禁止触碰、加载或置零", "info")
            else:
                raise ValueError("请先开始测试")
        except ValueError as error:
            self._write_log(str(error), "error")
        self._update_protocol_ui("action")

    def _stop_protocol(self):
        if not self.protocol.active and self.protocol.phase not in {"complete"}:
            self._write_log("当前没有进行中的测试", "info")
            return
        self.protocol.stop()
        if self.protocol.kind == "high_force_continuous":
            self._append_record("PROTOCOL_STOPPED", self.last_device_ms, time.time())
        self.recording = False
        self.tare_button.setEnabled(True)
        self._update_record_state()
        self._update_protocol_ui("stopped")
        self._write_log("测试已结束；下一项独立测试开始前必须重新空载 RAW 置零", "info")

    def _update_protocol_ui(self, event):
        context = self.protocol.context()
        completed_events = {
            "accuracy_complete", "recovery_complete_pass", "recovery_complete_fail",
            "emi_complete_pass", "emi_complete_fail",
            "high_force_complete", "high_force_clock_error",
        }
        if event in completed_events:
            if event == "high_force_clock_error":
                self.acquisition_diag_status = "PROTOCOL_CLOCK_ERROR"
                self._append_record("PROTOCOL_CLOCK_ERROR", self.last_device_ms, time.time())
            self.recording = False
            self.tare_button.setEnabled(True)
            self._update_record_state()
            level = "success" if event and event.endswith("pass") or event == "accuracy_complete" else "error"
            message = "高力连续记录完成，请导出 CSV 和平台统计；数据待校正模型验证" if event == "high_force_complete" else f"测试流程完成，验收={context.acceptance}；下一项测试前必须重新空载 RAW 置零"
            if event == "high_force_clock_error":
                message = "高力连续记录已因计时时钟异常中断；保留已有记录，请导出并重新开始"
            self._write_log(message, level)
        self.protocol_mode.setEnabled(not self.protocol.active)
        self.test_position_box.setEnabled(not self.protocol.active)
        self.reference_force.setEnabled(not self.protocol.active or context.phase == "waiting_for_load" or context.protocol != "high_force_continuous")
        if context.protocol == "high_force_continuous":
            self.protocol_action.setEnabled(self.protocol.active and context.phase == "waiting_for_load")
            if self.protocol.active:
                position, point, nominal, duration = self.protocol.high_force_target()
                trajectory = getattr(self.protocol, "high_force_trajectory", "A")
                if context.phase == "waiting_for_load":
                    reference_point = (self.high_force_session_id, point)
                    if self.high_force_reference_point != reference_point:
                        self.reference_force.clear()
                        self.high_force_reference_entered = False
                        self.high_force_reference_point = reference_point
                    self.protocol_action.setText("实测读数已填，开始保持")
                    self.protocol_status.setText(f"{trajectory} · {position}位置 · 第{point+1}/{len(HIGH_FORCE_TRAJECTORIES[trajectory])}步：{'完全卸载' if nominal == 0 else f'加载至{nominal:g} N'}；稳定后填实测参考，保持{duration:g}s")
                else:
                    elapsed = max(0, int(time.monotonic() - float(self.protocol.sample_started_at)))
                    self.protocol_action.setText("正在连续记录")
                    self.protocol_status.setText(f"{trajectory} · {position}位置 · 第{point+1}步：保持 {elapsed}/{duration:g}s；参考为手填平台读数；禁止置零")
            else:
                self.protocol_action.setText("实测读数已填，开始保持")
                self.protocol_status.setText("高力连续记录已完成或停止；请导出本轮，补偿尚未验收")
        elif context.protocol == "accuracy_hysteresis_front_middle_rear":
            if self.protocol.active and context.phase == "waiting_for_load":
                nominal = context.nominal_force_n
                self.reference_force.setValue(float(nominal))
                self.protocol_action.setEnabled(True)
                self.protocol_action.setText("载荷稳定，采样 3 秒")
                self.protocol_status.setText(f"{context.test_position}位置 · 点 {context.point_index + 1}/{ACCURACY_TOTAL_POINTS}：名义 {nominal:g} N；输入测力计实测值")
            elif context.phase == "stable_3s":
                self.protocol_action.setEnabled(False)
                self.protocol_status.setText(f"{context.test_position}位置 · 点 {context.point_index + 1}/{ACCURACY_TOTAL_POINTS}：稳定采样 3 秒中")
            else:
                self.protocol_action.setEnabled(False)
                self.protocol_status.setText("精度/迟滞测试已完成或停止")
        elif context.protocol == "long_hold_recovery":
            if context.phase == "hold_30n_500s":
                self.protocol_action.setEnabled(False)
                self.protocol_action.setText("已快速卸载到 0 N")
                elapsed = max(0, int(time.time() - float(self.protocol.phase_started_at)))
                self.protocol_status.setText(f"30 N 保持：{elapsed}/500 秒")
            elif context.phase == "await_fast_unload":
                self.protocol_action.setEnabled(True)
                self.protocol_action.setText("已快速卸载到 0 N")
                self.protocol_status.setText("请快速卸载到 0 N 后立即点击；不要置零")
            elif context.phase == "recovery_0n_120s":
                self.protocol_action.setEnabled(False)
                elapsed = max(0, int(time.time() - float(self.protocol.phase_started_at)))
                self.protocol_status.setText(f"空载恢复：{elapsed}/120 秒；瞬态≤{FAST_RECOVERY_LIMIT_N:.1f} N，末10秒最大残差≤{RECOVERY_STEADY_LIMIT_N:.2f} N")
            else:
                self.protocol_action.setEnabled(False)
                self.protocol_status.setText(f"长按恢复已完成/停止 · 验收 {context.acceptance}")
        elif context.protocol == "emi_immunity":
            if context.phase == "baseline_30s":
                self.protocol_action.setEnabled(False)
                elapsed = max(0, int(time.time() - float(self.protocol.phase_started_at)))
                self.protocol_status.setText(f"无干扰空载基线：{elapsed}/{int(EMI_BASELINE_S)} 秒")
            elif context.phase == "await_emi_on":
                self.protocol_action.setEnabled(True)
                self.protocol_action.setText("备注已填，开启干扰")
                self.protocol_status.setText("在备注填写干扰源，开启干扰后立即点击；不要触碰传感器")
            elif context.phase == "emi_exposure_60s":
                self.protocol_action.setEnabled(False)
                elapsed = max(0, int(time.time() - float(self.protocol.phase_started_at)))
                self.protocol_status.setText(f"电磁干扰暴露：{elapsed}/{int(EMI_EXPOSURE_S)} 秒；自动监测漂移、尖峰、质量标记和断流")
            else:
                self.protocol_action.setEnabled(False)
                self.protocol_status.setText(f"电磁抗扰已完成/停止 · 验收 {context.acceptance}")
        else:
            self.protocol_action.setEnabled(False)
            self.protocol_status.setText("测试未开始")

    @staticmethod
    def _csv_header():
        header = ["record_type", "local_time_iso", "device_ms", "selected_resultant_or_diagnostic_n", "selected_value_mode", "scalar_resultant_raw_model_n", "scalar_model_status", "regression_unclamped_n", "regression_clamped_n", "direction_invariant_fallback_n", "load_classifier_score", "release_similarity_ratio", "gate_reason", "firmware_fz_tared_diagnostic_n", "scalar_model_confidence_0_1", "raw_activity_sum", "magnetic_direction_coherence_0_1", "raw_model_best_region", "raw_model_dominant_half", "front_resultant_allocation_estimated_n", "back_resultant_allocation_estimated_n", "firmware_fx_raw_component_unvalidated", "firmware_fy_raw_component_unvalidated", "firmware_fz_raw_component_n", "firmware_fz_tare_reference_n", "test_protocol", "test_phase", "test_point_index", "nominal_reference_resultant_n", "measured_reference_resultant_n", "stable_3s_sample", "test_position", "acceptance_state", "frame_state", "quality_flags", "zone_animation_source", "position_mark", "note", *[f"S{i}_resultant_allocation_estimated_n" for i in range(1, 9)], *[f"S{i}_raw_dB{axis}" for i in range(1, 9) for axis in "xyz"], *[f"S{i}_retared_dB{axis}" for i in range(1, 9) for axis in "xyz"]]
        header += ["spatial_allocation_method", "spatial_allocation_exponent", "spatial_animation_active_threshold_n",
                   "spatial_tare_present", *[f"S{i}_spatial_noise_floor" for i in range(1, 9)], "monitor_version"]
        header += HIGH_FORCE_CSV_COLUMNS
        header += DIAGNOSTIC_CSV_COLUMNS
        header += LOCAL_FORCE_CSV_COLUMNS
        header += BASELINE_CSV_COLUMNS
        header += RELEASE_ZERO_CSV_COLUMNS
        header += RAW_BASELINE_CSV_COLUMNS
        return header

    def _export(self):
        if not self.records: self._write_log("没有可导出的记录", "error"); return
        suggested = f"TacAI_H3_resultant_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        path, _ = QtWidgets.QFileDialog.getSaveFileName(self, "导出 CSV", suggested, "CSV Files (*.csv)")
        if not path: return
        header = self._csv_header()
        with open(path, "w", newline="", encoding="utf-8-sig") as output:
            writer = csv.writer(output); writer.writerow(header); writer.writerows(self.records)
        if self.protocol.summaries:
            summary_suffix = "_high_force_summary.csv" if self.protocol.kind == "high_force_continuous" else "_stable3s_summary.csv"
            summary_path = path[:-4] + summary_suffix if path.lower().endswith(".csv") else path + summary_suffix
            summary_header = list(self.protocol.summaries[0].keys())
            with open(summary_path, "w", newline="", encoding="utf-8-sig") as output:
                writer = csv.DictWriter(output, fieldnames=summary_header); writer.writeheader(); writer.writerows(self.protocol.summaries)
        self._write_log(f"已导出 {len(self.records)} 条：{path}", "success")

    def closeEvent(self, event):
        if self.device_probe is not None:
            self.device_probe.requestInterruption()
            if not self.device_probe.wait(2500):
                event.ignore()
                return
        if self.reader:
            self.reader.stop()
            if not self.reader.wait(2500):
                self._write_log("正在结束设备事务，请稍后再关闭窗口", "info")
                event.ignore()
                return
        self._interrupt_protocol()
        self._save_force_trace("WINDOW_CLOSED")
        event.accept()


if __name__ == "__main__":
    if "--generate-folder-reports" in sys.argv or "--generate-folder-markdown" in sys.argv:
        markdown = "--generate-folder-markdown" in sys.argv
        argument_index = sys.argv.index("--generate-folder-markdown" if markdown else "--generate-folder-reports")
        try:
            input_directory = sys.argv[argument_index + 1]
            output_directory = sys.argv[argument_index + 2]
        except IndexError:
            print("用法：--generate-folder-reports <CSV目录> <输出目录>", file=sys.stderr)
            raise SystemExit(2)
        print(generate_consistency_report(input_directory, output_directory, mode="folders_md" if markdown else "folders"))
        raise SystemExit(0)
    if "--generate-consistency-report" in sys.argv or "--generate-recording-report" in sys.argv:
        recording_report = "--generate-recording-report" in sys.argv
        argument_index = sys.argv.index("--generate-recording-report" if recording_report else "--generate-consistency-report")
        try:
            input_directory = sys.argv[argument_index + 1]
            output_workbook = sys.argv[argument_index + 2]
        except IndexError:
            print("用法：--generate-consistency-report <CSV目录> <输出.xlsx>", file=sys.stderr)
            raise SystemExit(2)
        result = generate_consistency_report(input_directory, output_workbook, mode="recordings" if recording_report else "calibration")
        print(result)
        raise SystemExit(0)
    QtCore.QCoreApplication.setAttribute(QtCore.Qt.AA_UseDesktopOpenGL)
    application = QtWidgets.QApplication(sys.argv)
    monitor = Monitor()
    if "--self-test" in sys.argv:
        if "--self-test-json" in sys.argv:
            import json
            output = Path(sys.argv[sys.argv.index("--self-test-json") + 1])
            model = monitor.local_force_runtime.model
            evidence = {
                "version": MONITOR_VERSION,
                "selection_index": monitor.local_force_box.currentIndex(),
                "selection_text": monitor.local_force_box.currentText(),
                "runtime_profile": monitor.local_force_runtime.profile,
                "runtime_model_method": getattr(model, "method", None),
                "default_back25_selected": model is monitor.back25_smooth_model and model is not None,
                "automatic_compensation_enabled": model is monitor.automatic_compensation_model and model is not None,
                "automatic_available_profiles": list(getattr(model,'models',{})),
                "parameter_load_error": monitor.back25_smooth_load_error,
                "serial_connected": monitor.reader is not None,
                "baseline_quality_policy": DEFAULT_POLICY.__dict__,
            }
            with output.open("x", encoding="utf-8") as stream:
                json.dump(evidence, stream, ensure_ascii=False, indent=2)
            if not evidence["automatic_compensation_enabled"]:
                raise SystemExit(1)
        QtCore.QTimer.singleShot(250, application.quit)
    else:
        monitor.show()
    sys.exit(application.exec_())

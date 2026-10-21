"""Antialiased 2.5D display of the eight physical H3 Hall positions.

This widget is deliberately presentation-only.  Incoming Fz allocations and
their source are supplied by the monitor; no calibration or device I/O occurs
here.  The arrows point along a screen-space +Fz display axis, not the measured
direction of a three-dimensional force vector.
"""

from __future__ import annotations

import time

import numpy as np
from PyQt5 import QtCore, QtGui, QtWidgets


# Keep the motion contract identical to the incumbent GL view.  These values
# affect only the drawing; last_zones and all numeric labels update at once.
ARROW_RISE_TIME_S = 0.075
ARROW_RELEASE_TIME_S = 0.120
ARROW_MAX_FRAME_DT_S = 0.050
ARROW_ZERO_SNAP = 0.015
DISPLAY_MAX_N = 8.0

BG = "#071419"
SURFACE = "#0D2026"
SURFACE_LINE = "#27444B"
BOARD_TOP = "#253940"
BOARD_SIDE = "#13252B"
PAD = "#1C3036"
PAD_ACTIVE = "#254147"
PAD_LINE = "#49646A"
PAD_LINE_IDLE = "#365157"
TEXT = "#EDF5F4"
SECONDARY = "#9BB8BE"
MUTED = "#718E96"
CYAN = "#6ADBD5"
AMBER = "#F2C46D"


def _qpoint(point):
    return QtCore.QPointF(float(point[0]), float(point[1]))


def _polygon(points):
    return QtGui.QPolygonF([_qpoint(point) for point in points])


def _at(origin, u, v, col, row):
    return (origin[0] + u[0] * col + v[0] * row,
            origin[1] + u[1] * col + v[1] * row)


def _chamfered_cell(origin, u, v, col, row):
    corners = [_at(origin, u, v, col + x, row + y)
               for x, y in ((0.08, 0.08), (0.92, 0.08),
                            (0.92, 0.92), (0.08, 0.92))]
    points = []
    for index, corner in enumerate(corners):
        for neighbor in (corners[index - 1], corners[(index + 1) % 4]):
            points.append((corner[0] + 0.065 * (neighbor[0] - corner[0]),
                           corner[1] + 0.065 * (neighbor[1] - corner[1])))
    return _polygon(points)


class ForceField2D(QtWidgets.QWidget):
    """Render a 4-row × 2-column Fz allocation on a matte perspective board.

    ``last_zones`` always holds the latest numeric Fz values in S1..S8 order.
    Only arrow lengths and the small on-board bars interpolate between frames.
    """

    def __init__(self, parent=None, simulated=False):
        super().__init__(parent)
        self.simulated = bool(simulated)
        self.last_zones = np.zeros(8, dtype=float)
        self._target_zones = np.zeros(8, dtype=float)
        self._display_zones = np.zeros(8, dtype=float)
        self._zone_velocity = np.zeros(8, dtype=float)
        self._reduce_motion = False
        self._last_tick_s = time.perf_counter()
        self._animation_timer = QtCore.QTimer(self)
        self._animation_timer.setTimerType(QtCore.Qt.PreciseTimer)
        self._animation_timer.setInterval(16)
        self._animation_timer.timeout.connect(self._advance_animation)
        self.setAttribute(QtCore.Qt.WA_OpaquePaintEvent)
        self.setAutoFillBackground(False)

    def sizeHint(self):
        return QtCore.QSize(900, 550)

    def minimumSizeHint(self):
        return QtCore.QSize(500, 320)

    def set_reduce_motion(self, enabled):
        """Show the current target without moving arrow or bar geometry."""
        self._reduce_motion = bool(enabled)
        if self._reduce_motion:
            self._animation_timer.stop()
            self._display_zones = self._target_zones.copy()
            self._zone_velocity.fill(0.0)
            self.update()

    def _update_force_map(self, frame, source):
        values = np.asarray(frame, dtype=float).reshape(4, 2, 3)[:, :, 2].reshape(8)
        self.last_zones = np.nan_to_num(values, nan=0.0, posinf=0.0,
                                        neginf=0.0).copy()
        self._target_zones = np.maximum(self.last_zones, 0.0)
        if self._reduce_motion:
            self._display_zones = self._target_zones.copy()
            self._zone_velocity.fill(0.0)
            self._animation_timer.stop()
        elif not self._animation_timer.isActive():
            self._last_tick_s = time.perf_counter()
            self._animation_timer.start()
        # Repaint now: the numerical truth never waits for the motion timer.
        self.update()
        return source

    def update_reconstructed_map(self, frame):
        """Accept the monitor's real 4×2×3 Hall-position frame."""
        return self._update_force_map(
            frame, "真实 4×2 霍尔响应（主模型合力空间分配）")

    def update_luo_force_map(self, frame):
        """Collapse a legacy 4×3×3 map to the physical 4×2 positions."""
        legacy = np.asarray(frame, dtype=float).reshape(4, 3, 3)
        physical = np.empty((4, 2, 3), dtype=float)
        physical[:, 0] = legacy[:, 0] + 0.5 * legacy[:, 1]
        physical[:, 1] = legacy[:, 2] + 0.5 * legacy[:, 1]
        return self._update_force_map(
            physical, "旧 4×3 帧折算到真实 4×2 位置")

    @staticmethod
    def _spring_step(current, velocity, target, dt, smooth_time):
        """Same critically damped, retargetable SmoothDamp step as the GL view."""
        omega = 2.0 / max(float(smooth_time), 1e-3)
        x = omega * dt
        decay = 1.0 / (1.0 + x + 0.48 * x * x + 0.235 * x * x * x)
        change = current - target
        temp = (velocity + omega * change) * dt
        next_velocity = (velocity - omega * temp) * decay
        output = target + (change + temp) * decay
        return output, next_velocity

    def _advance_animation(self):
        now = time.perf_counter()
        dt = min(max(now - self._last_tick_s, 1.0 / 240.0), ARROW_MAX_FRAME_DT_S)
        self._last_tick_s = now
        for index in range(8):
            current = self._display_zones[index]
            target = self._target_zones[index]
            smooth_time = (ARROW_RISE_TIME_S if target >= current
                           else ARROW_RELEASE_TIME_S)
            output, velocity = self._spring_step(
                current, self._zone_velocity[index], target, dt, smooth_time)
            if target <= ARROW_ZERO_SNAP and output <= ARROW_ZERO_SNAP:
                output, velocity = 0.0, 0.0
            self._display_zones[index] = max(output, 0.0)
            self._zone_velocity[index] = velocity
        if (np.all(np.abs(self._display_zones - self._target_zones) < 0.003)
                and np.all(np.abs(self._zone_velocity) < 0.02)):
            self._display_zones = self._target_zones.copy()
            self._zone_velocity.fill(0.0)
            self._animation_timer.stop()
        self.update()

    @staticmethod
    def _pen(color, width=1.0):
        pen = QtGui.QPen(QtGui.QColor(color))
        pen.setWidthF(width)
        pen.setJoinStyle(QtCore.Qt.RoundJoin)
        pen.setCapStyle(QtCore.Qt.RoundCap)
        return pen

    @staticmethod
    def _text(painter, rect, label, size, color=TEXT, bold=False,
              align=QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter,
              number=False):
        font = QtGui.QFont("Segoe UI" if number else "Microsoft YaHei")
        font.setPixelSize(size)
        font.setWeight(QtGui.QFont.DemiBold if bold else QtGui.QFont.Normal)
        painter.setFont(font)
        painter.setPen(QtGui.QColor(color))
        painter.drawText(QtCore.QRectF(*rect), int(align), label)

    @staticmethod
    def _line(painter, a, b, color, width=1.0):
        painter.setPen(ForceField2D._pen(color, width))
        painter.drawLine(_qpoint(a), _qpoint(b))

    @staticmethod
    def _flat_shape(painter, points, fill, outline=None, width=1.0):
        painter.setBrush(QtGui.QColor(fill))
        painter.setPen(ForceField2D._pen(outline, width) if outline
                       else QtCore.Qt.NoPen)
        painter.drawPolygon(_polygon(points))

    def _draw_arrow(self, painter, center, load, peak):
        cx, cy = center
        accent = AMBER if peak else CYAN
        if load <= ARROW_ZERO_SNAP:
            painter.setBrush(QtGui.QColor(PAD_LINE_IDLE))
            painter.setPen(self._pen(MUTED, 1.0))
            painter.drawEllipse(QtCore.QPointF(cx, cy), 4.0, 4.0)
            return

        # A screen-space Fz glyph anchored to its cell.  Geometry has one
        # fixed 0..8 N scale across every frame, with saturation above 8 N.
        height = 105.0 * min(load / DISPLAY_MAX_N, 1.0)
        self._line(painter, (cx - 14, cy + 2), (cx + 14, cy + 2), accent, 2.0)
        painter.setBrush(QtGui.QColor(accent))
        painter.setPen(QtCore.Qt.NoPen)
        painter.drawEllipse(QtCore.QPointF(cx, cy + 2), 3.5, 3.5)
        if height < 12.0:
            return
        if height < 24.0:
            self._line(painter, (cx, cy - 5), (cx, cy - height + 5), accent, 5.0)
            self._flat_shape(painter,
                             [(cx, cy - height), (cx + 7, cy - height + 9),
                              (cx - 7, cy - height + 9)], accent)
            return
        top = cy - height
        head_height = min(19.0, max(11.0, height * 0.27))
        head_half = 14.0 if peak else 12.0
        shaft_half = 5.0 if peak else 4.5
        self._flat_shape(painter, [
            (cx, top), (cx + head_half, top + head_height),
            (cx + shaft_half, top + head_height), (cx + shaft_half, cy - 5),
            (cx - shaft_half, cy - 5), (cx - shaft_half, top + head_height),
            (cx - head_half, top + head_height),
        ], accent)

    def _draw_scene(self, painter):
        painter.setPen(self._pen(SURFACE_LINE, 1.0))
        painter.setBrush(QtGui.QColor(SURFACE))
        painter.drawRoundedRect(QtCore.QRectF(8, 8, 622, 534), 12, 12)
        self._text(painter, (28, 25, 380, 32), "八点法向力分布", 26, bold=True)
        self._text(painter, (28, 60, 510, 23),
                   "4 行 × 2 列   /   S1–S4 前区   /   S5–S8 后区",
                   16, SECONDARY)
        self._text(painter, (570, 28, 36, 22), "Fz", 17, CYAN, True,
                   QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, True)
        self._line(painter, (594, 68), (594, 88), CYAN, 2.0)
        self._flat_shape(painter,
                         [(594, 61), (588, 72), (600, 72)], CYAN)

        origin, u, v = (280.0, 160.0), (150.0, 25.0), (-52.0, 68.0)
        a, b = origin, _at(origin, u, v, 2, 0)
        c, d = _at(origin, u, v, 2, 4), _at(origin, u, v, 0, 4)
        depth = (0.0, 8.0)
        self._flat_shape(painter,
                         [d, c, (c[0], c[1] + depth[1]),
                          (d[0], d[1] + depth[1])], BOARD_SIDE)
        self._flat_shape(painter,
                         [b, c, (c[0], c[1] + depth[1]),
                          (b[0], b[1] + depth[1])], "#182A30")
        self._flat_shape(painter, [a, b, c, d], BOARD_TOP, "#647A7E")

        peak = (int(np.argmax(self._target_zones))
                if np.max(self._target_zones) > ARROW_ZERO_SNAP else -1)
        for index in range(8):
            row, col = divmod(index, 2)
            load = self._display_zones[index]
            active = self._target_zones[index] > ARROW_ZERO_SNAP
            is_peak = index == peak
            painter.setBrush(QtGui.QColor(PAD_ACTIVE if active else PAD))
            painter.setPen(self._pen(AMBER if is_peak else
                                     PAD_LINE if active else PAD_LINE_IDLE,
                                     1.7 if is_peak else 1.0))
            painter.drawPolygon(_chamfered_cell(origin, u, v, col, row))
            if active:
                start = _at(origin, u, v, col + 0.59, row + 0.75)
                end = _at(origin, u, v,
                          col + 0.59 + 0.25 * min(load / DISPLAY_MAX_N, 1.0),
                          row + 0.75)
                self._line(painter, start, end, AMBER if is_peak else CYAN, 3.0)

        # This line is the reporting split between S1–S4 and S5–S8.
        self._line(painter, _at(origin, u, v, 0, 2),
                   _at(origin, u, v, 2, 2), "#8EAAAE", 1.8)
        for index in range(8):
            row, col = divmod(index, 2)
            center = _at(origin, u, v, col + 0.5, row + 0.5)
            self._draw_arrow(painter, center, self._display_zones[index],
                             index == peak)
            label = _at(origin, u, v, col + 0.13, row + 0.69)
            self._text(painter, (label[0], label[1], 55, 27), f"S{index + 1}",
                       18, AMBER if index == peak else TEXT)

        self._line(painter, (28, 504), (608, 504), SURFACE_LINE)
        self._text(painter, (28, 510, 578, 22),
                   "箭头沿 +Fz 显示轴；长度按 0–8 N 固定比例。", 14,
                   SECONDARY)

    def _draw_sidebar(self, painter):
        painter.setPen(self._pen(SURFACE_LINE, 1.0))
        painter.setBrush(QtGui.QColor(SURFACE))
        painter.drawRoundedRect(QtCore.QRectF(638, 8, 254, 534), 12, 12)
        self._text(painter, (656, 25, 120, 24), "合力", 19, SECONDARY)
        total = float(np.sum(self.last_zones))
        front = float(np.sum(self.last_zones[:4]))
        rear = float(np.sum(self.last_zones[4:]))
        self._text(painter, (655, 52, 171, 54), f"{total:.2f}", 44,
                   TEXT, number=True)
        self._text(painter, (828, 72, 45, 30), "N", 19, SECONDARY,
                   number=True)
        caption = ("模拟载荷 · 非设备数据" if self.simulated
                   else "局部响应分配 · 实时数据")
        self._text(painter, (656, 107, 214, 20), caption, 14,
                   MUTED)
        self._line(painter, (656, 136), (875, 136), SURFACE_LINE)
        self._text(painter, (656, 147, 138, 23), "前区  S1–S4", 16,
                   SECONDARY)
        self._text(painter, (782, 147, 93, 23), f"{front:.2f} N", 17,
                   TEXT, align=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   number=True)
        self._text(painter, (656, 176, 138, 23), "后区  S5–S8", 16,
                   SECONDARY)
        self._text(painter, (782, 176, 93, 23), f"{rear:.2f} N", 17,
                   TEXT, align=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                   number=True)
        self._line(painter, (656, 210), (875, 210), SURFACE_LINE)
        self._text(painter, (656, 222, 180, 25), "位置响应", 19, TEXT,
                   True)
        self._text(painter, (656, 249, 220, 20),
                   "局部 ΔB 分配 · 非逐点标定", 14, SECONDARY)
        peak = (int(np.argmax(self._target_zones))
                if np.max(self._target_zones) > ARROW_ZERO_SNAP else -1)
        for index, value in enumerate(self.last_zones):
            y = 278 + index * 27
            active = value > ARROW_ZERO_SNAP
            accent = AMBER if index == peak else CYAN
            self._text(painter, (656, y, 37, 22), f"S{index + 1}", 16,
                       accent if active else SECONDARY, True)
            painter.setPen(QtCore.Qt.NoPen)
            painter.setBrush(QtGui.QColor("#274046"))
            painter.drawRoundedRect(QtCore.QRectF(697, y + 9, 94, 5), 2.5, 2.5)
            if self._display_zones[index] > ARROW_ZERO_SNAP:
                painter.setBrush(QtGui.QColor(accent))
                painter.drawRoundedRect(
                    QtCore.QRectF(697, y + 9,
                                  94 * min(self._display_zones[index] /
                                           DISPLAY_MAX_N, 1.0), 5), 2.5, 2.5)
            self._text(painter, (798, y, 77, 22), f"{value:.2f}", 16,
                       accent if active else SECONDARY,
                       align=QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter,
                       number=True)
        self._text(painter, (656, 509, 220, 22),
                   "仅显示 Fz；不暗示 Fx / Fy 标定。", 13, MUTED)

    def paintEvent(self, event):
        painter = QtGui.QPainter(self)
        painter.setRenderHint(QtGui.QPainter.Antialiasing, True)
        painter.setRenderHint(QtGui.QPainter.TextAntialiasing, True)
        painter.fillRect(self.rect(), QtGui.QColor(BG))
        scale = min(self.width() / 900.0, self.height() / 550.0)
        painter.translate((self.width() - 900.0 * scale) * 0.5,
                          (self.height() - 550.0 * scale) * 0.5)
        painter.scale(scale, scale)
        self._draw_scene(painter)
        self._draw_sidebar(painter)
        painter.end()

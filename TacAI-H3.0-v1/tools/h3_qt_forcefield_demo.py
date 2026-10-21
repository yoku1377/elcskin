"""Run or measure the optional H3 antialiased force-field preview.

This program uses synthetic loads only. It never opens a serial port, writes
firmware, or changes calibration. Example:

    python tools/h3_qt_forcefield_demo.py
    python tools/h3_qt_forcefield_demo.py --benchmark --screenshot docs/h3_qt_forcefield_preview.png
"""

from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc" / "TfgMonitorPython_H3_v5.0"))

from PyQt5 import QtCore, QtGui, QtWidgets  # noqa: E402
from forcefield_2d import ForceField2D  # noqa: E402


class MeasuredForceField(ForceField2D):
    """Record completed paint times, not merely scheduled QTimer callbacks."""

    def __init__(self):
        super().__init__(simulated=True)
        self.paint_started = []
        self.paint_duration_ms = []

    def paintEvent(self, event):
        start = time.perf_counter()
        super().paintEvent(event)
        self.paint_started.append(start)
        self.paint_duration_ms.append((time.perf_counter() - start) * 1000.0)


class Demo(QtWidgets.QWidget):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("H3 2.5D 抗锯齿动态预览 · 模拟数据")
        self.resize(1100, 720)
        self.setStyleSheet("background: #071419; color: #EDF5F4;")
        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(18, 18, 18, 18)
        layout.setSpacing(12)
        title = QtWidgets.QLabel("八点法向力分布  /  动态视觉预览")
        title.setStyleSheet("font: 600 20px 'Microsoft YaHei'; color: #EDF5F4;")
        layout.addWidget(title)
        description = QtWidgets.QLabel(
            "模拟载荷，不是设备实时数据。数字直接显示目标值；只对箭头长度作视觉过渡。"
        )
        description.setStyleSheet("font: 13px 'Microsoft YaHei'; color: #9BB8BE;")
        layout.addWidget(description)
        self.view = MeasuredForceField()
        layout.addWidget(self.view, 1)
        controls = QtWidgets.QHBoxLayout()
        for label, values in (
            ("空载 0 N", [0.0] * 8),
            ("轻触 0.2 N", [0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
            ("多点 12 N", [7.5, 0.0, 0.0, 3.0, 0.0, 0.0, 1.5, 0.0]),
            ("强压 30 N", [16.0, 0.0, 0.0, 9.0, 0.0, 0.0, 5.0, 0.0]),
        ):
            button = QtWidgets.QPushButton(label)
            button.setMinimumHeight(34)
            button.setStyleSheet(
                "QPushButton { background: #1A353B; border: 1px solid #42616A; "
                "border-radius: 5px; padding: 6px 12px; }"
                "QPushButton:hover { background: #24474D; }"
                "QPushButton:focus { border: 2px solid #6ADBD5; }"
            )
            button.clicked.connect(lambda _checked=False, v=values: self.set_zones(v))
            controls.addWidget(button)
        controls.addStretch()
        self.reduce_motion = QtWidgets.QCheckBox("减少动画")
        self.reduce_motion.toggled.connect(self.view.set_reduce_motion)
        controls.addWidget(self.reduce_motion)
        layout.addLayout(controls)
        self.set_zones([7.5, 0.0, 0.0, 3.0, 0.0, 0.0, 1.5, 0.0])

    def set_zones(self, values):
        frame = np.zeros((4, 2, 3), dtype=float)
        frame[:, :, 2] = np.asarray(values, dtype=float).reshape(4, 2)
        self.view.update_reconstructed_map(frame)


def percentile(values, fraction):
    if not values:
        return float("nan")
    ordered = sorted(values)
    return ordered[round((len(ordered) - 1) * fraction)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--benchmark", action="store_true", help="run a 3-second synthetic-load paint test")
    parser.add_argument("--screenshot", type=Path, help="save an image of the demo")
    args = parser.parse_args()
    app = QtWidgets.QApplication(sys.argv[:1])
    app.setFont(QtGui.QFont("Microsoft YaHei", 10))
    demo = Demo()
    demo.show()
    if not args.benchmark and not args.screenshot:
        return app.exec_()

    scenario_start = time.perf_counter()
    scenarios = (
        (0.0, [0.0] * 8),
        (0.45, [0.2, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]),
        (0.85, [7.5, 0.0, 0.0, 3.0, 0.0, 0.0, 1.5, 0.0]),
        (1.55, [0.0] * 8),
        (2.0, [16.0, 0.0, 0.0, 9.0, 0.0, 0.0, 5.0, 0.0]),
        (2.55, [0.0] * 8),
    )
    next_scenario = 0

    def advance():
        nonlocal next_scenario
        elapsed = time.perf_counter() - scenario_start
        while next_scenario < len(scenarios) and elapsed >= scenarios[next_scenario][0]:
            demo.set_zones(scenarios[next_scenario][1])
            next_scenario += 1
        if args.screenshot and elapsed >= 1.28 and not getattr(demo, "_saved", False):
            destination = args.screenshot.resolve()
            destination.parent.mkdir(parents=True, exist_ok=True)
            screenshot = demo.grab()
            if not screenshot.save(str(destination)):
                raise RuntimeError(f"Could not save screenshot: {destination}")
            demo._saved = True
            print(f"Screenshot: {destination}")
            if not args.benchmark:
                app.quit()
        if args.benchmark and elapsed >= 3.1:
            app.quit()

    timer = QtCore.QTimer(demo)
    timer.setTimerType(QtCore.Qt.PreciseTimer)
    timer.setInterval(5)
    timer.timeout.connect(advance)
    timer.start()
    app.exec_()
    if args.benchmark:
        starts = demo.view.paint_started
        intervals = [(b - a) * 1000.0 for a, b in zip(starts, starts[1:])]
        durations = demo.view.paint_duration_ms
        print(
            f"Completed paints: {len(starts)} in 3.1 s; "
            f"interval median/p95: {statistics.median(intervals):.1f}/{percentile(intervals, 0.95):.1f} ms; "
            f"paint median/p95: {statistics.median(durations):.1f}/{percentile(durations, 0.95):.1f} ms"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

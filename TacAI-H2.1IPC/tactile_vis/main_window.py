"""
Main application window

Provides the full UI for the tactile sensor 3D force visualization platform,
including serial connection management, real-time 3D rendering, data recording,
and coordinate mapping.

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

import csv
import os
import time
from datetime import datetime

import numpy as np
import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph.opengl as gl

from .config import COLORS, ROWS, COLS, get_color_for_force
from .components import ModernArrowItem, CardWidget, StatusIndicator
from .serial_reader import SerialReader


class ForceVisualizer(QtWidgets.QMainWindow):
    """Main window for tactile sensor 3D force visualization platform"""

    def __init__(self):
        super().__init__()
        self.setWindowTitle("Tactile Sensor 3D Force Visualization")
        self.resize(1600, 900)

        self.reader = None
        self.scale = 1
        self.normalize_mode = False
        self.show_grid = True
        self.show_axis = True
        self.show_labels = False
        self.show_indices = False
        self.color_mapping = True

        self.swap_xy = False
        self.invert_x = False
        self.invert_y = False
        self.invert_z = False
        self.array_rotation = 0

        self.frame_count = 0
        self.last_fps_update = time.time()
        self.current_fps = 0

        self.max_force = 0
        self.avg_force = 0
        self.total_force = np.zeros(3)
        self.peak_idx = -1

        self.recording = False
        self.recorded_frames = []
        self.recorded_timestamps = []
        self.record_start_time = 0

        self._init_ui()
        self._init_3d_view()

        self.fps_timer = QtCore.QTimer()
        self.fps_timer.timeout.connect(self._update_fps)
        self.fps_timer.start(500)

        zero_frame = np.zeros((ROWS, COLS, 3), dtype=float)
        self.update_frame(zero_frame)

    def _init_ui(self):
        self.setStyleSheet(f"""
            QMainWindow {{
                background-color: {COLORS['bg']};
            }}
            QWidget {{
                color: {COLORS['text']};
                font-family: 'Segoe UI', 'Microsoft YaHei', sans-serif;
                font-size: 10pt;
            }}
            QPushButton {{
                background-color: {COLORS['panel_bg']};
                border: 1px solid {COLORS['grid']};
                border-radius: 4px;
                padding: 6px 12px;
                min-height: 24px;
            }}
            QPushButton:hover {{
                background-color: #2a2a2a;
            }}
            QPushButton:pressed {{
                background-color: #333333;
            }}
            QComboBox {{
                background-color: {COLORS['panel_bg']};
                border: 1px solid {COLORS['grid']};
                border-radius: 4px;
                padding: 4px 8px;
                min-height: 24px;
            }}
            QComboBox::drop-down {{
                border: none;
            }}
            QSlider::groove:horizontal {{
                height: 6px;
                background: {COLORS['grid']};
                border-radius: 3px;
            }}
            QSlider::handle:horizontal {{
                width: 16px;
                height: 16px;
                background: {COLORS['accent']};
                border-radius: 8px;
                margin: -5px 0;
            }}
            QCheckBox {{
                spacing: 6px;
            }}
            QTableWidget {{
                background-color: {COLORS['panel_bg']};
                border: none;
                gridline-color: {COLORS['grid']};
                selection-background-color: {COLORS['accent']};
            }}
            QHeaderView::section {{
                background-color: {COLORS['bg']};
                color: {COLORS['text_secondary']};
                padding: 6px;
                border: none;
                border-bottom: 1px solid {COLORS['grid']};
                font-weight: bold;
            }}
            QTableWidget::item {{
                padding: 6px;
            }}
            QTextEdit {{
                background-color: {COLORS['panel_bg']};
                border: none;
                font-family: Consolas, 'Courier New', monospace;
                font-size: 9pt;
            }}
        """)

        main_widget = QtWidgets.QWidget()
        self.setCentralWidget(main_widget)

        main_layout = QtWidgets.QVBoxLayout(main_widget)
        main_layout.setContentsMargins(12, 12, 12, 12)
        main_layout.setSpacing(12)

        self._build_top_bar(main_layout)
        self._build_content_area(main_layout)
        self._build_bottom_panel(main_layout)

        self.refresh_ports()
        self._connect_signals()

    def _build_top_bar(self, parent_layout):
        top_bar = QtWidgets.QHBoxLayout()
        top_bar.setSpacing(16)

        title_label = QtWidgets.QLabel("Tactile Sensor 3D Force Visualization")
        title_label.setStyleSheet(f"""
            font-size: 12pt;
            font-weight: bold;
            color: {COLORS['accent']};
        """)
        top_bar.addWidget(title_label)
        top_bar.addStretch()

        self.connection_indicator = StatusIndicator("Disconnected", "disconnected")
        top_bar.addWidget(self.connection_indicator)

        self.port_label = QtWidgets.QLabel("")
        self.port_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        top_bar.addWidget(self.port_label)

        self.fps_label = QtWidgets.QLabel("FPS: --")
        self.fps_label.setStyleSheet(f"color: {COLORS['text_secondary']};")
        top_bar.addWidget(self.fps_label)

        parent_layout.addLayout(top_bar)

    def _build_content_area(self, parent_layout):
        content_layout = QtWidgets.QHBoxLayout()
        content_layout.setSpacing(12)

        left_panel = QtWidgets.QVBoxLayout()
        left_panel.setSpacing(12)
        left_panel.setContentsMargins(0, 0, 0, 0)

        left_cols = QtWidgets.QHBoxLayout()
        left_cols.setSpacing(10)

        left_col1 = QtWidgets.QVBoxLayout()
        left_col1.setSpacing(12)
        left_col2 = QtWidgets.QVBoxLayout()
        left_col2.setSpacing(12)

        self._build_serial_card(left_col1)
        self._build_coord_card(left_col1)
        self._build_data_card(left_col1)
        left_col1.addStretch()

        self._build_display_card(left_col2)
        self._build_view_card(left_col2)
        self._build_recorder_card(left_col2)
        left_col2.addStretch()

        left_cols.addLayout(left_col1)
        left_cols.addLayout(left_col2)
        left_panel.addLayout(left_cols)

        content_layout.addLayout(left_panel, 0)

        self.view = gl.GLViewWidget()
        content_layout.addWidget(self.view, 1)

        parent_layout.addLayout(content_layout, 1)

    def _build_serial_card(self, parent):
        card = CardWidget("Device Connection")
        self.serial_card = card

        port_layout = QtWidgets.QHBoxLayout()
        self.port_box = QtWidgets.QComboBox()
        self.refresh_btn = QtWidgets.QPushButton("Refresh")
        port_layout.addWidget(self.port_box, 1)
        port_layout.addWidget(self.refresh_btn)
        card.add_layout(port_layout)

        baud_layout = QtWidgets.QHBoxLayout()
        baud_layout.addWidget(QtWidgets.QLabel("Baud rate:"))
        self.baud_box = QtWidgets.QComboBox()
        self.baud_box.addItems(["115200", "230400", "460800", "921600"])
        self.baud_box.setCurrentText("115200")
        baud_layout.addWidget(self.baud_box, 1)
        card.add_layout(baud_layout)

        btn_layout = QtWidgets.QHBoxLayout()
        self.connect_btn = QtWidgets.QPushButton("Connect")
        self.connect_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['success']}; "
            "color: #000000; font-weight: bold; }"
        )
        self.disconnect_btn = QtWidgets.QPushButton("Disconnect")
        btn_layout.addWidget(self.connect_btn)
        btn_layout.addWidget(self.disconnect_btn)
        card.add_layout(btn_layout)

        parent.addWidget(card)

    def _build_display_card(self, parent):
        card = CardWidget("Display Settings")
        self.display_card = card

        scale_layout = QtWidgets.QVBoxLayout()
        scale_label_layout = QtWidgets.QHBoxLayout()
        scale_label_layout.addWidget(QtWidgets.QLabel("Arrow Scale"))
        self.scale_label = QtWidgets.QLabel("0.80x")
        self.scale_label.setStyleSheet(
            f"color: {COLORS['accent']}; font-weight: bold;"
        )
        scale_label_layout.addStretch()
        scale_label_layout.addWidget(self.scale_label)
        scale_layout.addLayout(scale_label_layout)

        self.scale_slider = QtWidgets.QSlider(QtCore.Qt.Horizontal)
        self.scale_slider.setMinimum(1)
        self.scale_slider.setMaximum(300)
        self.scale_slider.setValue(80)
        scale_layout.addWidget(self.scale_slider)
        card.add_layout(scale_layout)

        self.normalize_check = QtWidgets.QCheckBox("Normalize Display")
        card.add_widget(self.normalize_check)

        self.color_map_check = QtWidgets.QCheckBox("Color Mapping")
        self.color_map_check.setChecked(True)
        card.add_widget(self.color_map_check)

        self.grid_check = QtWidgets.QCheckBox("Show Grid")
        self.grid_check.setChecked(True)
        card.add_widget(self.grid_check)

        self.axis_check = QtWidgets.QCheckBox("Show Axis")
        self.axis_check.setChecked(True)
        card.add_widget(self.axis_check)

        self.label_check = QtWidgets.QCheckBox("Show Point Labels")
        card.add_widget(self.label_check)

        parent.addWidget(card)

    def _build_coord_card(self, parent):
        card = CardWidget("Coordinate Mapping")
        self.coord_card = card

        self.swap_xy_check = QtWidgets.QCheckBox("Swap Fx/Fy")
        card.add_widget(self.swap_xy_check)
        self.invert_x_check = QtWidgets.QCheckBox("Invert Fx")
        card.add_widget(self.invert_x_check)
        self.invert_y_check = QtWidgets.QCheckBox("Invert Fy")
        card.add_widget(self.invert_y_check)
        self.invert_z_check = QtWidgets.QCheckBox("Invert Fz")
        card.add_widget(self.invert_z_check)

        rot_layout = QtWidgets.QHBoxLayout()
        rot_layout.addWidget(QtWidgets.QLabel("Array rotation:"))
        self.rot_combo = QtWidgets.QComboBox()
        self.rot_combo.addItems(["0 deg", "90 deg", "180 deg", "270 deg"])
        rot_layout.addWidget(self.rot_combo)
        card.add_layout(rot_layout)

        parent.addWidget(card)

    def _build_view_card(self, parent):
        card = CardWidget("Viewpoint")
        self.view_card = card

        row1 = QtWidgets.QHBoxLayout()
        self.top_view_btn = QtWidgets.QPushButton("Top")
        self.front_view_btn = QtWidgets.QPushButton("Front")
        self.side_view_btn = QtWidgets.QPushButton("Side")
        row1.addWidget(self.top_view_btn)
        row1.addWidget(self.front_view_btn)
        row1.addWidget(self.side_view_btn)
        card.add_layout(row1)

        row2 = QtWidgets.QHBoxLayout()
        self.iso_view_btn = QtWidgets.QPushButton("Isometric")
        self.reset_view_btn = QtWidgets.QPushButton("Reset")
        row2.addWidget(self.iso_view_btn)
        row2.addWidget(self.reset_view_btn)
        card.add_layout(row2)

        parent.addWidget(card)

    def _build_data_card(self, parent):
        card = CardWidget("Live Data")
        self.data_card = card

        self.max_force_label = QtWidgets.QLabel("Max force: -- N")
        card.add_widget(self.max_force_label)
        self.avg_force_label = QtWidgets.QLabel("Avg force: -- N")
        card.add_widget(self.avg_force_label)
        self.total_force_label = QtWidgets.QLabel("Total: Fx --  Fy --  Fz --")
        card.add_widget(self.total_force_label)
        self.peak_label = QtWidgets.QLabel("Peak point: --")
        card.add_widget(self.peak_label)

        parent.addWidget(card)

    def _build_recorder_card(self, parent):
        card = CardWidget("Data Recording")
        self.recorder_card = card

        self.record_status_label = QtWidgets.QLabel("Idle | 0 frames")
        card.add_widget(self.record_status_label)

        btn_layout = QtWidgets.QHBoxLayout()
        self.record_start_btn = QtWidgets.QPushButton("Start")
        self.record_start_btn.setStyleSheet(
            f"QPushButton {{ background-color: {COLORS['success']}; "
            "color: #000000; font-weight: bold; }"
        )
        self.record_stop_btn = QtWidgets.QPushButton("Stop")
        self.record_stop_btn.setEnabled(False)
        self.record_save_btn = QtWidgets.QPushButton("Save CSV")
        self.record_save_btn.setEnabled(False)
        btn_layout.addWidget(self.record_start_btn)
        btn_layout.addWidget(self.record_stop_btn)
        btn_layout.addWidget(self.record_save_btn)
        card.add_layout(btn_layout)

        parent.addWidget(card)

    def _build_bottom_panel(self, parent_layout):
        bottom_splitter = QtWidgets.QSplitter(QtCore.Qt.Horizontal)

        self.value_table = QtWidgets.QTableWidget(ROWS * COLS, 5)
        self.value_table.setHorizontalHeaderLabels(
            ["Point", "Fx", "Fy", "Fz", "|F|"]
        )
        self.value_table.verticalHeader().setVisible(False)
        self.value_table.setMaximumHeight(200)

        for idx in range(ROWS * COLS):
            self.value_table.setItem(
                idx, 0, QtWidgets.QTableWidgetItem(f"P{idx:02d}")
            )
            for col in range(1, 5):
                item = QtWidgets.QTableWidgetItem("0.00")
                item.setTextAlignment(
                    QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter
                )
                self.value_table.setItem(idx, col, item)

        bottom_splitter.addWidget(self.value_table)

        self.status_text = QtWidgets.QTextEdit()
        self.status_text.setReadOnly(True)
        self.status_text.setMaximumHeight(200)
        bottom_splitter.addWidget(self.status_text)

        bottom_splitter.setSizes([400, 400])
        parent_layout.addWidget(bottom_splitter)

    def _connect_signals(self):
        self.refresh_btn.clicked.connect(self.refresh_ports)
        self.connect_btn.clicked.connect(self.connect_serial)
        self.disconnect_btn.clicked.connect(self.disconnect_serial)
        self.scale_slider.valueChanged.connect(self._on_scale_changed)
        self.normalize_check.toggled.connect(self._on_normalize_toggled)
        self.color_map_check.toggled.connect(self._on_color_map_toggled)
        self.grid_check.toggled.connect(self._on_grid_toggled)
        self.axis_check.toggled.connect(self._on_axis_toggled)
        self.label_check.toggled.connect(self._on_label_toggled)
        self.swap_xy_check.toggled.connect(self._on_swap_xy_toggled)
        self.invert_x_check.toggled.connect(self._on_invert_x_toggled)
        self.invert_y_check.toggled.connect(self._on_invert_y_toggled)
        self.invert_z_check.toggled.connect(self._on_invert_z_toggled)
        self.rot_combo.currentIndexChanged.connect(self._on_rotation_changed)
        self.top_view_btn.clicked.connect(lambda: self._set_view("top"))
        self.front_view_btn.clicked.connect(lambda: self._set_view("front"))
        self.side_view_btn.clicked.connect(lambda: self._set_view("side"))
        self.iso_view_btn.clicked.connect(lambda: self._set_view("iso"))
        self.reset_view_btn.clicked.connect(lambda: self._set_view("reset"))
        self.record_start_btn.clicked.connect(self.start_recording)
        self.record_stop_btn.clicked.connect(self.stop_recording)
        self.record_save_btn.clicked.connect(self.save_recording)

    def _init_3d_view(self):
        self.view.opts['distance'] = 6
        self.view.setBackgroundColor((20, 20, 20))

        self.grid = gl.GLGridItem()
        self.grid.scale(1, 1, 1)
        self.grid.setSize(x=8, y=8)
        self.grid.setColor((40, 40, 40, 100))
        self.view.addItem(self.grid)

        self.axis = gl.GLAxisItem()
        self.axis.setSize(2, 2, 2)
        self.view.addItem(self.axis)

        plane_md = gl.MeshData(
            vertexes=np.array(
                [[-1.5, -2, 0], [1.5, -2, 0], [1.5, 2, 0], [-1.5, 2, 0]],
                dtype=float,
            ),
            faces=np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32),
        )
        self.plane = gl.GLMeshItem(
            meshdata=plane_md,
            color=(0.1, 0.1, 0.15, 0.3),
            smooth=True,
            drawEdges=False,
            drawFaces=True,
        )
        self.view.addItem(self.plane)

        self.base_positions = []
        x_coords = np.linspace(-1, 1, COLS)
        y_coords = np.linspace(1.5, -1.5, ROWS)

        for i in range(ROWS):
            for j in range(COLS):
                pos = np.array([x_coords[j], y_coords[i], 0.01], dtype=float)
                self.base_positions.append(pos)

        self.base_positions = np.array(self.base_positions)

        self.arrows = []
        for _ in range(ROWS * COLS):
            arrow = ModernArrowItem(self.view, color=(0.0, 0.7, 1.0, 1.0))
            self.arrows.append(arrow)

        self.labels = []

    def refresh_ports(self):
        self.port_box.clear()
        ports = serial.tools.list_ports.comports()
        for p in ports:
            self.port_box.addItem(p.device)

    def _on_scale_changed(self, value):
        self.scale = value / 100.0
        self.scale_label.setText(f"{self.scale:.2f}x")

    def _on_normalize_toggled(self, checked):
        self.normalize_mode = checked

    def _on_color_map_toggled(self, checked):
        self.color_mapping = checked

    def _on_grid_toggled(self, checked):
        self.show_grid = checked
        self.grid.setVisible(checked)

    def _on_axis_toggled(self, checked):
        self.show_axis = checked
        self.axis.setVisible(checked)

    def _on_label_toggled(self, checked):
        self.show_indices = checked

    def _on_swap_xy_toggled(self, checked):
        self.swap_xy = checked

    def _on_invert_x_toggled(self, checked):
        self.invert_x = checked

    def _on_invert_y_toggled(self, checked):
        self.invert_y = checked

    def _on_invert_z_toggled(self, checked):
        self.invert_z = checked

    def _on_rotation_changed(self, index):
        self.array_rotation = index * 90

    def _set_view(self, view_type):
        if view_type == "top":
            self.view.opts['elevation'] = 90
            self.view.opts['azimuth'] = -90
        elif view_type == "front":
            self.view.opts['elevation'] = 0
            self.view.opts['azimuth'] = -90
        elif view_type == "side":
            self.view.opts['elevation'] = 0
            self.view.opts['azimuth'] = 0
        elif view_type == "iso":
            self.view.opts['elevation'] = 30
            self.view.opts['azimuth'] = -45
        elif view_type == "reset":
            self.view.opts['elevation'] = 30
            self.view.opts['azimuth'] = -45
            self.view.opts['distance'] = 6

    def log(self, msg, level="info"):
        colors = {
            'info': COLORS['text'],
            'warning': COLORS['warning'],
            'error': COLORS['danger'],
            'success': COLORS['success'],
        }
        color = colors.get(level, colors['info'])
        timestamp = time.strftime("%H:%M:%S")
        self.status_text.append(
            f'<span style="color:{color}">[{timestamp}] {msg}</span>'
        )

    def connect_serial(self):
        self.disconnect_serial()

        port = self.port_box.currentText().strip()
        if not port:
            self.log("No serial port selected", "warning")
            return

        baudrate = int(self.baud_box.currentText())

        self.reader = SerialReader(port, baudrate)
        self.reader.frame_received.connect(self.update_frame)
        self.reader.status_signal.connect(self.log)
        self.reader.start()

        self.connection_indicator.set_state("connected")
        self.connection_indicator.label.setText("Connected")
        self.port_label.setText(f"{port} @ {baudrate}")
        self.log(f"Connecting {port}...", "info")

    def disconnect_serial(self):
        if self.reader is not None:
            self.reader.stop()
            self.reader.wait()
            self.reader = None
            self.connection_indicator.set_state("disconnected")
            self.connection_indicator.label.setText("Disconnected")
            self.port_label.setText("")
            self.log("Disconnected", "info")

    def update_frame(self, frame):
        self.frame_count += 1

        if self.recording:
            self.recorded_frames.append(frame.copy())
            self.recorded_timestamps.append(time.time())
            count = len(self.recorded_frames)
            elapsed = time.time() - self.record_start_time
            self.record_status_label.setText(
                f"Recording | {count} frames | {elapsed:.1f}s"
            )

        force_magnitudes = []
        forces = []
        for i in range(ROWS):
            for j in range(COLS):
                fx, fy, fz = frame[i, j]

                if self.swap_xy:
                    fx, fy = fy, fx
                if self.invert_x:
                    fx = -fx
                if self.invert_y:
                    fy = -fy
                if self.invert_z:
                    fz = -fz

                vec = np.array([fx, fy, fz], dtype=float)
                forces.append(vec)
                force_magnitudes.append(np.linalg.norm(vec))

        force_magnitudes = np.array(force_magnitudes)
        forces = np.array(forces)

        self.max_force = np.max(force_magnitudes)
        self.avg_force = np.mean(force_magnitudes)
        self.total_force = np.sum(forces, axis=0)
        self.peak_idx = np.argmax(force_magnitudes)

        self.max_force_label.setText(f"Max force: {self.max_force:.2f} N")
        self.avg_force_label.setText(f"Avg force: {self.avg_force:.2f} N")
        self.total_force_label.setText(
            f"Total: Fx {self.total_force[0]:.2f}  "
            f"Fy {self.total_force[1]:.2f}  "
            f"Fz {self.total_force[2]:.2f}"
        )
        self.peak_label.setText(f"Peak point: P{self.peak_idx:02d}")

        max_mag = max(self.max_force, 1e-6)
        max_display = 5.0

        idx = 0
        for i in range(ROWS):
            for j in range(COLS):
                fx, fy, fz = frame[i, j]

                if self.swap_xy:
                    fx, fy = fy, fx
                if self.invert_x:
                    fx = -fx
                if self.invert_y:
                    fy = -fy
                if self.invert_z:
                    fz = -fz

                vec = np.array([fx, fy, fz], dtype=float)
                mag = np.linalg.norm(vec)

                if self.normalize_mode and mag > 1e-6:
                    vec = vec / mag * min(max_mag, 2.0)

                vec_scaled = vec * self.scale

                base = self.base_positions[idx]

                if self.color_mapping:
                    color = get_color_for_force(mag, max_display)
                else:
                    color = np.array([0.0, 0.7, 1.0, 1.0])

                if idx == self.peak_idx and self.max_force > 0.1:
                    color = np.array([1.0, 0.84, 0.0, 1.0])

                self.arrows[idx].set_data(base, vec_scaled, color=color)

                self.value_table.item(idx, 1).setText(f"{fx:.2f}")
                self.value_table.item(idx, 2).setText(f"{fy:.2f}")
                self.value_table.item(idx, 3).setText(f"{fz:.2f}")
                self.value_table.item(idx, 4).setText(f"{mag:.2f}")

                for col in range(5):
                    item = self.value_table.item(idx, col)
                    if idx == self.peak_idx and self.max_force > 0.1:
                        item.setBackground(QtGui.QColor(80, 70, 0))
                    else:
                        item.setBackground(QtGui.QColor(30, 30, 30))

                idx += 1

    def _update_fps(self):
        now = time.time()
        elapsed = now - self.last_fps_update
        if elapsed >= 0.5:
            self.current_fps = self.frame_count / elapsed
            self.frame_count = 0
            self.last_fps_update = now

            color = COLORS['success']
            if self.current_fps < 20:
                color = COLORS['warning']
            if self.current_fps < 10:
                color = COLORS['danger']

            self.fps_label.setText(f"FPS: {self.current_fps:.1f}")
            self.fps_label.setStyleSheet(f"color: {color};")

    def start_recording(self):
        self.recording = True
        self.recorded_frames = []
        self.recorded_timestamps = []
        self.record_start_time = time.time()
        self.record_status_label.setText("Recording | 0 frames")
        self.record_status_label.setStyleSheet(
            f"color: {COLORS['success']}; font-weight: bold;"
        )
        self.record_start_btn.setEnabled(False)
        self.record_stop_btn.setEnabled(True)
        self.record_save_btn.setEnabled(False)

        port = self.port_box.currentText().strip()
        baudrate = self.baud_box.currentText()
        self.log(f"Recording started - port: {port} @ {baudrate}", "info")

    def stop_recording(self):
        self.recording = False
        count = len(self.recorded_frames)
        elapsed = time.time() - self.record_start_time
        self.record_status_label.setText(
            f"Stopped | {count} frames | {elapsed:.1f}s"
        )
        self.record_status_label.setStyleSheet(
            f"color: {COLORS['warning']};"
        )
        self.record_start_btn.setEnabled(True)
        self.record_stop_btn.setEnabled(False)
        self.record_save_btn.setEnabled(count > 0)
        self.log(
            f"Recording stopped - {count} frames, {elapsed:.1f}s", "info"
        )

    def save_recording(self):
        if not self.recorded_frames:
            self.log("No data to save", "warning")
            return

        default_name = (
            f"force_data_{datetime.now().strftime('%Y%m%d_%H%M%S')}.csv"
        )
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Data File", default_name,
            "CSV Files (*.csv);;All Files (*)",
        )

        if not filepath:
            return

        try:
            with open(filepath, 'w', newline='', encoding='utf-8-sig') as f:
                writer = csv.writer(f)

                header = ['Frame', 'Timestamp']
                for idx in range(ROWS * COLS):
                    row = idx // COLS
                    col = idx % COLS
                    header.extend([
                        f"P{idx:02d}_Fx({row},{col})",
                        f"P{idx:02d}_Fy({row},{col})",
                        f"P{idx:02d}_Fz({row},{col})",
                    ])
                writer.writerow(header)

                for frame_idx, (frame, ts) in enumerate(
                    zip(self.recorded_frames, self.recorded_timestamps)
                ):
                    row_data = [frame_idx, f"{ts - self.record_start_time:.3f}"]
                    for idx in range(ROWS * COLS):
                        row = idx // COLS
                        col = idx % COLS
                        fx, fy, fz = frame[row, col]
                        row_data.extend(
                            [f"{fx:.4f}", f"{fy:.4f}", f"{fz:.4f}"]
                        )
                    writer.writerow(row_data)

            count = len(self.recorded_frames)
            size_kb = os.path.getsize(filepath) / 1024
            self.log(
                f"Data saved: {os.path.basename(filepath)} "
                f"({count} frames, {size_kb:.1f} KB)",
                "success",
            )

        except Exception as e:
            self.log(f"Save failed: {e}", "error")

    def closeEvent(self, event):
        self.recording = False
        self.disconnect_serial()
        super().closeEvent(event)
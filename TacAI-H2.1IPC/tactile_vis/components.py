"""
UI components and 3D arrow visualization

Provides the ModernArrowItem class for force visualization, along with
reusable UI widgets: CardWidget and StatusIndicator.

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

import numpy as np
from PyQt5 import QtCore, QtWidgets, QtGui
import pyqtgraph.opengl as gl

from .config import COLORS
from .mesh_utils import create_cylinder_mesh, create_cone_mesh


class ModernArrowItem:
    """3D force arrow composed of a cylinder shaft and a cone tip

    Each arrow has a permanently visible base marker (small sphere),
    so the sensor matrix layout remains visible even when force is zero.
    Arrow colors and directions update in real-time based on force data.
    """

    def __init__(self, view, color=(1, 0, 0, 1)):
        self.view = view
        self.color = np.array(color, dtype=float)
        self.current_color = self.color.copy()

        base_md = gl.MeshData.sphere(rows=8, cols=16, radius=0.06)
        self.base_marker = gl.GLMeshItem(
            meshdata=base_md,
            smooth=True,
            drawFaces=True,
            drawEdges=False,
            shader='shaded',
            color=(0.3, 0.3, 0.3, 0.8)
        )
        self.view.addItem(self.base_marker)

        self.shaft_radius = 0.03
        self.shaft = gl.GLMeshItem(
            meshdata=create_cylinder_mesh(self.shaft_radius, 1.0),
            smooth=True,
            drawFaces=True,
            drawEdges=False,
            shader='shaded',
            color=self.color
        )
        self.view.addItem(self.shaft)

        self.tip_radius = 0.08
        self.tip_height = 0.2
        self.tip = gl.GLMeshItem(
            meshdata=create_cone_mesh(self.tip_radius, self.tip_height),
            smooth=True,
            drawFaces=True,
            drawEdges=False,
            shader='shaded',
            color=self.color
        )
        self.view.addItem(self.tip)

        self.last_vec = np.zeros(3, dtype=float)
        self.target_vec = np.zeros(3, dtype=float)
        self.smoothing_factor = 0.3

    def set_data(self, base, vec, color=None):
        """Update arrow position, direction, and appearance

        Args:
            base: 3D coordinates of the arrow origin
            vec: Force vector (magnitude determines arrow length)
            color: Optional RGBA color override
        """
        base = np.array(base, dtype=float)
        vec = np.array(vec, dtype=float)

        self.base_marker.resetTransform()
        self.base_marker.translate(base[0], base[1], base[2])

        self.target_vec = vec
        self.last_vec = self.last_vec * (1 - self.smoothing_factor) + vec * self.smoothing_factor
        vec = self.last_vec

        vec_len = np.linalg.norm(vec)
        if vec_len < 0.1:
            self.shaft.hide()
            self.tip.hide()
            self.base_marker.setColor((0.3, 0.3, 0.3, 0.8))
            return

        self.shaft.show()
        self.tip.show()

        if color is not None:
            self.current_color = np.array(color, dtype=float)
            self.shaft.setColor(self.current_color)
            self.tip.setColor(self.current_color)
            base_color = self.current_color.copy()
            base_color[3] = 0.6
            self.base_marker.setColor(base_color)

        direction = vec / vec_len
        up = np.array([0, 0, 1], dtype=float)

        if np.abs(np.dot(direction, up)) > 0.999:
            rot = QtGui.QMatrix4x4()
        else:
            axis = np.cross(up, direction)
            axis = axis / np.linalg.norm(axis)
            angle = np.arccos(np.dot(up, direction))
            rot = QtGui.QMatrix4x4()
            rot.rotate(np.degrees(angle), axis[0], axis[1], axis[2])

        shaft_len = max(vec_len - self.tip_height, 0.001)
        self.shaft.resetTransform()
        self.shaft.setTransform(rot)
        scale_y = shaft_len if shaft_len > 0 else 0.001
        self.shaft.scale(self.shaft_radius / 0.03, self.shaft_radius / 0.03, scale_y)
        self.shaft.translate(base[0], base[1], base[2])

        tip_base = base + direction * shaft_len
        self.tip.resetTransform()
        self.tip.setTransform(rot)
        self.tip.scale(self.tip_radius / 0.08, self.tip_radius / 0.08, 1.0)
        self.tip.translate(tip_base[0], tip_base[1], tip_base[2])


class CardWidget(QtWidgets.QFrame):
    """Dark-themed card container with an optional title bar"""

    def __init__(self, title=""):
        super().__init__()
        self.setFrameShape(QtWidgets.QFrame.StyledPanel)
        self.setStyleSheet(f"""
            CardWidget {{
                background-color: {COLORS['panel_bg']};
                border-radius: 8px;
                padding: 8px;
            }}
        """)

        layout = QtWidgets.QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(10)

        if title:
            title_label = QtWidgets.QLabel(title)
            title_label.setStyleSheet(f"""
                font-size: 11pt;
                font-weight: bold;
                color: {COLORS['accent']};
                padding-bottom: 4px;
                border-bottom: 1px solid {COLORS['grid']};
            """)
            layout.addWidget(title_label)

        self.content_layout = QtWidgets.QVBoxLayout()
        self.content_layout.setSpacing(8)
        layout.addLayout(self.content_layout)

    def add_widget(self, widget):
        self.content_layout.addWidget(widget)

    def add_layout(self, layout):
        self.content_layout.addLayout(layout)


class StatusIndicator(QtWidgets.QWidget):
    """Compact status indicator with a colored dot and label"""

    def __init__(self, text="", state="disconnected"):
        super().__init__()
        layout = QtWidgets.QHBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)

        self.dot = QtWidgets.QLabel()
        self.dot.setFixedSize(10, 10)
        layout.addWidget(self.dot)

        self.label = QtWidgets.QLabel(text)
        self.label.setStyleSheet(f"color: {COLORS['text']};")
        layout.addWidget(self.label)

        self.set_state(state)

    def set_state(self, state):
        colors = {
            'disconnected': COLORS['text_secondary'],
            'connected': COLORS['success'],
            'error': COLORS['danger'],
            'warning': COLORS['warning']
        }
        color = colors.get(state, colors['disconnected'])
        self.dot.setStyleSheet(f"""
            background-color: {color};
            border-radius: 5px;
        """)
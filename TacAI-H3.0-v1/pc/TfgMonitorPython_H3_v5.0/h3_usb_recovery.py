"""Standalone H3 USB reconnect recovery launcher."""

from __future__ import annotations

import sys

import serial.tools.list_ports
from PyQt5 import QtCore, QtWidgets

from firmware_updater import FirmwareUpgradeDialog


class RecoveryLauncher(QtWidgets.QDialog):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("TacAI H3 USB 拔插恢复工具 v1.0")
        self.resize(560, 210)
        self.setStyleSheet(
            "QDialog{background:#0b1d2b;color:#e8f3ff;}"
            "QLabel{color:#e8f3ff;}"
            "QComboBox,QPushButton{background:#12364a;color:#eefbff;"
            "border:1px solid #3b7894;border-radius:5px;padding:7px;}"
        )
        layout = QtWidgets.QVBoxLayout(self)
        message = QtWidgets.QLabel(
            "用于应用卡死、无法响应进入命令的设备。选择当前能烧录的 CH342 串口，"
            "进入升级窗口并开始后，拔掉再插回整根 USB；工具会自动重连并捕获上电 Bootloader。"
        )
        message.setWordWrap(True)
        layout.addWidget(message)

        row = QtWidgets.QHBoxLayout()
        row.addWidget(QtWidgets.QLabel("目标串口"))
        self.port_combo = QtWidgets.QComboBox()
        row.addWidget(self.port_combo, 1)
        refresh = QtWidgets.QPushButton("刷新串口")
        refresh.clicked.connect(self.refresh_ports)
        row.addWidget(refresh)
        layout.addLayout(row)

        self.open_button = QtWidgets.QPushButton("进入恢复升级")
        self.open_button.clicked.connect(self.open_recovery)
        layout.addWidget(self.open_button)
        self.refresh_ports()

    def refresh_ports(self):
        previous = self.port_combo.currentData()
        self.port_combo.clear()
        for info in serial.tools.list_ports.comports():
            self.port_combo.addItem(f"{info.device} · {info.description}", info.device)
        if previous:
            index = self.port_combo.findData(previous)
            if index >= 0:
                self.port_combo.setCurrentIndex(index)
        self.open_button.setEnabled(self.port_combo.count() > 0)

    def open_recovery(self):
        port = self.port_combo.currentData()
        if not port:
            QtWidgets.QMessageBox.warning(self, "未选择串口", "请连接设备并刷新串口。")
            return
        FirmwareUpgradeDialog(port, self).exec_()


def main() -> int:
    QtCore.QCoreApplication.setApplicationName("TacAI H3 USB Recovery")
    app = QtWidgets.QApplication(sys.argv)
    window = RecoveryLauncher()
    if "--self-test" in sys.argv:
        window.refresh_ports()
        window.close()
        return 0
    window.show()
    return app.exec_()


if __name__ == "__main__":
    raise SystemExit(main())

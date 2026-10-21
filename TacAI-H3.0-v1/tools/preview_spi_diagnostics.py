"""Capture only connection controls using simulated failures; never open USB."""
import os
from pathlib import Path
import sys

os.environ["QT_QPA_PLATFORM"] = "windows"
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "pc/TfgMonitorPython_H3_v5.0"))
from PyQt5 import QtCore, QtWidgets
import main

app = QtWidgets.QApplication([])
window = main.Monitor()
window.setAttribute(QtCore.Qt.WA_DontShowOnScreen, True)
window.unified_panel.hide()
window.transport_box.setCurrentIndex(1)
output = ROOT / "release/TacAI_H3_v5_3_4_SPI_Diagnostics_20261015/验证记录"
output.mkdir(parents=True, exist_ok=True)
window.show()
for width in (1500, 1100):
    window.resize(width, 950)
    window._on_status("SPI 采集失败：CH347 索引 0 无法打开。" + main.USB_OPEN_HELP, "error")
    window._on_reader_finished()
    app.processEvents()
    assert window.status.text() == "连接失败"
    panel = window.connection_error_label.parentWidget()
    assert panel.grab().save(str(output / f"usb_open_failure_{width}.png"))
    window._set_connection_error("")
    window._on_devices_detected([])
    app.processEvents()
    assert panel.grab().save(str(output / f"empty_detection_{width}.png"))
window.close()
print(output)

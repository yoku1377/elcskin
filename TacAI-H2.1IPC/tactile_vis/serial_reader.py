"""
Serial port data reader thread

Connects to the tactile sensor via serial port, parses comma-separated
force readings, and emits complete frames to the main window.

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""

import numpy as np
import serial
from PyQt5 import QtCore


class SerialReader(QtCore.QThread):
    """Background thread that reads and parses serial data frames"""

    frame_received = QtCore.pyqtSignal(object)
    status_signal = QtCore.pyqtSignal(str, str)

    def __init__(self, port, baudrate=115200, parent=None):
        from .config import FLOATS_PER_FRAME
        super().__init__(parent)
        self.port = port
        self.baudrate = baudrate
        self._running = True
        self.FLOATS_PER_FRAME = FLOATS_PER_FRAME
        self.last_frame_time = 0
        self.frame_count = 0

    def stop(self):
        self._running = False

    def run(self):
        try:
            ser = serial.Serial(self.port, self.baudrate, timeout=0.1)
            self.status_signal.emit(f"Serial opened: {self.port} @ {self.baudrate}", "info")
        except Exception as e:
            self.status_signal.emit(f"Serial open failed: {e}", "error")
            return

        while self._running:
            try:
                data = ser.readline()
                if not data:
                    continue

                try:
                    line = data.decode("utf-8", errors="ignore").strip()
                except Exception:
                    continue

                if not line:
                    continue

                parts = line.split(',')
                float_values = []

                for part in parts:
                    part = part.strip()
                    if part:
                        try:
                            val = float(part)
                            float_values.append(val)
                        except ValueError:
                            continue

                if len(float_values) >= self.FLOATS_PER_FRAME:
                    frame_vals = float_values[:self.FLOATS_PER_FRAME]

                    from .config import ROWS, COLS
                    frame = np.array(frame_vals, dtype=float).reshape(
                        (ROWS, COLS, 3)
                    ) * 9.8
                    self.frame_received.emit(frame)
                    self.frame_count += 1

            except Exception as e:
                self.status_signal.emit(f"Serial read error: {e}", "error")
                break

        try:
            ser.close()
        except Exception:
            pass

        self.status_signal.emit("Serial thread stopped", "info")
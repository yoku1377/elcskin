"""Gauge integration uses only fake serial ports and never launches the vendor app."""

import csv
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PyQt5 import QtWidgets

from calibration_acquisition import CalibrationAcquisitionDialog, GaugeReader


OBSERVED_DESCRIPTOR = b"130271400760500.00000.00500.030980.0"


def observed_metadata(unit_code="6"):
    """Actual 36-byte descriptor from the 2026-10-20 field capture."""
    raw = bytearray(OBSERVED_DESCRIPTOR)
    raw[10] = ord(unit_code)
    return bytes(raw)


class FakeGaugePort:
    def __init__(self, owner, events, clock):
        self.owner = owner
        self.events = list(events)
        self.clock = clock
        self.writes = []
        self.write_times = []
        self.closed = False
        self.is_open = owner.protocol == "ascii"
        self._port = owner.port if self.is_open else None
        self._dtr = True
        self._rts = True
        self.control_history = []
        self.open_snapshots = []
        self.fail_stage = None
        self.readline_calls = 0
        self.read_calls = 0
        self.read_sizes = []
        self.invalidations = []
        self.signal_order = []

    @property
    def in_waiting(self):
        if self.events and self.events[0][0] <= self.clock[0]:
            return len(self.events[0][1])
        return 0

    @property
    def port(self):
        return self._port

    @port.setter
    def port(self, value):
        if self.fail_stage == "port":
            raise OSError("cannot set port")
        self._port = value

    @property
    def dtr(self):
        return self._dtr

    @dtr.setter
    def dtr(self, value):
        if self.fail_stage == "dtr":
            raise OSError("cannot set dtr")
        self.control_history.append(("dtr", value, self.is_open))
        self._dtr = value

    @property
    def rts(self):
        return self._rts

    @rts.setter
    def rts(self, value):
        if self.fail_stage == "rts":
            raise OSError("cannot set rts")
        self.control_history.append(("rts", value, self.is_open))
        self._rts = value

    def open(self):
        self.open_snapshots.append((self._port, self._dtr, self._rts))
        if self.fail_stage == "open":
            raise OSError("cannot open port")
        self.is_open = True

    def write(self, data):
        self.writes.append(bytes(data))
        self.write_times.append(self.clock[0])
        if self.fail_stage == "write_25" and data == b"\x25":
            return 0
        return len(data)

    def _next(self):
        if not self.events:
            self.owner.stop()
            return b""
        now, raw = self.events.pop(0)
        self.clock[0] = now
        return raw

    def read(self, _size):
        self.read_calls += 1
        self.read_sizes.append(_size)
        return self._next()

    def readline(self):
        self.readline_calls += 1
        return self._next()

    def close(self):
        self.closed = True
        self.is_open = False


def run_fake_reader(reader, events, *, fail_stage=None):
    clock = [0.0]
    if reader.protocol == "zp500n":
        events = [(now + 0.05, raw) for now, raw in events]
    port = FakeGaugePort(reader, events, clock)
    port.fail_stage = fail_stage
    measurements, contexts, protocol_events, statuses = [], [], [], []
    reader.measurement.connect(lambda *args: (measurements.append(args), port.signal_order.append(("measurement", args))))
    reader.sample_context.connect(contexts.append)
    reader.protocol_event.connect(lambda event: (protocol_events.append(event), port.signal_order.append(("protocol", event))))
    reader.reference_invalidated.connect(lambda event: (port.invalidations.append(event), port.signal_order.append(("invalidated", event))))
    reader.status.connect(lambda *args: statuses.append(args))
    with patch("calibration_acquisition.serial.Serial", return_value=port) as opening, \
         patch("calibration_acquisition.time.monotonic", side_effect=lambda: clock[0]), \
         patch("calibration_acquisition.time.time", side_effect=lambda: 1000.0 + clock[0]), \
         patch("calibration_acquisition.time.sleep", side_effect=lambda seconds: clock.__setitem__(0, clock[0] + seconds)):
        reader.run()
    return port, measurements, contexts, protocol_events, statuses, opening.call_args


class ZP500NGaugeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def run_reader(self, reader, events, *, fail_stage=None):
        return run_fake_reader(reader, events, fail_stage=fail_stage)

    def test_existing_three_argument_ascii_reader_never_writes_to_instrument(self):
        reader = GaugeReader("COM13", 9600, 2.0)
        port, values, contexts, events, _status, opened = self.run_reader(
            reader, [(0.1, b"ST,NT,+1.250 N\r\n"), (0.2, b"OVERLOAD\n")],
        )
        self.assertEqual(port.writes, [])
        self.assertGreater(port.readline_calls, 0)
        self.assertEqual(port.read_calls, 0)
        self.assertEqual(values, [(2.5, "ST,NT,+1.250 N", 1000.1)])
        self.assertEqual(contexts[0]["original_value"], 1.25)
        self.assertEqual(contexts[0]["scale_to_n"], 2.0)
        self.assertFalse(events)
        self.assertEqual(opened.args, ("COM13", 9600))
        self.assertTrue(port.closed)

    def test_zp_handshake_and_no_newline_measurement_only_send_fa_and_25(self):
        reader = GaugeReader("COM13", 9600, 123.0, protocol="zp500n")
        raw = observed_metadata()
        port, values, contexts, events, statuses, opened = self.run_reader(reader, [
            (0.03, (raw * 3)[:16]), (0.06, (raw * 3)[16:]), (0.13, b""),
            (0.25, b"0016.1"), (0.32, b""),
        ])
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        self.assertGreaterEqual(port.write_times[0], 0.05)
        self.assertEqual(port.readline_calls, 0)
        self.assertEqual(opened.args, ())
        self.assertIsNone(opened.kwargs["port"])
        self.assertEqual(opened.kwargs["baudrate"], 2400)
        self.assertEqual(port.open_snapshots, [("COM13", False, False)])
        self.assertEqual(port.control_history, [("dtr", False, False), ("rts", False, False)])
        self.assertLessEqual(opened.kwargs["timeout"], 0.01)
        self.assertEqual(opened.kwargs["write_timeout"], 0.5)
        self.assertEqual((opened.kwargs["bytesize"], opened.kwargs["parity"], opened.kwargs["stopbits"]), (8, "N", 1))
        self.assertFalse(any(opened.kwargs[key] for key in ("xonxoff", "rtscts", "dsrdtr")))
        self.assertEqual(len(values), 1, statuses)
        self.assertEqual(values[0], (16.1, "0016.1", 1000.30))
        self.assertEqual(contexts[0]["source_unit"], "N")
        self.assertEqual(contexts[0]["scale_to_n"], 1.0)
        self.assertEqual(contexts[0]["original_value"], 16.1)
        self.assertEqual(contexts[0]["raw_bytes_hex"], b"0016.1".hex())
        self.assertEqual(contexts[0]["metadata_raw_hex"], raw.hex())
        self.assertTrue(any(event.get("metadata_raw_hex") == raw.hex() for event in events))
        self.assertTrue(port.closed)
        self.assertEqual(statuses[0][1], "info")
        self.assertTrue(any(level == "success" for _, level in statuses))

    def test_unverified_kgf_wire_profile_is_not_guessed_or_scaled(self):
        reader = GaugeReader("COM13", 2400, 1000.0, protocol="zp500n")
        _port, values, contexts, _events, statuses, _ = self.run_reader(reader, [
            (0.02, observed_metadata("1") * 3), (0.09, b""),
            (0.2, b"0016.1"), (0.27, b""),
        ])
        self.assertFalse(values)
        self.assertFalse(contexts)
        self.assertEqual(_port.writes, [b"\xfa"])
        self.assertTrue(any(level == "error" for _, level in statuses))

    def test_unknown_unit_never_sends_start_or_emits_measurement(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, events, statuses, _ = self.run_reader(reader, [
            (0.02, observed_metadata("9") * 3), (0.09, b""),
            (0.2, b"15.000"), (0.27, b""),
        ])
        self.assertEqual(port.writes, [b"\xfa"])
        self.assertFalse(values)
        self.assertTrue(any(level == "error" for _, level in statuses))
        self.assertFalse(any(level == "success" for _, level in statuses))
        self.assertTrue(any(event.get("raw_bytes_hex") for event in events))
        self.assertTrue(port.closed)

    def test_absent_metadata_does_not_treat_plain_numeric_as_newton_measurement(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, _events, statuses, _ = self.run_reader(reader, [
            (0.1, b"15.000"), (0.2, b""), (3.2, b""),
        ])
        self.assertEqual(port.writes, [b"\xfa"])
        self.assertFalse(values)
        self.assertTrue(any(level == "error" for _, level in statuses))

    def test_stop_during_post_open_wait_does_not_start_zp_handshake(self):
        for stop_at in (0.01, 0.05):
            with self.subTest(stop_at=stop_at):
                reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
                clock = [0.0]
                port = FakeGaugePort(reader, [], clock)
                def stop_during_delay(seconds):
                    clock[0] += seconds
                    if clock[0] >= stop_at:
                        reader.stop()
                with patch("calibration_acquisition.serial.Serial", return_value=port), \
                     patch("calibration_acquisition.time.monotonic", side_effect=lambda: clock[0]), \
                     patch("calibration_acquisition.time.sleep", side_effect=stop_during_delay), \
                     patch("zp500n_protocol.ZP500NProtocol.start") as start:
                    reader.run()
                self.assertEqual(port.writes, [])
                self.assertEqual(port.read_calls, 0)
                self.assertTrue(port.closed)
                start.assert_not_called()

    def test_short_handshake_write_stops_and_closes_without_retry(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        with patch.object(FakeGaugePort, "write", return_value=0) as write:
            port, values, _contexts, _events, statuses, _ = self.run_reader(reader, [])
        write.assert_called_once_with(b"\xfa")
        self.assertFalse(values)
        self.assertEqual(port.read_calls, 0)
        self.assertTrue(port.closed)
        self.assertTrue(any(level == "error" and "未完整发送" in text for text, level in statuses))

    def test_control_line_configuration_and_open_failures_close_unopened_object(self):
        for stage in ("dtr", "rts", "port", "open"):
            with self.subTest(stage=stage):
                reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
                port, values, _contexts, _events, statuses, _ = self.run_reader(reader, [], fail_stage=stage)
                self.assertTrue(port.closed)
                self.assertFalse(port.is_open)
                self.assertEqual(port.writes, [])
                self.assertEqual(port.read_calls, 0)
                self.assertFalse(values)
                self.assertTrue(any(level == "error" and "串口打开失败" in message for message, level in statuses))

    def test_short_output_request_is_not_retried_or_used_as_reference(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, _events, statuses, _ = self.run_reader(reader, [
            (0.02, observed_metadata() * 3), (0.2, b"0016.1"),
        ], fail_stage="write_25")
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        self.assertFalse(values)
        self.assertTrue(port.closed)
        self.assertTrue(any(level == "error" and "未完整发送" in text for text, level in statuses))

    def test_initialization_timeout_clock_starts_after_post_open_wait(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, _events, statuses, _ = self.run_reader(reader, [(2.99, b""), (3.01, b"")])
        self.assertEqual(port.writes, [b"\xfa"])
        self.assertFalse(values)
        self.assertTrue(any(level == "error" and "timed out" in message for message, level in statuses))
        self.assertAlmostEqual(port.write_times[0], 0.05)
        self.assertGreaterEqual(port.clock[0], 3.05)

    def test_malformed_numeric_bytes_fail_closed_and_are_preserved(self):
        reader = GaugeReader("COM13", 2400, 1.0, protocol="zp500n")
        port, values, _contexts, events, _statuses, _ = self.run_reader(reader, [
            (0.02, observed_metadata() * 3), (0.09, b""),
            (0.2, b"12.34.56"), (0.27, b""),
            (0.4, b"2.000"), (0.47, b""),
        ])
        self.assertEqual(port.writes, [b"\xfa", b"\x25"])
        self.assertFalse(values)
        self.assertTrue(any(event.get("raw_bytes_hex") == b"12.34.56".hex() for event in events))

    def make_dialog(self):
        with patch("calibration_acquisition.serial.tools.list_ports.comports", return_value=[]):
            dialog = CalibrationAcquisitionDialog()
        self.addCleanup(lambda: (setattr(dialog, "capturing", False), dialog.close(), dialog.deleteLater()))
        return dialog

    def test_ui_protocol_selection_defaults_zp_to_2400_and_disables_manual_scaling(self):
        dialog = self.make_dialog()
        self.assertEqual(dialog.gauge_protocol.currentData(), "ascii")
        self.assertEqual(dialog.gauge_baud.currentText(), "9600")
        self.assertTrue(dialog.gauge_scale.isEnabled())
        dialog.gauge_protocol.setCurrentIndex(1)
        self.assertEqual(dialog.gauge_protocol.currentData(), "zp500n")
        self.assertEqual(dialog.gauge_baud.currentText(), "2400")
        self.assertFalse(dialog.gauge_baud.isEnabled())
        self.assertFalse(dialog.gauge_scale.isEnabled())
        for baud in ("1200", "2400", "4800"):
            self.assertGreaterEqual(dialog.gauge_baud.findText(baud), 0)

    def test_reconnect_discards_previous_reference_and_ignores_old_reader_queued_signals(self):
        dialog = self.make_dialog()
        dialog.gauge_port.addItem("COM13")
        with patch.object(GaugeReader, "start"):
            dialog._toggle_gauge()
            first = dialog.gauge_reader
            first.measurement.emit(15.0, "15.0", 100.0)
            self.assertTrue(dialog._gauge_seen)
            dialog._toggle_gauge()
            self.assertFalse(dialog._gauge_seen)
            self.assertFalse(dialog.gauge_history)
            dialog._toggle_gauge()
            second = dialog.gauge_reader
            first.measurement.emit(17.0, "17.0", 100.1)
            self.assertFalse(dialog._gauge_seen)
            second.measurement.emit(0.0, "0.0", 100.2)
            self.assertEqual(len(dialog.gauge_history), 1)
            self.assertEqual(dialog.gauge_history[0]["value_n"], 0.0)
            self.assertGreater(dialog.gauge_history[0]["gauge_connection_epoch"], 1)
            first.reference_invalidated.emit({"reason": "late_old_connection_backlog"})
            self.assertEqual(len(dialog.gauge_history), 1)

    def test_pending_disconnect_keeps_reader_and_prevents_second_open(self):
        dialog = self.make_dialog()
        dialog.gauge_port.addItem("COM13")
        with patch.object(GaugeReader, "start") as start:
            dialog._toggle_gauge()
            reader = dialog.gauge_reader
            with patch.object(reader, "wait", return_value=False):
                dialog._toggle_gauge()
                dialog._toggle_gauge()
                self.assertIs(dialog.gauge_reader, reader)
                self.assertEqual(start.call_count, 1)
                self.assertIn("正在退出", dialog.gauge_value.text())
            dialog._toggle_gauge()
            self.assertIsNone(dialog.gauge_reader)

    def test_zp_audit_keeps_units_original_value_raw_and_metadata_in_trajectory(self):
        dialog = self.make_dialog()
        with tempfile.TemporaryDirectory() as folder:
            dialog.device_sn.setText("ZP-AUDIT")
            dialog.output_dir.setText(folder)
            dialog.gauge_protocol.setCurrentIndex(1)
            dialog._start_session()
            writer = dialog.trajectory_writer
            raw = observed_metadata()
            dialog._on_gauge_protocol_event({
                "event": "zp500n_protocol_state", "protocol": "zp500n", "state": "streaming",
                "metadata_raw_hex": raw.hex(), "metadata_raw_text": raw.decode(), "source_unit": "N",
            })
            dialog._on_gauge_sample_context({
                "protocol": "zp500n", "source_unit": "N", "original_value": 16.1,
                "scale_to_n": 1.0, "force_n": 16.1, "raw_text": "0016.1",
                "raw_bytes_hex": b"0016.1".hex(), "host_received_at": 100.0,
            })
            dialog._on_gauge(16.1, "0016.1", 100.0)
            dialog._finish_session(stopped=True)
            with writer.gauge_path.open(newline="", encoding="utf-8-sig") as stream:
                row = next(csv.DictReader(stream))
            self.assertEqual(row["source_unit"], "N")
            self.assertEqual(row["original_value"], "16.1")
            self.assertEqual(row["protocol"], "zp500n")
            self.assertEqual(row["raw_bytes_hex"], b"0016.1".hex())
            self.assertEqual(json.loads(row["protocol_metadata_json"])["metadata_raw_hex"], raw.hex())
            self.assertAlmostEqual(float(row["force_n"]), 16.1)

    def test_new_calibration_session_clears_old_gauge_history(self):
        dialog = self.make_dialog()
        with tempfile.TemporaryDirectory() as folder:
            dialog.device_sn.setText("ZP-RESTART")
            dialog.output_dir.setText(folder)
            dialog._on_gauge(15.0, "15.0", 100.0)
            self.assertTrue(dialog.gauge_history)
            dialog._start_session()
            self.assertFalse(dialog.gauge_history)
            self.assertFalse(dialog._gauge_seen)
            dialog._on_gauge(16.0, "16.0", 101.0)
            dialog._finish_session(stopped=True)
            dialog._start_session()
            self.assertFalse(dialog.gauge_history)
            dialog._finish_session(stopped=True)


if __name__ == "__main__":
    unittest.main()

"""Acquisition integrity and audit tests; force calibration is unchanged."""
import os
import time
import unittest
from unittest.mock import Mock

import numpy as np

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from PyQt5 import QtWidgets
from main import DIAGNOSTIC_CSV_COLUMNS, FIRMWARE_COMPAT_MODE, Monitor, RAW_TARE_MODE, RawForceReader
from no8_raw_tare_core import AcquisitionDiagnostic, PairedFrame, StreamPairer, build_tare


def raw_line(ms, value=0):
    return f"RAW,{ms}," + ",".join([str(value)] * 24)


def diag_line(ms, mask=255, errors=0, epoch=1, fw="h3_diag_20261014_v1"):
    return f"DIAG,{ms},{fw},{mask},{errors},{epoch}"


class PairerDiagnosticTests(unittest.TestCase):
    def pair(self, p, ms, value=0):
        self.assertIsNone(p.feed_line(raw_line(ms, value), 10.0))
        return p.feed_line(f"FORCE,{ms},0,0,1", 10.1)

    def test_legacy_firmware_pairs_without_diagnostics(self):
        frame = self.pair(StreamPairer(), 100)
        self.assertEqual(frame.device_ms, 100)
        self.assertIsNone(frame.diagnostic)

    def test_new_firmware_requires_matching_valid_diagnostic(self):
        p = StreamPairer()
        p.feed_line(diag_line(100), 9.9)
        self.assertIsNone(self.pair(p, 99))
        frame = self.pair(p, 100)
        self.assertEqual(frame.diagnostic.device_ms, frame.device_ms)
        self.assertTrue(frame.diagnostic.valid)
        self.assertIsNone(self.pair(p, 101))  # no DIAG for this cycle

    def test_invalid_cycle_discards_partial_then_recovers(self):
        p = StreamPairer()
        p.feed_line(diag_line(100))
        p.feed_line(raw_line(100, 9))
        p.feed_line(diag_line(200, mask=253, errors=1))
        self.assertIsNone(p.feed_line("FORCE,100,0,0,1"))
        self.assertIsNone(self.pair(p, 200))
        p.feed_line(diag_line(300, errors=1))
        frame = self.pair(p, 300, value=3)
        np.testing.assert_array_equal(frame.raw, np.full(24, 3))
        self.assertEqual(frame.diagnostic.error_count, 1)

    def test_epoch_zero_is_not_valid_even_with_full_mask(self):
        p = StreamPairer()
        p.feed_line(diag_line(100, epoch=0))
        self.assertFalse(p.last_diagnostic.valid)
        self.assertIsNone(self.pair(p, 100))

    def test_malformed_diag_cannot_reuse_prior_valid_cycle(self):
        for bad in ["DIAG,200,fw,255,0", "DIAG,200,fw,256,0,1", "DIAG,200,fw,255,-1,1", "DIAG,200,fw,255,0,1.0"]:
            p = StreamPairer()
            p.feed_line(diag_line(100))
            p.feed_line(raw_line(100))
            with self.assertRaises(ValueError): p.feed_line(bad)
            self.assertIsNone(p.feed_line("FORCE,100,0,0,1"))
            self.assertIsNone(self.pair(p, 200))
            p.feed_line(diag_line(300))
            self.assertIsNotNone(self.pair(p, 300))

    def test_state_transitions_are_explicit(self):
        cases=[(diag_line(200, epoch=2), "BASELINE_CHANGED"),
               (diag_line(200, fw="new_fw"), "FIRMWARE_CHANGED"),
               (diag_line(10, errors=5), "DEVICE_TIME_RESET"),
               (diag_line(200, errors=4), "ERROR_COUNTER_RESET")]
        for line, reason in cases:
            p = StreamPairer()
            p.feed_line(diag_line(100, errors=5))
            self.pair(p, 100)
            p.feed_line(line)
            self.assertIn(reason, p.last_diagnostic.reset_reason)

    def test_duplicate_pair_is_not_a_new_measurement(self):
        p=StreamPairer();p.feed_line(diag_line(100))
        self.assertIsNotNone(self.pair(p,100))
        p.feed_line(diag_line(100))
        self.assertIsNone(self.pair(p,100))

    def test_legacy_clock_restart_is_marked(self):
        p=StreamPairer();self.pair(p,100)
        self.assertEqual(self.pair(p,10).reset_reason,"DEVICE_TIME_RESET")

    def test_tare_refuses_mixed_baseline_epochs(self):
        frames=[PairedFrame(i,10,np.zeros(24),np.zeros(3),
                 AcquisitionDiagnostic(i,10,"fw",255,0,1 if i<5 else 2)) for i in range(10)]
        with self.assertRaisesRegex(ValueError,"基线变化"):build_tare(frames)


class MonitorDiagnosticTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.w=Monitor();self.w.timeout_timer.stop();self.p=StreamPairer()
        self.ms=0
        self.receipt_origin=time.time()-2.

    def tearDown(self):
        self.w.calibration_dialog=None
        self.w.reader=None
        self.w.close()

    def cycle(self,mask=255,errors=0,epoch=1,value=0):
        self.ms+=100
        now=self.receipt_origin+self.ms/1000
        self.p.feed_line(diag_line(self.ms,mask,errors,epoch),now)
        self.w._on_diagnostic(self.p.last_diagnostic)
        self.p.feed_line(raw_line(self.ms,value),now)
        frame=self.p.feed_line(f"FORCE,{self.ms},0,0,0",now)
        if frame is not None:self.w._on_paired(frame)
        return frame

    def establish_tare(self):
        for _ in range(20):self.cycle()
        self.w._tare()
        self.assertIsNotNone(self.w.tare_raw)
        self.w.recording=True

    def tail(self,row):
        values = dict(zip(self.w._csv_header(), row))
        return {name: values[name] for name in DIAGNOSTIC_CSV_COLUMNS}

    def test_invalid_diagnostic_is_blank_audit_row_and_valid_cycle_recovers(self):
        self.establish_tare();self.cycle(value=4000)
        self.cycle(mask=254,errors=1)
        row=self.w.records[-1]
        self.assertEqual(row[0],"DIAG_INVALID")
        self.assertEqual(row[3],"")
        self.assertEqual(row[38:94],[""]*56)
        self.assertEqual(row[33],"INVALID")
        self.assertEqual(self.tail(row)["firmware_valid_mask"],254)
        self.assertTrue(self.w.recording)
        self.assertEqual(len(self.w.paired_frames),0)
        self.assertIn("—",self.w.total_readout.text())
        self.cycle(errors=1,value=3000)
        self.assertFalse(self.w.data_stale)
        self.assertEqual(self.w.records[-1][0],"PAIRED")
        self.assertIsInstance(self.w.records[-1][3],float)

    def test_baseline_change_clears_tare_and_preserves_raw_without_fake_force(self):
        self.establish_tare();self.cycle(epoch=2,value=4)
        self.assertIsNone(self.w.tare_raw)
        self.assertTrue(self.w.recording)
        self.assertEqual([r[0] for r in self.w.records], ["DIAG_RESET","RAW_NO_TARE"])
        row=self.w.records[-1]
        self.assertEqual(row[38:46],[""]*8)
        self.assertEqual(row[46:70],[4.0]*24)
        self.assertEqual(row[70:94],[""]*24)
        self.assertEqual(self.tail(row)["host_tare_valid"],0)
        self.assertEqual(len(self.w.paired_frames),1)

    def test_error_counter_increase_without_invalid_mask_is_audited(self):
        self.establish_tare();self.cycle(errors=2,value=4000)
        self.assertEqual([r[0] for r in self.w.records],["DIAG_ERROR_COUNT","PAIRED"])
        self.assertEqual(self.w.records[0][3],"")
        self.assertEqual(self.tail(self.w.records[0])["firmware_error_count"],2)

    def test_first_nonzero_counter_is_not_attributed_to_current_record(self):
        self.w.recording=True
        self.cycle(errors=99)
        self.assertNotIn("DIAG_ERROR_COUNT",[r[0] for r in self.w.records])

    def test_protocol_error_removes_stale_force_and_stops_calibration_capture(self):
        self.establish_tare()
        dialog=Mock();dialog.capturing=True;dialog.trajectory_error=None;self.w.calibration_dialog=dialog
        self.w._on_stream_fault("bad DIAG","DIAG,bad",time.time())
        dialog._finish_session.assert_called_once_with(stopped=True)
        dialog._complete_capture.assert_not_called()
        self.assertEqual(self.w.records[-1][0],"STREAM_ERROR")
        self.assertEqual(self.w.records[-1][3],"")
        self.assertTrue(self.w.recording)
        self.assertIn("本步未完成",dialog.step_instruction.setText.call_args.args[0])

    def test_legacy_frames_are_usable_and_show_unavailable_diagnostics(self):
        now=time.time()
        for i in range(20):self.w._on_paired(PairedFrame(i,now-1.9+i*.1,np.zeros(24),np.zeros(3)))
        self.w._tare();self.w.recording=True
        self.w._on_paired(PairedFrame(21,now+.1,np.ones(24),np.zeros(3)))
        row=self.w.records[-1]
        self.assertEqual(row[0],"PAIRED")
        self.assertEqual(self.tail(row)["acquisition_diag_available"],0)
        self.assertIn("DIAG_UNAVAILABLE",row[34])
        self.assertIn("诊断不可用",self.w.force_readout.text())

    def test_recording_blocks_tare_and_force_mode_changes(self):
        self.establish_tare()
        old_tare=self.w.tare_raw.copy();old_epoch=self.w.host_tare_epoch
        self.w._tare()
        np.testing.assert_array_equal(self.w.tare_raw,old_tare)
        self.assertEqual(self.w.host_tare_epoch,old_epoch)
        self.w.force_mode_box.setCurrentText(FIRMWARE_COMPAT_MODE)
        self.assertEqual(self.w.force_mode_box.currentText(),RAW_TARE_MODE)

    def test_retare_after_stop_is_audited_and_cannot_mix_existing_records(self):
        self.establish_tare();self.cycle()
        original=list(self.w.records)
        self.w._stop_recording();self.w._tare()
        self.assertEqual(self.w.records[:-1],original)
        self.assertEqual(self.w.records[-1][0],"TARE_CHANGED")
        self.assertEqual(self.w.records[-1][3],"")
        self.assertEqual(self.tail(self.w.records[-1])["host_tare_epoch"],2)
        self.w._start_recording()
        self.assertFalse(self.w.recording)
        self.w._start_protocol()
        self.assertFalse(self.w.protocol.active)

    def test_counter_rollback_invalidates_old_host_tare(self):
        self.establish_tare();self.cycle(errors=8)
        self.cycle(errors=1)
        self.assertIsNone(self.w.tare_raw)
        self.assertIn("DIAG_RESET",[r[0] for r in self.w.records])
        self.assertEqual(self.w.records[-1][0],"RAW_NO_TARE")

    def test_disconnect_clears_tare_pair_history_and_writes_blank_event(self):
        self.establish_tare();self.cycle(value=4000)
        reader=Mock();self.w.reader=reader
        self.w._toggle_connection()
        reader.stop.assert_called_once();reader.wait.assert_called_once()
        self.assertIsNone(self.w.reader)
        self.assertIsNone(self.w.tare_raw)
        self.assertEqual(len(self.w.paired_frames),0)
        self.assertEqual(self.w.last_paired_at,0)
        self.assertEqual(self.w.records[-1][0],"STREAM_DISCONNECTED")
        self.assertEqual(self.w.records[-1][3],"")
        self.assertEqual(self.w.records[-1][38:94],[""]*56)

    def test_late_signal_from_old_reader_cannot_restore_cached_values(self):
        previous=RawForceReader("unused",115200)
        current=RawForceReader("unused",115200)
        previous.paired_received.connect(self.w._on_paired)
        self.w.reader=current
        previous.paired_received.emit(PairedFrame(100,time.time(),np.full(24,999),np.ones(3)))
        self.assertEqual(len(self.w.paired_frames),0)
        self.assertEqual(self.w.last_paired_at,0)
        np.testing.assert_array_equal(self.w.raw,np.zeros(24))


if __name__=="__main__":unittest.main()

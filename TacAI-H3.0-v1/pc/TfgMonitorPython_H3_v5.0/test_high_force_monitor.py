"""Integration checks for continuous history, truth provenance and safe export."""
import csv
import itertools
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import numpy as np
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PyQt5 import QtWidgets
from main import DIAGNOSTIC_CSV_COLUMNS, HIGH_FORCE_CSV_COLUMNS, Monitor
from no8_raw_tare_core import PairedFrame


class HighForceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.w = Monitor(); self.w.timeout_timer.stop()
        self.ms = 0
        self.receipt_origin = time.time()-2.
        for _ in range(20): self.frame()
        self.w._tare()
        self.w.protocol_mode.setCurrentIndex(3)
        self.w.test_position_box.setCurrentText('前')

    def tearDown(self):
        self.w.close()

    def frame(self, raw=0., received_at=None):
        self.ms += 100
        self.w._on_paired(PairedFrame(self.ms, self.receipt_origin+self.ms/1000 if received_at is None else received_at,
                                     np.full(24,raw), np.zeros(3)))

    def row(self):
        return dict(zip(self.w._csv_header(),self.w.records[-1]))

    def reference(self,value):
        # Simulate an explicit operator edit, including re-entering zero.
        self.w.reference_force.setValue(value)
        self.w.reference_force.lineEdit().setText(f'{value:.3f} N')
        self.w.reference_force.lineEdit().textEdited.emit(f'{value:.3f} N')

    def test_waiting_and_holding_are_both_recorded_without_fabricated_reference(self):
        with patch('main.time.monotonic', side_effect=itertools.count(100.,.01)):
            self.w._start_protocol()
            self.frame(raw=100.)
            waiting=self.row()
            self.assertEqual(waiting['test_phase'],'waiting_for_load')
            self.assertIsNone(waiting['measured_reference_resultant_n'])
            self.assertEqual(waiting['reference_force_source'],'')
            self.reference(.12)
            self.w._protocol_action()
            # A wall-clock jump does not terminate monotonic hold timing.
            self.frame(raw=300.,received_at=time.time()-30)
            holding=self.row()
            self.assertEqual(holding['test_phase'],'hold_reference')
            self.assertEqual(holding['nominal_reference_resultant_n'],0.)
            self.assertEqual(holding['measured_reference_resultant_n'],.12)
            self.assertEqual(holding['reference_force_source'],'MANUAL_PLATEAU_READING')
            self.assertEqual(holding['stable_3s_sample'],0)
            self.assertEqual(holding['protocol_clock_source'],'MONOTONIC')
            self.assertGreater(holding['protocol_phase_elapsed_s'],0)
            self.assertEqual(len(self.w.records),2)
            self.assertTrue(self.w.protocol.active)
            self.assertTrue(self.w.recording)
            self.assertEqual(holding['selected_resultant_or_diagnostic_n'],holding['scalar_resultant_raw_model_n'])
            self.assertAlmostEqual(sum(holding[f'S{i}_resultant_allocation_estimated_n'] for i in range(1,9)),holding['scalar_resultant_raw_model_n'])

    def test_first_step_completion_keeps_waiting_history_and_clears_reference(self):
        with patch('main.time.monotonic', return_value=100.): self.w._start_protocol()
        self.reference(.1)
        with patch('main.time.monotonic', return_value=101.): self.w._protocol_action()
        with patch('main.time.monotonic', side_effect=itertools.count(281.,.01)): self.frame(raw=100.)
        self.assertTrue(self.w.recording)
        self.assertEqual(self.w.protocol.phase,'waiting_for_load')
        self.assertEqual(self.w.protocol.point_index,1)
        self.assertIsNone(self.w.protocol.reference_force_n)
        self.assertEqual(self.w.protocol.summaries[0]['acceptance'],'DATA_ONLY')
        with patch('main.time.monotonic', side_effect=itertools.count(282.,.01)): self.frame(raw=500.)
        self.assertEqual(self.row()['nominal_reference_resultant_n'],15.)
        self.assertIsNone(self.row()['measured_reference_resultant_n'])

    def test_active_sequence_locks_configuration_and_tare(self):
        self.w._start_protocol()
        original=self.w.tare_raw.copy()
        self.w._tare(); np.testing.assert_array_equal(self.w.tare_raw,original)
        self.w.high_force_correction_box.setChecked(False)
        self.assertTrue(self.w.provisional_high_force_enabled)
        self.assertFalse(self.w.protocol_mode.isEnabled())
        self.assertFalse(self.w.test_position_box.isEnabled())
        self.w._open_calibration(); self.assertIsNone(self.w.calibration_dialog)
        self.w._clear_recording(); self.assertTrue(self.w.protocol.active)
        self.w._stop_recording()
        self.assertFalse(self.w.protocol.active)
        self.assertFalse(self.w.recording)
        self.assertTrue(self.w.protocol_mode.isEnabled())

    def test_middle_position_and_nonempty_record_are_rejected(self):
        self.w.test_position_box.setCurrentText('中')
        self.w._start_protocol(); self.assertFalse(self.w.protocol.active)
        self.w.test_position_box.setCurrentText('前')
        self.w.records.append(self.w._record_base('MARK',0,time.time()))
        self.w._start_protocol(); self.assertFalse(self.w.protocol.active)

    def test_csv_shape_preserves_original_columns_and_diag_epoch_guard(self):
        self.w._start_protocol(); self.frame()
        row=self.w.records[-1];header=self.w._csv_header()
        self.assertEqual(len(row),len(header))
        self.assertEqual(header[38:46],[f'S{i}_resultant_allocation_estimated_n' for i in range(1,9)])
        self.assertEqual(header[107:107+len(HIGH_FORCE_CSV_COLUMNS)],HIGH_FORCE_CSV_COLUMNS)
        self.assertEqual(header[113:123],DIAGNOSTIC_CSV_COLUMNS)
        self.assertFalse(self.w._recording_configuration_conflict())
        self.w.host_tare_epoch += 1
        self.assertTrue(self.w._recording_configuration_conflict())

    def test_export_uses_high_force_summary_name_and_retains_data_only(self):
        with patch('main.time.monotonic', return_value=100.):self.w._start_protocol()
        self.reference(.1)
        with patch('main.time.monotonic', return_value=101.):self.w._protocol_action()
        with patch('main.time.monotonic', side_effect=itertools.count(281.,.01)):self.frame()
        self.w._stop_protocol()
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'capture.csv'
            with patch.object(QtWidgets.QFileDialog,'getSaveFileName',return_value=(str(path),'')):self.w._export()
            with path.open(encoding='utf-8-sig',newline='') as f: rows=list(csv.DictReader(f))
            self.assertEqual(sum(r['record_type']=='PAIRED' for r in rows),1)
            self.assertEqual(rows[-1]['record_type'],'PROTOCOL_STOPPED')
            self.assertTrue(rows[0]['high_force_session_id'])
            summary=Path(folder)/'capture_high_force_summary.csv'
            self.assertTrue(summary.is_file())
            with summary.open(encoding='utf-8-sig',newline='') as f: summaries=list(csv.DictReader(f))
            self.assertEqual(summaries[0]['acceptance'],'DATA_ONLY')
            self.assertEqual(summaries[0]['reference_is_continuous_measurement'],'False')
            self.assertFalse((Path(folder)/'capture_stable3s_summary.csv').exists())

    def test_new_platform_requires_an_explicit_reference_edit(self):
        with patch('main.time.monotonic', return_value=100.):self.w._start_protocol()
        with patch('main.time.monotonic', return_value=101.):self.w._protocol_action()
        self.assertEqual(self.w.protocol.phase,'waiting_for_load')
        self.reference(0.)
        with patch('main.time.monotonic', return_value=102.):self.w._protocol_action()
        self.assertEqual(self.w.protocol.phase,'hold_reference')
        with patch('main.time.monotonic', side_effect=itertools.count(282.,.01)):self.frame()
        self.assertFalse(self.w.high_force_reference_entered)
        with patch('main.time.monotonic', return_value=283.):self.w._protocol_action()
        self.assertEqual(self.w.protocol.phase,'waiting_for_load')
        self.assertIsNone(self.w.protocol.reference_force_n)

    def test_clear_removes_session_metadata_and_old_summaries(self):
        with patch('main.time.monotonic', return_value=100.):self.w._start_protocol()
        self.reference(0.)
        with patch('main.time.monotonic', return_value=101.):self.w._protocol_action()
        with patch('main.time.monotonic', side_effect=itertools.count(281.,.01)):self.frame()
        self.w._stop_protocol()
        self.assertTrue(self.w.protocol.summaries)
        self.w._start_recording();self.assertFalse(self.w.recording)
        with patch.object(QtWidgets.QMessageBox,'question',return_value=QtWidgets.QMessageBox.Yes):self.w._clear_recording()
        self.assertEqual(self.w.protocol.kind,'none')
        self.assertEqual(self.w.protocol.summaries,[])
        self.assertEqual(self.w.high_force_session_id,'')
        self.assertEqual(self.w._record_base('MARK',0,time.time())[107:107+len(HIGH_FORCE_CSV_COLUMNS)],['']*len(HIGH_FORCE_CSV_COLUMNS))

    def test_active_disconnect_is_audited_before_recording_stops(self):
        self.w._start_protocol()
        self.w.reader=Mock();self.w.reader.wait.return_value=True
        self.w._toggle_connection()
        self.assertEqual(self.w.records[-1][0],'STREAM_DISCONNECTED')
        self.assertEqual(self.w.records[-1][3],'')
        self.assertFalse(self.w.recording)
        self.assertFalse(self.w.protocol.active)
        self.assertTrue(self.w.protocol_mode.isEnabled())

    def test_clock_failure_is_exported_as_a_control_event(self):
        with patch('main.time.monotonic',return_value=100.):self.w._start_protocol()
        event=self.w.protocol.feed(99.,1.,1.,0.)
        with patch('main.time.monotonic',return_value=101.):self.w._update_protocol_ui(event)
        row=self.row()
        self.assertEqual(row['record_type'],'PROTOCOL_CLOCK_ERROR')
        self.assertEqual(row['acceptance_state'],'INVALID_CLOCK')
        self.assertFalse(self.w.recording)

    def test_watchdog_uses_receipt_monotonic_time_in_high_force_session(self):
        with patch('main.time.monotonic',return_value=100.):self.w._start_protocol()
        with patch('main.time.monotonic',side_effect=itertools.count(101.,.01)):self.frame(received_at=time.time()-30.)
        self.w.reader=Mock()
        with patch('main.time.monotonic',return_value=101.5):self.w._check_data_timeout()
        self.assertFalse(self.w.data_stale)
        self.assertTrue(self.w.protocol.active)
        with patch('main.time.monotonic',return_value=103.):self.w._check_data_timeout()
        self.assertTrue(self.w.data_stale)
        self.assertFalse(self.w.protocol.active)
        self.w.reader=None


if __name__=='__main__':unittest.main()

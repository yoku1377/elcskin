"""Rear/front selection is explicit, mutually exclusive and locked in a run."""
import os,unittest
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets
from main import Monitor

class BackForceMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])
    def setUp(self):self.w=Monitor();self.w.timeout_timer.stop()
    def tearDown(self):self.w.reader=None;self.w.close()

    def test_front_back_parameters_and_signature_switch_independently(self):
        self.assertIsNotNone(self.w.local_force_model);self.assertIsNotNone(self.w.back_force_model)
        self.assertTrue(self.w.local_force_box.model().item(2).isEnabled())
        self.w.local_force_box.setCurrentIndex(2)
        self.assertEqual(self.w.local_force_runtime.profile,'back')
        self.assertIs(self.w.local_force_runtime.model,self.w.back_force_model)
        self.assertIn('LOCAL_BACK:',self.w._active_model_status())
        self.w.local_force_box.setCurrentIndex(1)
        self.assertEqual(self.w.local_force_runtime.profile,'front')
        self.assertIs(self.w.local_force_runtime.model,self.w.local_force_model)
        self.assertIn('LOCAL_FRONT:',self.w._active_model_status())
        self.w.local_force_box.setCurrentIndex(0)
        self.assertEqual(self.w.local_force_runtime.profile,'off');self.assertIsNone(self.w.local_force_runtime.model)

    def test_record_and_protocol_lock_keeps_back_selection(self):
        self.w.local_force_box.setCurrentIndex(2);self.w.recording=True
        self.w.local_force_box.setCurrentIndex(1)
        self.assertEqual(self.w.local_force_box.currentIndex(),2);self.assertEqual(self.w.local_force_runtime.profile,'back')
        self.w.recording=False

if __name__=='__main__':unittest.main()

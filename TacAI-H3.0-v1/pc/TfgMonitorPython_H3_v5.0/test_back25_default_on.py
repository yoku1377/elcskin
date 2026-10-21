"""Startup must select the real model; reloads and missing parameters are explicit."""
import os
import unittest
from unittest.mock import patch
import numpy as np

os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from PyQt5 import QtWidgets
from main import Monitor
from local_high_force import load_local_model
from test_back25_consistency_force import state


class Back25DefaultOnTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app=QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def window(self):
        w=Monitor();w.timeout_timer.stop()
        self.addCleanup(w.close)
        return w

    def test_fresh_launch_and_relaunch_select_back25_even_after_manual_off(self):
        first=self.window()
        first.local_force_box.setCurrentIndex(0)
        self.assertEqual(first.local_force_runtime.profile,'off')
        second=self.window()
        self.assertEqual(second.local_force_box.currentIndex(),6)
        self.assertEqual(second.local_force_runtime.profile,'auto')
        self.assertIs(second.local_force_runtime.model,second.automatic_compensation_model)

    def test_tare_and_connection_state_resets_keep_default_model(self):
        w=self.window()
        for reason in ('TARE_CHANGED','HOST_TARE_INVALIDATED','ACQUISITION_ERROR_COUNT_INCREASE'):
            w._reset_local_force(reason)
            self.assertEqual(w.local_force_box.currentIndex(),6)
            self.assertEqual(w.local_force_runtime.profile,'auto')
            self.assertIs(w.local_force_runtime.model,w.automatic_compensation_model)
            self.assertIsNone(w.local_force_result)

    def test_absent_resources_do_not_claim_correction_enabled(self):
        def loader(path, **kwargs):
            if path.name in ('local_force_profile.json','back25_smooth_profile.json','back25_domain_profile.json'):
                raise FileNotFoundError('Missing back25_smooth_profile.json')
            return load_local_model(path, **kwargs)
        with patch('main.load_local_model',side_effect=loader):
            w=self.window()
        self.assertEqual(w.local_force_box.currentIndex(),0)
        self.assertEqual(w.local_force_runtime.profile,'off')
        self.assertFalse(w.local_force_box.model().item(4).isEnabled())
        self.assertFalse(w.local_force_box.model().item(6).isEnabled())
        self.assertIn('参数不可用',w.local_force_box.toolTip())

    def test_default_does_not_relax_domain_checks(self):
        w=self.window();m=w.local_force_runtime.model
        st=state()
        object.__setattr__(st,'response24',np.full(24,1e7))
        object.__setattr__(st,'rising_raw24',st.quiet_raw24+st.response24)
        r=m.predict(np.arange(1.,25.)*100,29.,np.zeros(24),np.ones(8)*10,st,timestamp=2.)
        self.assertFalse(r.applied)
        self.assertEqual(r.reason,'AUTO_NO_SUPPORTED_PROFILE')
        self.assertEqual(r.total_n,29.)


if __name__=='__main__':unittest.main()

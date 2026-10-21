import os
from pathlib import Path
import unittest
from unittest import mock

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt5 import QtWidgets

from firmware_updater import CONFIRM_TEXT, FirmwareUpgradeDialog
from iap_protocol import load_image


ROOT = Path(__file__).resolve().parents[2]
IMAGE_PATH = ROOT / "release" / "TacAI_H3_App_v1.tacai"


class FirmwareUpgradeDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.dialog = FirmwareUpgradeDialog("COM5")
        self.dialog.image = load_image(IMAGE_PATH)
        self.dialog.path_edit.setText(str(IMAGE_PATH))
        self.dialog._update_enabled()

    def tearDown(self):
        self.dialog.close()

    def test_confirmation_instruction_is_visible_and_explicit(self):
        self.assertIn(CONFIRM_TEXT, self.dialog.confirm_edit.placeholderText())
        self.assertIn(CONFIRM_TEXT, self.dialog.status.text())
        self.assertFalse(self.dialog.start_button.isEnabled())
        self.dialog.confirm_edit.setText(CONFIRM_TEXT)
        self.assertTrue(self.dialog.start_button.isEnabled())

    @mock.patch.object(QtWidgets.QMessageBox, "warning")
    def test_click_without_confirmation_shows_actionable_warning(self, warning):
        self.dialog._start()
        warning.assert_called_once()
        self.assertIsNone(self.dialog.worker)
        self.assertIn("确认口令不正确", self.dialog.status.text())

    @mock.patch.object(QtWidgets.QMessageBox, "question", return_value=QtWidgets.QMessageBox.No)
    def test_correct_confirmation_reaches_final_confirmation(self, question):
        self.dialog.confirm_edit.setText(CONFIRM_TEXT)
        self.dialog._start()
        question.assert_called_once()
        self.assertIsNone(self.dialog.worker)


if __name__ == "__main__":
    unittest.main()

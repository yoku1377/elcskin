import os
from pathlib import Path
import unittest

os.environ.setdefault("QT_QPA_PLATFORM", "windows")

from PyQt5 import QtWidgets

from no8_hex_uart_flasher import CONFIRM_TEXT, No8HexFlashDialog


ROOT = Path(__file__).resolve().parents[3]
NO8_HEX = ROOT / "TacAI-H2.2" / "交付" / "TacAI_NO8_四区域平滑融合_v3" / "TacAI_NO8_4region_softfusion_v3.hex"


class No8HexFlashDialogTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QtWidgets.QApplication.instance() or QtWidgets.QApplication([])

    def setUp(self):
        self.dialog = No8HexFlashDialog()
        self.dialog._load_hex(NO8_HEX)

    def tearDown(self):
        self.dialog.close()

    def test_known_hex_is_loaded_with_expected_addresses(self):
        self.assertIsNotNone(self.dialog.image)
        self.assertIn("0x08000000", self.dialog.details.toPlainText())
        self.assertIn("44,160", self.dialog.details.toPlainText())

    def test_confirmation_alone_does_not_enable_without_port(self):
        self.dialog.port_box.clear()
        self.dialog.confirm_edit.setText(CONFIRM_TEXT)
        self.assertFalse(self.dialog.start_button.isEnabled())


if __name__ == "__main__":
    unittest.main()


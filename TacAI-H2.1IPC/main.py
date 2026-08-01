"""
Tactile sensor 3D force visualization — application entry point

Author: Yijie Luo
Email: yijie_luo@tongji.edu.cn
"""
import sys
from PyQt5 import QtWidgets
from tactile_vis import ForceVisualizer


def main():
    app = QtWidgets.QApplication(sys.argv)
    win = ForceVisualizer()
    win.show()
    sys.exit(app.exec_())


if __name__ == "__main__":
    main()

import os
import sys

from PyQt5 import QtCore, QtWidgets

print("python", sys.executable, flush=True)
print("libraryPaths-before", QtCore.QCoreApplication.libraryPaths(), flush=True)
print("QT_PLUGIN_PATH", os.environ.get("QT_PLUGIN_PATH"), flush=True)
print("QT_QPA_PLATFORM_PLUGIN_PATH", os.environ.get("QT_QPA_PLATFORM_PLUGIN_PATH"), flush=True)
app = QtWidgets.QApplication([])
print("platform", app.platformName(), flush=True)
app.quit()

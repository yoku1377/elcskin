"""Set the bundled Qt platform directory before the application imports Qt."""

import os
from pathlib import Path
import sys


runtime_root = Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
for candidate in (
    runtime_root / "PyQt5" / "Qt5" / "plugins" / "platforms",
    runtime_root / "PyQt5" / "Qt" / "plugins" / "platforms",
    runtime_root / "qt5_plugins" / "platforms",
):
    if (candidate / "qwindows.dll").exists():
        os.environ["QT_QPA_PLATFORM_PLUGIN_PATH"] = str(candidate)
        break

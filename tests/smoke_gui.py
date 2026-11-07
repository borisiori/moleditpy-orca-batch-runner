"""Headless smoke test: the dialog builds and closes without MoleditPy."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_calculator.gui import OrcaCalculatorDialog


class StubContext:
    """Minimal PluginContext stand-in for offscreen runs."""

    def __init__(self):
        self._settings = {}

    def get_setting(self, key, default=None):
        return self._settings.get(key, default)

    def set_setting(self, key, value):
        self._settings[key] = value

    def get_main_window(self):
        return None

    def register_window(self, *_args):
        pass


def main() -> int:
    app = QApplication(sys.argv)
    dialog = OrcaCalculatorDialog(None, StubContext(), settings={}, version="0.1.0")
    dialog.show()
    QTimer.singleShot(1500, dialog.close)  # smoke mode: no interaction
    app.exec()
    return 0


if __name__ == "__main__":
    sys.exit(main())

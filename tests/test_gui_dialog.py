"""Dialog-level tests: setup bar prefill and status."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from orca_batch_runner import config
from orca_batch_runner.gui import OrcaCalculatorDialog


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


def test_dialog_prefills_detected_binary(tmp_path, monkeypatch):
    """On open the bar shows the auto-detected ORCA; no Detect click needed."""
    app = QApplication.instance() or QApplication(sys.argv)
    fake = tmp_path / "orcamock"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(0o755)
    monkeypatch.setattr(config, "find_orca_binary", lambda: str(fake))

    dialog = OrcaCalculatorDialog(None, StubContext(), settings={}, version="t")

    assert dialog.binary_edit.text() == str(fake)
    assert dialog.workspace_edit.text() == str(tmp_path)
    assert str(fake) in dialog.paths_status.text()
    assert dialog.binary == str(fake)


def test_dialog_reports_missing_binary(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication(sys.argv)
    monkeypatch.setattr(config, "find_orca_binary", lambda: None)

    dialog = OrcaCalculatorDialog(None, StubContext(), settings={}, version="t")

    assert dialog.binary_edit.text() == ""
    assert "not set" in dialog.paths_status.text()

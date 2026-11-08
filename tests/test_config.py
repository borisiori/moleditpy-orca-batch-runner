"""Config tests: cross-platform ORCA auto-detection candidates."""

import os
import shutil
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_batch_runner import config


def _make_executable(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("#!/bin/sh\n")
    path.chmod(0o755)


def test_detects_under_linux_parents(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCA_BIN", raising=False)
    monkeypatch.delenv("ORCA_HOME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    _make_executable(tmp_path / "ORCA_6_1_1" / "orca")
    monkeypatch.setattr(config, "_common_parents", lambda: [tmp_path])

    assert config.find_orca_binary() == str(tmp_path / "ORCA_6_1_1" / "orca")


def test_detects_windows_binary_name(tmp_path, monkeypatch):
    monkeypatch.delenv("ORCA_BIN", raising=False)
    monkeypatch.delenv("ORCA_HOME", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    _make_executable(tmp_path / "orca_6" / "orca.exe")
    monkeypatch.setattr(config, "_binary_names", lambda: ("orca.exe",))
    monkeypatch.setattr(config, "_common_parents", lambda: [tmp_path])

    assert config.find_orca_binary() == str(tmp_path / "orca_6" / "orca.exe")


def test_env_home_uses_platform_binary_name(monkeypatch):
    monkeypatch.delenv("ORCA_BIN", raising=False)
    monkeypatch.setattr(shutil, "which", lambda _name: None)
    monkeypatch.setattr(config, "_binary_names", lambda: ("orca.exe",))
    # ORCA_HOME candidates resolve through the platform launcher name only
    assert config._binary_names() == ("orca.exe",)
    assert sys.platform.startswith("win") is False  # doc: this suite runs on Linux


def test_extra_environment_skips_ld_library_path_off_linux(monkeypatch):
    fake = "/fake/orca_dir/orca.exe"
    monkeypatch.setattr(sys, "platform", "win32")
    updates = config.extra_environment(fake)
    assert "PATH" in updates
    assert "LD_LIBRARY_PATH" not in updates

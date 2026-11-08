"""Proc tests: platform dispatch of the tree termination."""

import os
import signal
import sys
from types import SimpleNamespace

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_batch_runner import proc


def test_dispatches_to_taskkill_on_windows(monkeypatch):
    calls = []

    def fake_run(*args, **kwargs):
        calls.append(args[0])
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(proc, "IS_WINDOWS", True)
    monkeypatch.setattr(proc, "IS_LINUX", False)
    monkeypatch.setattr(proc.subprocess, "run", fake_run)

    proc.terminate_tree_sync(1234)

    assert len(calls) == 1
    assert calls[0][:2] == ["taskkill", "/PID"]
    assert "/T" in calls[0] and "/F" in calls[0]  # tree walk + force
    assert "1234" in calls[0]


def test_dispatches_to_proc_on_linux(monkeypatch):
    monkeypatch.setattr(proc, "IS_WINDOWS", False)
    monkeypatch.setattr(proc, "IS_LINUX", True)
    # a fake /proc tree: pid 10 -> child 11
    monkeypatch.setattr(proc, "tree_of", lambda pid: {10, 11})
    monkeypatch.setattr(proc, "is_alive", lambda pid: False)  # dies instantly
    signaled = []
    monkeypatch.setattr(proc, "signal_tree",
                        lambda pids, sig: signaled.append((tuple(pids), sig)))

    proc.terminate_tree_sync(10, grace=0)

    # SIGTERM to the whole tree; everyone already dead, so the SIGKILL
    # follow-up carries an empty survivor list
    assert signaled[0] == ((10, 11), signal.SIGTERM)
    assert signaled[1] == ((), signal.SIGKILL)


def test_probe_alive_semantics():
    # pid 1 always exists (init); signal 0 must not raise
    assert proc._probe_alive(1) is True
    # a PID that cannot exist: the kernel answers "no such process"
    assert proc._probe_alive(2**30) is False

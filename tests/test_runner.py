"""QueueRunner end-to-end tests against a fake ORCA executable."""

import os
import sys
import time

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtWidgets import QApplication

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_batch_runner.runner import QueueRunner

FAKE_OK = (
    "#!/usr/bin/env bash\n"
    "echo 'fake orca running'\n"
    "sleep 0.2\n"
    "echo 'some intermediate output'\n"
    "echo 'ORCA TERMINATED NORMALLY'\n"
    "touch fake_property.txt\n"
    "exit 0\n"
)

FAKE_FAIL = (
    "#!/usr/bin/env bash\n"
    "echo 'fake orca crashing'\n"
    "sleep 0.2\n"
    "echo 'Error: bad input'\n"
    "exit 1\n"
)


@pytest.fixture()
def app():
    return QApplication.instance() or QApplication(sys.argv)


@pytest.fixture()
def workspace(tmp_path):
    run = tmp_path / "Run"
    run.mkdir()
    for name in ("job_a.inp", "job_b.inp"):
        (run / name).write_text("! HF\n")
    return tmp_path


@pytest.fixture()
def fake_orca(tmp_path):
    def make(content):
        script = tmp_path / f"orca_{len(list(tmp_path.iterdir()))}"
        script.write_text(content)
        script.chmod(0o755)
        return str(script)

    return make


def run_queue(app, binary, workspace, inputs, timeout=20):
    """Drive the queue while spinning the event loop (QProcess needs it)."""
    runner = QueueRunner()
    events = {"started": [], "finished": [], "output": ""}
    done = []
    runner.job_started.connect(lambda n, i, t: events["started"].append(n))
    runner.job_output.connect(
        lambda text: events.__setitem__("output", events["output"] + text)
    )
    runner.job_finished.connect(
        lambda n, ok, msg: events["finished"].append((n, ok, msg))
    )
    runner.queue_finished.connect(done.append)

    assert runner.start(binary, str(workspace), [str(p) for p in inputs])
    deadline = time.time() + timeout
    while not done and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)
    assert done, "queue_finished was never emitted"
    return runner, events, done[0]


def test_queue_all_ok(app, workspace, fake_orca):
    binary = fake_orca(FAKE_OK)
    inputs = [workspace / "Run" / "job_a.inp", workspace / "Run" / "job_b.inp"]

    runner, events, results = run_queue(app, binary, workspace, inputs)

    assert [r["ok"] for r in results] == [True, True]
    assert events["started"] == ["job_a.inp", "job_b.inp"]
    assert runner.is_running() is False  # queue ended: relaunching must work
    for stem in ("job_a", "job_b"):
        out_dir = workspace / "Run" / stem
        assert (out_dir / f"{stem}.out").is_file()
        assert "ORCA TERMINATED NORMALLY" in (out_dir / f"{stem}.out").read_text()
        assert (out_dir / "fake_property.txt").is_file()
        # the input travels with its results — MOVED in, no loose duplicate
        assert (out_dir / f"{stem}.inp").is_file()
        assert not (workspace / "Run" / f"{stem}.inp").exists()
        # scratch cleaned after success
        assert not (workspace / "temp" / stem).exists()


def test_queue_with_failure(app, workspace, fake_orca):
    binary = fake_orca(FAKE_FAIL)
    inputs = [workspace / "Run" / "job_a.inp"]

    runner, events, results = run_queue(app, binary, workspace, inputs)

    assert results[0]["ok"] is False
    assert runner.is_running() is False
    # failed jobs keep their raw files in temp/ for inspection
    assert (workspace / "temp" / "job_a").is_dir()
    assert (workspace / "Run" / "job_a" / "job_a.out").is_file()
    # ...and the loose input stays in Run/ so Retry failed can re-run it
    assert (workspace / "Run" / "job_a.inp").is_file()


def test_stop_after_current(app, workspace, fake_orca):
    binary = fake_orca(FAKE_OK)
    inputs = [workspace / "Run" / "job_a.inp", workspace / "Run" / "job_b.inp"]

    runner = QueueRunner()
    done = []
    runner.queue_finished.connect(done.append)
    # Ask for the graceful stop as soon as the first file completes
    runner.job_finished.connect(
        lambda n, ok, msg: runner.stop_after_current() if n == "job_a.inp" else None
    )
    assert runner.start(binary, str(workspace), [str(p) for p in inputs])
    deadline = time.time() + 20
    while not done and time.time() < deadline:
        app.processEvents()
        time.sleep(0.02)

    results = done[0]
    assert [r["name"] for r in results] == ["job_a.inp"]
    assert results[0]["ok"] is True
    # Regression: the runner used to stay "running" after a graceful stop,
    # blocking any new queue with "A queue is already running."
    assert runner.is_running() is False
    runner2, _events2, results2 = run_queue(
        app, binary, workspace, [workspace / "Run" / "job_b.inp"]
    )
    assert [r["ok"] for r in results2] == [True]


def test_invalid_binary_rejected(workspace, fake_orca):
    app = QApplication.instance() or QApplication(sys.argv)
    runner = QueueRunner()
    assert runner.start(
        "/nonexistent/orca", str(workspace), [str(workspace / "Run" / "job_a.inp")]
    ) is False


def test_final_banner_styles(app):
    """The queue end shows a big launcher-style banner."""
    from PySide6.QtWidgets import QWidget

    from orca_batch_runner.run_tab import RunTab

    stub = QWidget()  # minimal dialog stand-in (buttons bind to its methods)
    stub.stop_queue = lambda: None
    stub.stop_after_current = lambda: None
    stub.retry_failed = lambda: None
    ok = [{"path": "/x/a.inp", "name": "a.inp", "ok": True, "error": ""}]
    tab = RunTab(stub, None, {})
    tab.on_queue_finished(ok)
    text = tab.console.toPlainText()
    assert "ALL FILES IN QUEUE FINISHED SUCCESSFULLY" in text
    lines = [ln for ln in text.splitlines() if ln.strip("=") == "" and ln]
    assert len(lines) >= 4 and min(len(ln) for ln in lines) >= 60  # big bars

    mixed = ok + [{"path": "/x/b.inp", "name": "b.inp", "ok": False,
                   "error": "boom"}]
    tab.on_queue_finished(mixed)
    text = tab.console.toPlainText()
    assert "QUEUE FINISHED — 1 OK, 1 FAILED" in text
    assert "FAILED: b.inp" in text

    tab.on_queue_finished([])
    assert "QUEUE STOPPED" in tab.console.toPlainText()

"""End-to-end pipeline against the real ORCA binary (run: real work, ~30 s).

Exercises queue run -> results collection -> monitor analysis -> sort,
in a throwaway workspace. Skips itself when ORCA is not installed.
"""

import os
import sys
import tempfile
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from PySide6.QtWidgets import QApplication

from orca_batch_runner import config, monitor_core, sort_core
from orca_batch_runner.runner import QueueRunner

H2_INP = """! HF STO-3G
* xyz 0 1
H  0.000000  0.000000  0.000000
H  0.000000  0.000000  0.740000
*
"""


def spin_until(app, done, timeout):
    deadline = time.time() + timeout
    while not done and time.time() < deadline:
        app.processEvents()
        time.sleep(0.05)
    return bool(done)


def main() -> int:
    binary = config.find_orca_binary()
    if not binary:
        print("SKIP: no ORCA binary found")
        return 0
    print(f"ORCA: {binary}")

    app = QApplication.instance() or QApplication(sys.argv)
    with tempfile.TemporaryDirectory(prefix="orca_plugin_e2e_") as tmp:
        workspace = os.path.join(tmp, "workspace")
        run_dir = os.path.join(workspace, "Run")
        os.makedirs(run_dir)
        with open(os.path.join(run_dir, "h2.inp"), "w") as f:
            f.write(H2_INP)

        # --- run the queue with the real binary
        runner = QueueRunner()
        finished = []
        runner.queue_finished.connect(finished.append)
        assert runner.start(binary, workspace, [os.path.join(run_dir, "h2.inp")])
        if not spin_until(app, finished, 120):
            print("FAIL: queue did not finish in 120 s")
            return 1
        results = finished[0]
        print(f"queue results: {results}")
        assert results[0]["ok"] is True, results[0]

        out_dir = os.path.join(run_dir, "h2")
        out_file = os.path.join(out_dir, "h2.out")
        assert os.path.isfile(out_file), "missing h2.out in results folder"
        content = open(out_file, encoding="utf-8", errors="ignore").read()
        assert "FINAL SINGLE POINT ENERGY" in content
        assert "ORCA TERMINATED NORMALLY" in content
        assert not os.path.exists(os.path.join(workspace, "temp", "h2")), \
            "scratch kept after success"
        # the input is MOVED inside the results folder: no loose duplicate
        assert os.path.isfile(os.path.join(out_dir, "h2.inp"))
        assert not os.path.exists(os.path.join(run_dir, "h2.inp"))

        # --- monitor reads the finished run back
        runs = monitor_core.collect_runs([run_dir])
        print("monitor:", [(r["name"], r["status"], r["type"]) for r in runs])
        assert runs and runs[0]["name"] == "h2" and runs[0]["status"] == "FINISHED"
        assert runs[0]["type"] == "SP"
        assert runs[0]["energy"] is not None

        # --- sort the Run folder into the general workspace folders
        operations = sort_core.build_operations(run_dir, workspace)
        sort_core.ensure_project_folders(workspace)
        counts = sort_core.execute_operations(operations, run_dir)
        print(f"sort counts: {counts}")
        assert counts["moved"] >= 2  # h2.inp and h2.out at least
        assert os.path.isfile(os.path.join(workspace, "input_files", "h2.inp"))
        assert os.path.isfile(os.path.join(workspace, "output_files", "h2.out"))

    print("E2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())

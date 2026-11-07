"""Monitor core tests against synthetic .out files."""

import os
import sys
import time

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_calculator import monitor_core

OPT_FREQ_OUT = """\
                             * O   R   C   A *
# GEOMETRY OPTIMIZATION CYCLE 3
SCF CONVERGED AFTER 8 CYCLES

The Optimization has converged, yey:
                    .---------.
ConvergenceCheck (...)=> Success:                    .---------.
*** GEOMETRY OPTIMIZATION IN DEGREE OF FREEDOM   ***

THE OPTIMIZATION HAS CONVERGED

                       .--------------------.
                       |      OPTCYC 3      |
Energy change         -0.0000002    0.000001  YES
RMS gradient           0.0000003    0.000001  YES
MAX gradient           0.0000004    0.000002  YES

FINAL SINGLE POINT ENERGY     -76.026765219030
VIBRATIONAL FREQUENCIES
   0:         0.00 cm**-1
   1:      1050.33 cm**-1
   2:      3600.12 cm**-1
Zero point energy:    0.021103 Eh
TOTAL RUN TIME: 0:00:03.4
ORCA TERMINATED NORMALLY
"""

# Step 1 converged, step 2 is mid-optimization: the step-1 CONVERGENCE banner
# must not leak into the running step's view (criteria all NO there)
SCAN_STEP2_RUNNING = """\
                             * O   R   C   A *
RELAXED SURFACE SCAN STEP   1
GEOMETRY OPTIMIZATION CYCLE 6
Energy change         -0.0000001    0.000005  YES
RMS gradient           0.0000001    0.000100  YES
MAX gradient           0.0000001    0.000300  YES
THE OPTIMIZATION HAS CONVERGED
SCF CONVERGED AFTER 12 CYCLES
RELAXED SURFACE SCAN STEP   2
GEOMETRY OPTIMIZATION CYCLE 1
Energy change         -0.0000313    0.000005  NO
RMS gradient           0.0001180    0.000100  NO
MAX gradient           0.0005900    0.000300  NO
RMS step               0.0138000    0.002000  NO
MAX step               0.0942000    0.004000  NO
SCF CONVERGED AFTER 14 CYCLES
FINAL SINGLE POINT ENERGY     -1740.0699150000
"""

SCAN_INP = (
    "! B3LYP def2-SVP Opt\n"
    "%geom\n"
    "  Scan\n"
    "    B 0 1 = 1.4, 2.0, 15\n"
    "  end\n"
    "end\n"
)


def make_run(folder, name, out_text, inp_text="! B3LYP def2-SVP OPT FREQ"):
    (folder / f"{name}.inp").write_text(inp_text)
    (folder / f"{name}.out").write_text(out_text)
    return folder / f"{name}.out"


def test_finished_run(tmp_path):
    out = make_run(tmp_path, "water_opt", OPT_FREQ_OUT)
    run = monitor_core.analyze_run(out)

    assert run is not None
    assert run["status"] == "FINISHED"
    assert run["type"] == "OPT+FREQ"
    assert run["converged"] is True
    assert run["energy"] == "-76.026765219030"
    assert run["scf_cycles"] == "8"
    assert run["cycles"] == 3
    assert len(run["criteria"]) == 3
    assert run["imaginary"] == []
    assert "0:00:03" in run["runtime"]


def test_fatal_error(tmp_path):
    out = make_run(tmp_path, "crash_test", "some output\nORCA finished by accident\n")
    run = monitor_core.analyze_run(out)
    assert run["status"] == "FAIL"


def test_fresh_running_file(tmp_path):
    # A file touched right now without the termination banner counts as RUN
    out = make_run(tmp_path, "fresh_job", "starting calculation...\n")
    os.utime(out, (time.time(), time.time()))
    run = monitor_core.analyze_run(out)
    assert run["status"] == "RUNNING"


def test_collect_sorts_running_first(tmp_path):
    make_run(tmp_path, "done_job", OPT_FREQ_OUT)
    fresh = make_run(tmp_path, "live_job", "working...\n")
    os.utime(fresh, (time.time(), time.time()))

    runs = monitor_core.collect_runs([tmp_path])

    assert len(runs) == 2
    assert runs[0]["name"] == "live_job"
    assert runs[0]["status"] == "RUNNING"
    assert runs[1]["name"] == "done_job"
    assert runs[1]["status"] == "FINISHED"


def test_progress_text_by_job_type():
    from orca_calculator.monitor_tab import _progress_text

    opt = {
        "type": "OPT", "status": "FINISHED", "cycles": 12,
        "criteria": ([{"item": "Energy change", "value": "-1e-06",
                       "tol": "1e-06", "ok": True}] * 4
                     + [{"item": "MAX step", "value": "2e-03",
                         "tol": "1e-03", "ok": False}]),
        "cpscf_done": None, "cpscf_total": None,
        "batch_n": 0, "batches_estimated": None, "imaginary": [], "freqs": [],
        "scan_steps": 0, "scan_total": None, "goat_iters": 0,
        "goat_conformers": None,
        "scf_failed": False, "scf_cycles": None, "scf_iter": None,
        "scf_maxiter": None,
    }
    assert _progress_text(opt) == "cycle 12 · 4/5 crit"

    freq = dict(opt, type="FREQ", cycles=0, criteria=[], freqs=[1050.0, 3600.0])
    assert _progress_text(freq) == "2 freqs"

    scan = dict(opt, type="SCAN", scan_steps=7, scan_total=21)
    assert _progress_text(scan) == "step 7/21"

    hess = dict(
        opt, type="FREQ", cycles=0, criteria=[], cpscf_done=84, cpscf_total=90,
    )
    assert _progress_text(hess) == "hess 84/90"

    # An SP's whole progress is its SCF
    sp_done = dict(opt, type="SP", scf_cycles="12")
    assert _progress_text(sp_done) == "conv 12 cyc"
    sp_live = dict(opt, type="SP", status="RUNNING", scf_iter=37,
                   scf_maxiter=100)
    assert _progress_text(sp_live) == "SCF 37/100"

    # OPT+FREQ carries its SCF inside Progress too (no separate column)
    combo = dict(opt, type="OPT+FREQ", scf_cycles="5", cpscf_done=96,
                 cpscf_total=96)
    assert _progress_text(combo) == "cycle 12 · 4/5 crit · hess 96/96 · conv 5 cyc"

    # A run just started shows something instead of an empty cell
    fresh = dict(opt, cycles=0, criteria=[], status="RUNNING")
    assert _progress_text(fresh) == "starting…"

    # the detail block mirrors the original terminal monitor: summary line,
    # aligned criteria rows and the frequency stage checklist
    detail = monitor_core.render_detail(dict(
        opt, name="job_x", path="/tmp/x.out", energy="-76.0", status="RUNNING",
        zpe=None, g_final=None, s_corr=None, imaginary=[],
        scf_criteria=[], cpscf_iter=None, cpscf_done=None, cpscf_total=None,
        cpscf_err=None, cpscf_seconds=0.0, cpscf_tol=None, cpscf_solved=None,
        goat_lowest=None, goat_best=None, goat_sconf=None, goat_conformers=None,
        scf_failed=False, converged=None, substeps_n=0, substeps_seconds=0.0,
        batch_n=0, substeps_batch=None, batches_estimated=None,
        hessian_active=False, freqs=[], runtime=None,
        scan_steps=0, scan_total=None, goat_iters=0, cycles=12,
        scf_cycles=None, scf_iter=None,
    ))
    assert "job_x  (OPT)   RUNNING   cycle 12" in detail
    # whitespace-normalized: the exact column padding is checked visually
    flat = " ".join(detail.split())
    assert "Energy change -1.00e-06 tol 1.0e-06 YES" in flat
    assert "MAX step 2.00e-03 tol 1.0e-03 NO" in flat
    assert "no criteria table yet" not in flat  # criteria were shown


def _checklist_run(**overrides):
    """A mid-run OPT+FREQ record; override fields per scenario."""
    run = {
        "name": "freqjob", "path": "/tmp/f.out", "type": "OPT+FREQ",
        "status": "RUNNING", "cycles": 8, "converged": True,
        "criteria": [], "energy": "-1741.3402914", "zpe": None,
        "g_final": None, "s_corr": None, "imaginary": [], "freqs": [],
        "scan_steps": 0, "scan_total": None, "goat_iters": 0,
        "goat_lowest": None, "goat_best": None, "goat_sconf": None,
        "goat_conformers": None, "scf_cycles": "6", "scf_iter": None,
        "scf_failed": False, "scf_criteria": [],
        "hessian_active": False, "substeps_n": 0, "substeps_seconds": 0.0,
        "batch_n": 0, "substeps_batch": None, "batches_estimated": None,
        "cpscf_iter": None, "cpscf_err": None, "cpscf_done": None,
        "cpscf_total": None, "cpscf_seconds": 0.0, "cpscf_tol": None,
        "cpscf_solved": None, "runtime": None,
    }
    run.update(overrides)
    return run


def test_render_detail_frequency_checklist():
    # --- mid derivative integrals: batches advancing, CP-SCF still pending
    detail = monitor_core.render_detail(_checklist_run(
        hessian_active=True, substeps_n=24, substeps_seconds=720.0,
        batch_n=2, substeps_batch=6, batches_estimated=5,
        cpscf_tol="1e-06",
    ))
    assert "GEOM READY" in detail            # geometry converged, still running
    assert "Frequency calculation (Hessian)" in detail
    assert "[.] Derivative integrals" in detail
    assert "batch 2/5" in detail
    assert "6/18 sub-steps" in detail        # batch size inferred: 18/1 closed
    assert "12.0 min" in detail
    assert "[ ] CP-SCF equations" in detail
    assert "tol 1.0e-06" in detail
    assert "[ ] Diagonalization and frequencies" in detail
    assert "[ ] Thermochemistry" in detail

    # --- mid CP-SCF: integrals done, equations iterating with an ETA
    detail = monitor_core.render_detail(_checklist_run(
        hessian_active=True, substeps_n=30, substeps_seconds=900.0,
        cpscf_iter=4, cpscf_err="3.7e-05", cpscf_done=84, cpscf_total=90,
        cpscf_seconds=84.2, cpscf_tol="1e-06",
    ))
    assert "[x] Derivative integrals" in detail
    assert "30/30 sub-steps" in detail
    assert "[.] CP-SCF equations" in detail
    assert "err 3.7e-05 (tol 1.0e-06)" in detail
    assert "84/90 rhs" in detail
    assert "min left" in detail
    assert "[ ] Diagonalization and frequencies" in detail


def test_scan_convergence_is_step_aware(tmp_path):
    """A CONVERGENCE banner from step 1 must not leak into step 2's view."""
    out = make_run(tmp_path, "scan_job", SCAN_STEP2_RUNNING, inp_text=SCAN_INP)
    os.utime(out, (time.time(), time.time()))
    run = monitor_core.analyze_run(out)

    assert run["type"] == "SCAN"
    assert run["status"] == "RUNNING"
    assert run["scan_steps"] == 2
    assert run["scan_total"] == 15
    assert run["converged"] is None  # step 2 is mid-run: neither done nor failed
    assert run["criteria"] and all(not c["ok"] for c in run["criteria"])

    detail = monitor_core.render_detail(run)
    assert "GEOM READY" not in detail
    assert "declared CONVERGENCE" not in detail


def test_scan_finished_when_last_step_converges(tmp_path):
    done = (SCAN_STEP2_RUNNING
            + "THE OPTIMIZATION HAS CONVERGED\n"
              "ORCA TERMINATED NORMALLY\n")
    out = make_run(tmp_path, "scan_done", done, inp_text=SCAN_INP)
    old = time.time() - 3600
    os.utime(out, (old, old))
    run = monitor_core.analyze_run(out)

    assert run["status"] == "FINISHED"
    assert run["converged"] is True
    assert "CONVERGENCE" in monitor_core.render_detail(run)


OPTTS_DONE = """\
                             * O   R   C   A *
Following TS mode number:   1
GEOMETRY OPTIMIZATION CYCLE 4
Energy change         -0.0000001    0.000005  YES
RMS gradient           0.0000001    0.000100  YES
THE OPTIMIZATION HAS CONVERGED
CP-SCF equations solved in   85.3 sec
VIBRATIONAL FREQUENCIES
   0:      -512.30 cm**-1
   1:      1050.33 cm**-1
   2:      3600.12 cm**-1
Zero point energy:    0.021103 Eh
TOTAL RUN TIME: 0:02:10.5
ORCA TERMINATED NORMALLY
"""


def test_detect_optts_type(tmp_path):
    # the input keyword wins ("OPTTS" contains "OPT": must not pass as OPT+FREQ)
    out = make_run(tmp_path, "ts1", OPTTS_DONE,
                   inp_text="! B3LYP def2-SVP OptTS Freq")
    run = monitor_core.analyze_run(out)
    assert run["type"] == "OPTTS+FREQ"
    assert run["hessians_done"] == 1

    # and without the input at hand, the output banner gives it away
    out = make_run(tmp_path, "ts2", OPTTS_DONE, inp_text="! B3LYP def2-SVP")
    assert monitor_core.analyze_run(out)["type"] == "OPTTS+FREQ"

    plain = OPTTS_DONE.replace("VIBRATIONAL FREQUENCIES\n", "")
    out = make_run(tmp_path, "ts3", plain, inp_text="! B3LYP def2-SVP OptTS")
    assert monitor_core.analyze_run(out)["type"] == "OPTTS"


def test_optts_checklist_and_ts_verdict(tmp_path):
    out = make_run(tmp_path, "ts_ok", OPTTS_DONE,
                   inp_text="! B3LYP def2-SVP OptTS Freq")
    old = time.time() - 3600
    os.utime(out, (old, old))
    run = monitor_core.analyze_run(out)

    assert run["status"] == "FINISHED"
    detail = monitor_core.render_detail(run)
    assert "Transition state search (OptTS)" in detail
    # exactly one imaginary mode: the transition state is confirmed
    assert "TS CONFIRMED" in detail
    assert "imaginary mode: -512.3 cm-1" in detail
    assert "[x] Final frequencies" in detail

    # two imaginary modes: NOT a valid TS, the detail must say so
    two_imag = OPTTS_DONE.replace(
        "   1:      1050.33 cm**-1", "   1:      -300.50 cm**-1")
    out = make_run(tmp_path, "ts_bad", two_imag,
                   inp_text="! B3LYP def2-SVP OptTS Freq")
    os.utime(out, (old, old))
    detail = monitor_core.render_detail(monitor_core.analyze_run(out))
    assert "TS CONFIRMED" not in detail
    assert "2 IMAGINARY" in detail
    assert "(a TS needs exactly 1)" in detail


def test_compact_column_formats():
    from orca_calculator.monitor_tab import _energy_text, _runtime_text

    assert _energy_text({"energy": "-1733.701011700000"}) == "-1733.701012"
    assert _energy_text({"energy": None}) == ""
    assert _runtime_text("0 days 0 hours 1 minutes 12 seconds") == "1 m 12 s"
    assert _runtime_text("0 days 2 hours 5 minutes 0 seconds") == "2 h 05 m"
    assert _runtime_text("1 days 3 hours 0 minutes 0 seconds") == "1 d 03 h"
    assert _runtime_text("0 days 0 hours 0 minutes 45 seconds") == "45 s"
    assert _runtime_text(None) == ""
    assert _runtime_text("weird") == "weird"


def test_selection_survives_refresh(tmp_path):
    """The details panel must not be wiped by the periodic monitor refresh."""
    from PySide6.QtWidgets import QApplication

    from orca_calculator.monitor_tab import MonitorTab

    app = QApplication.instance() or QApplication(sys.argv)
    make_run(tmp_path, "run_a", OPT_FREQ_OUT)
    make_run(tmp_path, "run_b", OPT_FREQ_OUT)
    # Old timestamps: both parse as FINISHED, stable sort order between scans
    old = time.time() - 3600
    for out in tmp_path.glob("*.out"):
        os.utime(out, (old, old))

    tab = MonitorTab(None, None, {})
    tab._fill_table(monitor_core.collect_runs([tmp_path]))
    tab.table.selectRow(1)
    selected_path = str(tab._runs[1]["path"])
    assert tab.details_view.toPlainText().startswith(selected_path)

    # A new scan rebuilds the table; the selection and the panel must persist
    tab._fill_table(monitor_core.collect_runs([tmp_path]))
    assert tab.table.currentRow() == 1
    assert str(tab._runs[tab.table.currentRow()]["path"]) == selected_path
    assert tab.details_view.toPlainText().startswith(selected_path)


def test_results_open_button_gated_by_status(tmp_path):
    """Open is offered only on rows that finished correctly."""
    from PySide6.QtWidgets import QApplication

    from orca_calculator.monitor_tab import MonitorTab

    app = QApplication.instance() or QApplication(sys.argv)
    make_run(tmp_path, "done_job", OPT_FREQ_OUT)
    running = make_run(tmp_path, "live_job", "half written output...\n")
    os.utime(running, (time.time(), time.time()))
    old = time.time() - 3600
    os.utime(tmp_path / "done_job.out", (old, old))

    tab = MonitorTab(None, None, {})
    tab._fill_table(monitor_core.collect_runs([tmp_path]))

    last_column = tab.table.columnCount() - 1
    statuses = {run["name"]: run["status"] for run in tab._runs}
    for row, run in enumerate(tab._runs):
        widget = tab.table.cellWidget(row, last_column)
        if run["status"] == "FINISHED":
            assert widget is not None and widget.text() == "Open"
        else:
            assert widget is None
    assert statuses["done_job"] == "FINISHED"
    assert statuses["live_job"] == "RUNNING"

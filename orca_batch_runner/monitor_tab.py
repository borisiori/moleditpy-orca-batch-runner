"""Monitor tab: status table of running and finished jobs, scanned in background."""

import logging
import re
import sys
import threading
from datetime import datetime

from PySide6.QtCore import QProcess, QTimer
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import config, monitor_core

logger = logging.getLogger(__name__)

REFRESH_SECONDS = 10

STATUS_COLORS = {
    "RUNNING": QColor("#b8860b"),
    "FINISHED": QColor("#2e7d32"),
    "FAIL": QColor("#c62828"),
    "STALE": QColor("#8e24aa"),
}

COLUMNS = ("Status", "Name", "Type", "Progress", "Energy (Eh)",
           "Total runtime", "Results")


class MonitorTab(QWidget):
    def __init__(self, parent_dialog, context, settings):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.context = context
        self.settings = settings
        self.workspace = ""
        self._scan_folders = ()
        self._scan_thread = None
        self._scan_result = None
        self._scan_series = 0
        self._paused = False
        self._runs = []

        self.setup_ui()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.timeout.connect(self._start_scan)
        self._refresh_timer.start(REFRESH_SECONDS * 1000)
        # The scan runs in a thread; this timer picks up its result
        self._collect_timer = QTimer(self)
        self._collect_timer.timeout.connect(self._apply_scan_if_ready)
        self._collect_timer.start(250)
        QTimer.singleShot(0, self._start_scan)  # first scan right away

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self.status_label = QLabel("Not scanned yet")
        row.addWidget(self.status_label)
        row.addStretch()
        self.pause_button = QPushButton("Pause auto-refresh")
        self.pause_button.setToolTip(
            "Freeze the periodic scan (no calculation is stopped)"
        )
        self.pause_button.clicked.connect(self._toggle_pause)
        row.addWidget(self.pause_button)
        refresh_now = QPushButton("Refresh")
        refresh_now.clicked.connect(self._start_scan)
        row.addWidget(refresh_now)
        layout.addLayout(row)

        self.table = QTableWidget(0, len(COLUMNS))
        self.table.setHorizontalHeaderLabels(list(COLUMNS))
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        # Columns size to their content so nothing gets cut off; the name
        # column (max 30 rows per scan) absorbs the remaining width
        header.setSectionResizeMode(QHeaderView.ResizeMode.ResizeToContents)
        header.setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(len(COLUMNS) - 1, QHeaderView.ResizeMode.Fixed)
        self.table.setColumnWidth(len(COLUMNS) - 1, 86)  # fits the Open button
        self.table.itemSelectionChanged.connect(self._show_selected_details)
        layout.addWidget(self.table, 1)

        # Full detail of the selected row, always visible (hover also works)
        self.details_view = QPlainTextEdit()
        self.details_view.setReadOnly(True)
        self.details_view.setMaximumHeight(170)
        self.details_view.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        details_font = self.details_view.font()
        details_font.setFamily("Monospace")
        self.details_view.setFont(details_font)
        self.details_view.setPlaceholderText(
            "Select a row to see its full detail "
            "(criteria, SCF convergence, energies, frequencies)."
        )
        layout.addWidget(self.details_view)

    # ------------------------------------------------------------ scanning
    def set_workspace(self, workspace):
        self.workspace = workspace
        if workspace:
            folders = config.workspace_folders(workspace)
            self._scan_folders = (folders["run"], folders["temp"])
        else:
            self._scan_folders = ()
        self._start_scan()

    def _toggle_pause(self):
        self._paused = not self._paused
        self.pause_button.setText(
            "Resume auto-refresh" if self._paused else "Pause auto-refresh"
        )
        if self._paused:
            self.status_label.setText("Auto-refresh paused")

    def _start_scan(self):
        """Scan in a BACKGROUND THREAD: reading the .out tails of large runs
        froze the window when done on the GUI thread."""
        if self._paused or not self._scan_folders:
            return
        if self._scan_thread is not None and self._scan_thread.is_alive():
            self.status_label.setText("Scanning…")  # previous scan still running
            return
        self._scan_series += 1
        series = self._scan_series
        folders = tuple(str(f) for f in self._scan_folders)

        def work():
            try:
                runs = monitor_core.collect_runs(folders)
            except Exception as e:  # noqa: BLE001 -- the monitor must never crash the GUI
                logger.warning("scan failed: %s", e)
                runs = []
            self._scan_result = (series, runs)

        self._scan_thread = threading.Thread(target=work, daemon=True)
        self._scan_thread.start()

    def _apply_scan_if_ready(self):
        """Pick up (on the GUI thread) the newest finished scan."""
        if self._scan_result is None:
            return
        series, runs = self._scan_result
        self._scan_result = None
        if series != self._scan_series:
            return  # stale result: a newer scan is already on its way
        self._fill_table(runs)
        self.status_label.setText(
            f"Updated {datetime.now().strftime('%H:%M:%S')}"
            + ("  ·  paused" if self._paused else "")
        )

    # ------------------------------------------------------------- table
    def _fill_table(self, runs):
        # Keep what the user is reading stable across refreshes: remember the
        # selected run and the scroll position before rebuilding the table
        selected_key = None
        current = self.table.currentRow()
        if 0 <= current < len(self._runs):
            selected_key = str(self._runs[current]["path"])
        scroll = self.table.verticalScrollBar().value()

        # Signals stay blocked while emptying: clearing the selection would
        # otherwise wipe the details panel the user may be reading
        self.table.blockSignals(True)
        self._runs = runs
        self.table.setRowCount(0)
        for run in runs:
            row = self.table.rowCount()
            self.table.insertRow(row)
            self._set_item(row, 0, run["status"], status_color(run["status"]))
            self._set_item(row, 1, run["name"])
            self._set_item(row, 2, run["type"])
            self._set_item(row, 3, _progress_text(run))
            self._set_item(row, 4, _energy_text(run))
            self._set_item(row, 5, _runtime_text(run["runtime"]))
            # Open is offered only for jobs that finished correctly
            if run["status"] == "FINISHED":
                self.table.setCellWidget(row, len(COLUMNS) - 1,
                                         self._open_button(run))
            # The hover tooltip works anywhere on the row, not just the name
            tip = monitor_core.render_detail(run)
            for column in range(self.table.columnCount()):
                item = self.table.item(row, column)
                if item is not None:
                    item.setToolTip(tip)
        if selected_key is not None:
            for row, run in enumerate(self._runs):
                if str(run["path"]) == selected_key:
                    self.table.selectRow(row)  # signals blocked: no flicker
                    break
        self.table.blockSignals(False)
        self.table.verticalScrollBar().setValue(
            min(scroll, self.table.verticalScrollBar().maximum())
        )
        # Refresh the panel content (same run, updated numbers) without
        # losing it when nothing is selected
        self._show_selected_details()

    def _show_selected_details(self):
        """Full detail of the selected row in the panel under the table."""
        row = self.table.currentRow()
        if 0 <= row < len(self._runs):
            self.details_view.setPlainText(monitor_core.render_detail(self._runs[row]))
        else:
            self.details_view.clear()

    def _open_button(self, run):
        """Open this .out in a fresh MoleditPy session when the button fires."""
        path = str(run["path"])
        button = QPushButton("Open")
        button.setToolTip(
            "Open this .out in a new MoleditPy session.\n"
            "Routed to the ORCA Result Analyzer plugin if installed."
        )
        button.clicked.connect(lambda _checked=False, p=path: open_in_new_session(p))
        return button

    def _set_item(self, row, column, text, color=None):
        item = QTableWidgetItem(str(text))
        if color is not None:
            item.setForeground(QBrush(color))
        self.table.setItem(row, column, item)


def status_color(status):
    return STATUS_COLORS.get(status)


def open_in_new_session(out_path):
    """Launch `moleditpy <file.out>` as a detached process.

    A separate session is deliberate: the analyzer's file opener syncs the
    main window to the loaded file, so opening in-process would replace the
    molecule the user is editing. The CLI path routes .out files through the
    plugin file openers, landing in the ORCA Result Analyzer when installed.
    """
    started = QProcess.startDetached(sys.executable, ["-m", "moleditpy", out_path])
    if not started:
        logger.warning("could not launch a new MoleditPy session for %s", out_path)


def _energy_text(run):
    """Six decimals fit the column; the full value stays in the detail view."""
    raw = run.get("energy")
    if not raw:
        return ""
    try:
        return f"{float(raw):.6f}"
    except ValueError:
        return raw


def _runtime_text(runtime):
    """Compact ORCA's '0 days 0 hours 1 minutes 12 seconds' table format."""
    if not runtime:
        return ""
    numbers = [
        int(n)
        for n in re.findall(r"(\d+)\s*(?:days?|hours?|minutes?|seconds?)", runtime)
    ]
    if len(numbers) < 4:
        return runtime  # unexpected format: show it as is
    days, hours, minutes, seconds = numbers[:4]
    if days:
        return f"{days} d {hours:02d} h"
    if hours:
        return f"{hours} h {minutes:02d} m"
    if minutes:
        return f"{minutes} m {seconds:02d} s"
    return f"{seconds} s"


def _scf_short(run):
    """The SCF state in a few words (the whole progress of an SP job)."""
    if run["scf_failed"]:
        return "SCF NOT CONVERGED"
    if run["scf_cycles"]:
        return f"conv {run['scf_cycles']} cyc"
    if run["scf_iter"] is not None:
        total = f"/{run['scf_maxiter']}" if run["scf_maxiter"] else ""
        return f"SCF {run['scf_iter']}{total}"
    return ""


def _progress_text(run):
    """One line telling how far along a job is, per job type."""
    job_type = run["type"]
    parts = []
    if job_type.startswith("SCAN"):
        total = f"/{run['scan_total']}" if run["scan_total"] else ""
        parts.append(f"step {run['scan_steps']}{total}")
    elif job_type == "GOAT":
        parts.append(f"iter {run['goat_iters']}")
        if run["goat_conformers"]:
            parts.append(f"{run['goat_conformers']} conf")
    elif job_type == "SP":
        # An SP has no geometry or Hessian: the SCF is all the progress
        parts.append(_scf_short(run))
    else:
        if run["cycles"]:
            parts.append(f"cycle {run['cycles']}")
        # Converged criteria count: all YES means the geometry step is done
        if run["criteria"]:
            ok = sum(1 for c in run["criteria"] if c["ok"])
            parts.append(f"{ok}/{len(run['criteria'])} crit")
    # Hessian progress: CP-SCF iterations, or the integrals batches before them
    if run["cpscf_done"] is not None:
        total = run["cpscf_total"] if run["cpscf_total"] else "?"
        parts.append(f"hess {run['cpscf_done']}/{total}")
    elif run["batch_n"]:
        estimated = run["batches_estimated"]
        suffix = f"/~{estimated}" if estimated else ""
        parts.append(f"batch {run['batch_n']}{suffix}")
    if job_type != "SP" and job_type != "GOAT":
        scf = _scf_short(run)
        if scf:
            parts.append(scf)
    if run["imaginary"]:
        parts.append(f"{len(run['imaginary'])} imag")
    if not parts and run["freqs"]:
        parts.append(f"{len(run['freqs'])} freqs")
    if not parts and run["status"] == "RUNNING":
        parts.append("starting…")
    return " · ".join(parts)

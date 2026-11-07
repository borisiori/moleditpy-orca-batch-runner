"""Run tab: live ORCA output plus stop, stop-after-current and retry."""

import logging
from datetime import datetime

from PySide6.QtCore import QTimer
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import (
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

logger = logging.getLogger(__name__)

# Keep the console bounded on very long runs (the full log stays in each .out)
MAX_CONSOLE_BLOCKS = 20000


class RunTab(QWidget):
    def __init__(self, parent_dialog, context, settings):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.context = context
        self.settings = settings
        self.failed_paths = []
        self._job_start = None
        self._job_label = ""

        self.setup_ui()

        # 1 s ticker for the running elapsed time
        self._ticker = QTimer(self)
        self._ticker.timeout.connect(self._tick)

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        row = QHBoxLayout()
        self.status_label = QLabel("No active run")
        row.addWidget(self.status_label)
        row.addStretch()

        self.stop_button = QPushButton("Stop")
        self.stop_button.setEnabled(False)
        self.stop_button.setToolTip(
            "Stop the queue now: terminates the whole process tree\n"
            "(ORCA and its helper processes)"
        )
        self.stop_button.clicked.connect(self.parent_dialog.stop_queue)
        row.addWidget(self.stop_button)

        self.stop_after_button = QPushButton("Stop after current")
        self.stop_after_button.setEnabled(False)
        self.stop_after_button.setToolTip(
            "Graceful stop: the file running NOW finishes normally\n"
            "and the rest of the queue is not started"
        )
        self.stop_after_button.clicked.connect(
            self.parent_dialog.stop_after_current
        )
        row.addWidget(self.stop_after_button)

        self.retry_button = QPushButton("Retry failed")
        self.retry_button.setEnabled(False)
        self.retry_button.setToolTip("Re-run only the files that ended with errors")
        self.retry_button.clicked.connect(self.parent_dialog.retry_failed)
        row.addWidget(self.retry_button)

        save_button = QPushButton("Save log…")
        save_button.clicked.connect(self._save_log)
        row.addWidget(save_button)

        clear_button = QPushButton("Clear")
        clear_button.clicked.connect(self.clear_console)
        row.addWidget(clear_button)
        layout.addLayout(row)

        self.console = QPlainTextEdit()
        self.console.setReadOnly(True)
        self.console.setMaximumBlockCount(MAX_CONSOLE_BLOCKS)
        self.console.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = QFont("Monospace")
        font.setPointSizeF(9.5)
        self.console.setFont(font)
        self.console.setPlaceholderText(
            "ORCA output appears here when a queue is launched."
        )
        layout.addWidget(self.console, 1)

    # ----------------------------------------------------------- logging
    def log_line(self, text):
        """Plugin-authored line (appendPlainText adds the newline)."""
        self.console.appendPlainText(text)

    def log_append(self, text):
        """Streamed ORCA chunk, inserted verbatim (no newline is added)."""
        cursor = self.console.textCursor()
        cursor.movePosition(cursor.MoveOperation.End)
        cursor.insertText(text)
        bar = self.console.verticalScrollBar()
        bar.setValue(bar.maximum())

    def clear_console(self):
        self.console.clear()

    def _save_log(self):
        suggested = "orca_queue_" + datetime.now().strftime("%Y%m%d_%H%M") + ".log"
        path, _ = QFileDialog.getSaveFileName(
            self, "Save run log", suggested, "Log files (*.log);;All files (*)"
        )
        if not path:
            return
        try:
            with open(path, "w", encoding="utf-8") as f:
                f.write(self.console.toPlainText())
            self.status_label.setText(f"Log saved to {path}")
        except OSError as e:
            self.status_label.setText(f"Could not save log: {e}")

    # ------------------------------------------------------------- events
    def on_job_started(self, name, index, total):
        self._job_start = datetime.now()
        self._job_label = f"[{index}/{total}] {name}"
        self.log_line(f"{self._job_label} — RUNNING  "
                      f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
        self._tick()
        self._ticker.start(1000)
        self.stop_after_button.setEnabled(True)

    def on_job_finished(self, name, ok, message):
        self._ticker.stop()
        duration = ""
        if self._job_start is not None:
            seconds = int((datetime.now() - self._job_start).total_seconds())
            minutes, secs = divmod(seconds, 60)
            duration = f"  (t: {minutes}:{secs:02d})"
        if ok:
            status = "FINISHED"
        elif message == "stopped by user":
            status = "STOPPED"
        else:
            status = "ERROR DETECTED"
        line = f"{name} — {status}{duration}"
        if message and not ok:
            line += f"  ·  {message}"
        self.log_line(line)

    def on_queue_finished(self, results):
        failed = [r for r in results if not r["ok"]]
        done = len(results) - len(failed)
        self.failed_paths = [r["path"] for r in failed]
        self.retry_button.setEnabled(bool(self.failed_paths))
        if self.retry_button.isEnabled():
            self.retry_button.setToolTip(
                "Retry:\n" + "\n".join("· " + r["name"] for r in failed)
            )
        self.status_label.setText(
            f"Queue finished — {done} ok, {len(failed)} failed"
        )
        # Big final banner, in the style of the original launcher console
        if results and not failed:
            self._banner("ALL FILES IN QUEUE FINISHED SUCCESSFULLY")
        elif results:
            self._banner(f"QUEUE FINISHED — {done} OK, {len(failed)} FAILED")
            for r in failed[:10]:
                self.log_line(f"      FAILED: {r['name']}")
            if len(failed) > 10:
                self.log_line(f"      ... and {len(failed) - 10} more")
        else:
            self._banner("QUEUE STOPPED")

    def _console_width_chars(self):
        """Console width in monospace characters (for the separators)."""
        metrics = QFontMetrics(self.console.font())
        step = max(1, metrics.horizontalAdvance("MMMMMMMMMM") // 10)
        return max(60, min(120, self.console.viewport().width() // step))

    def _banner(self, text, mark="="):
        """Double-line banner with a centered title, launcher-console style."""
        width = self._console_width_chars()
        line = mark * width
        self.log_line("")
        self.log_line(line)
        self.log_line(line)
        self.log_line(text.center(width))
        self.log_line(line)
        self.log_line(line)

    def on_stop_requested(self):
        self.status_label.setText("Stopping the process tree…")
        self.stop_button.setEnabled(False)
        self.stop_after_button.setEnabled(False)

    def on_stop_after_set(self):
        self.stop_after_button.setEnabled(False)
        self.status_label.setText(
            "Stop scheduled: the current file finishes, the next ones are skipped."
        )

    def _tick(self):
        if self._job_start is None:
            return
        seconds = int((datetime.now() - self._job_start).total_seconds())
        minutes, secs = divmod(seconds, 60)
        self.status_label.setText(
            f"Running · {self._job_label} · {minutes}:{secs:02d}"
        )

    # -------------------------------------------------------------- state
    def set_running(self, running):
        self.stop_button.setEnabled(running)
        self.stop_after_button.setEnabled(running)
        if not running:
            self._ticker.stop()

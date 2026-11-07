"""Main dialog: ORCA setup bar plus the queue management tabs."""

import logging

from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from . import config
from .runner import QueueRunner

logger = logging.getLogger(__name__)

# Local Imports
try:
    from .queue_tab import QueueTab
    from .run_tab import RunTab
    from .monitor_tab import MonitorTab
    from .sort_tab import SortTab
    from .delete_tab import DeleteTab
except ImportError:  # pragma: no cover - broken install, keep the dialog usable
    QueueTab = RunTab = MonitorTab = SortTab = DeleteTab = None


class OrcaCalculatorDialog(QDialog):
    def __init__(self, parent=None, context=None, settings=None, version=None):
        super().__init__(parent)
        self.context = context
        self.settings = settings if settings is not None else {}
        self.version = version
        self.closing = False

        # Machine-level paths (binary + workspace), shared by every tab
        self.binary, self.workspace = config.load_paths(context)

        self.runner = QueueRunner(self)
        self.runner.job_output.connect(self.log)

        title = "ORCA Calculator"
        if version:
            title += f" v{version}"
        self.setWindowTitle(title)
        self.resize(1020, 720)

        self.setup_ui()
        # Prefill with what load_paths detected/saved, so the bar is ready on
        # open; Browse/Detect stay as the fallback when detection comes up empty
        self.binary_edit.setText(self.binary)
        self.workspace_edit.setText(self.workspace)
        self._wire_runner()
        self.apply_paths(save=False)
        self.load_settings()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)

        layout.addWidget(self._build_setup_group())

        self.tabs = QTabWidget()
        tab_classes = (
            ("Queue", QueueTab),
            ("Running", RunTab),
            ("Monitor", MonitorTab),
            ("Sort", SortTab),
            ("Delete", DeleteTab),
        )
        for label, tab_class in tab_classes:
            if tab_class is None:
                placeholder = QWidget()
                placeholder.setLayout(QVBoxLayout())
                placeholder.layout().addWidget(QLabel(f"{label} tab failed to load."))
                self.tabs.addTab(placeholder, label)
            else:
                self.tabs.addTab(tab_class(self, self.context, self.settings), label)
        layout.addWidget(self.tabs, 1)

    def _run_tab(self):
        return self.tabs.widget(1)

    def _wire_runner(self):
        runner = self.runner
        runner.job_started.connect(
            lambda *args: self._call_run_tab("on_job_started", *args)
        )
        runner.job_finished.connect(
            lambda *args: self._call_run_tab("on_job_finished", *args)
        )
        runner.queue_finished.connect(self._on_queue_finished)

    def _call_run_tab(self, method, *args):
        tab = self._run_tab()
        if hasattr(tab, method) and not self.closing:
            getattr(tab, method)(*args)

    # ------------------------------------------------------------- queue
    def start_queue(self, paths):
        """Entry point used by the Queue tab's Launch button."""
        if self.runner.is_running():
            QMessageBox.warning(self, "ORCA Calculator", "A queue is already running.")
            return
        ok, message = config.validate_binary(self.binary)
        if not ok:
            QMessageBox.warning(
                self,
                "ORCA Calculator",
                f"The ORCA executable is not usable ({message}).\n"
                "Fix it in the ORCA Setup section above.",
            )
            return
        if not self.workspace:
            QMessageBox.warning(
                self, "ORCA Calculator", "Set the workspace folder first."
            )
            return
        try:
            config.ensure_workspace_layout(self.workspace)
        except OSError as e:
            QMessageBox.critical(
                self, "ORCA Calculator", f"Cannot create workspace folders:\n{e}"
            )
            return

        self.tabs.setCurrentIndex(1)  # Run tab
        self._call_run_tab("clear_console")
        self._set_running(True)
        if not self.runner.start(self.binary, self.workspace, paths):
            self._set_running(False)

    def stop_queue(self):
        """Hard stop, with confirmation (kills ORCA mid-run)."""
        if not self.runner.is_running():
            return
        answer = QMessageBox.question(
            self,
            "Stop run",
            "The whole process tree will be stopped (ORCA and its helpers);\n"
            "the current .out file will be left half-written.\n\nStop now?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return
        self._call_run_tab("on_stop_requested")
        self.runner.stop()

    def stop_after_current(self):
        """Graceful stop: the file in progress finishes normally."""
        if not self.runner.is_running():
            return
        self.runner.stop_after_current()
        self._call_run_tab("on_stop_after_set")

    def retry_failed(self):
        run_tab = self._run_tab()
        failed = getattr(run_tab, "failed_paths", [])
        if failed and not self.runner.is_running():
            self.start_queue(failed)

    def _on_queue_finished(self, results):
        self._set_running(False)
        self._call_run_tab("on_queue_finished", results)
        self.update_internal_state()
        if self.context is not None:
            self.context.mark_project_modified()

    def _set_running(self, running):
        """Lock every tab that must not touch the Run folder mid-run."""
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if hasattr(tab, "set_running"):
                tab.set_running(running)
        self.tabs.setTabText(1, "Running ●" if running else "Running")

    def _build_setup_group(self):
        group = QGroupBox("ORCA Setup")
        grid = QGridLayout(group)
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(4)

        grid.addWidget(QLabel("ORCA executable:"), 0, 0)
        self.binary_edit = QLineEdit()
        self.binary_edit.setPlaceholderText("Path to the 'orca' launcher script")
        self.binary_edit.setToolTip(
            "ORCA must be invoked through its full path.\n"
            "Auto-detected from ORCA_BIN, ORCA_HOME, PATH or common folders."
        )
        grid.addWidget(self.binary_edit, 0, 1)

        browse_binary = QPushButton("Browse…")
        browse_binary.clicked.connect(self._browse_binary)
        grid.addWidget(browse_binary, 0, 2)

        detect = QPushButton("Detect")
        detect.setToolTip("Re-run automatic detection (env, PATH, common folders)")
        detect.clicked.connect(self._detect_binary)
        grid.addWidget(detect, 0, 3)

        grid.addWidget(QLabel("Workspace:"), 1, 0)
        self.workspace_edit = QLineEdit()
        self.workspace_edit.setToolTip(
            "Root folder that holds the Run/ and temp/ folders, projects and\n"
            "the input_files/output_files/output_analysis/Aux_files structure."
        )
        grid.addWidget(self.workspace_edit, 1, 1)

        browse_workspace = QPushButton("Browse…")
        browse_workspace.clicked.connect(self._browse_workspace)
        grid.addWidget(browse_workspace, 1, 2)

        self.paths_status = QLabel("")
        grid.addWidget(self.paths_status, 2, 0, 1, 4)

        self.binary_edit.editingFinished.connect(self.apply_paths)
        self.workspace_edit.editingFinished.connect(self.apply_paths)
        return group

    # ------------------------------------------------------------- paths
    def _browse_binary(self):
        path, _ = QFileDialog.getOpenFileName(
            self, "Select the ORCA executable", self.binary_edit.text() or "/opt"
        )
        if path:
            self.binary_edit.setText(path)
            if not self.workspace_edit.text().strip():
                self.workspace_edit.setText(config.default_workspace(path))
            self.apply_paths()

    def _browse_workspace(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select the ORCA workspace", self.workspace_edit.text() or "/opt"
        )
        if path:
            self.workspace_edit.setText(path)
            self.apply_paths()

    def _detect_binary(self):
        found = config.find_orca_binary()
        if found:
            self.binary_edit.setText(found)
            if not self.workspace_edit.text().strip():
                self.workspace_edit.setText(config.default_workspace(found))
            self.apply_paths()
        else:
            QMessageBox.information(
                self,
                "ORCA Calculator",
                "No ORCA executable was found automatically.\n"
                "Set ORCA_BIN, add it to PATH, or use Browse…",
            )

    def apply_paths(self, save=True):
        """Validate the edited paths, persist them and notify the tabs."""
        self.binary = self.binary_edit.text().strip()
        self.workspace = self.workspace_edit.text().strip()

        ok, message = config.validate_binary(self.binary)
        if ok:
            status = f"ORCA: {message}"
        else:
            status = f"ORCA executable {message} — set it to enable launching."
        if self.workspace:
            status += f"   ·   Workspace: {self.workspace}"
        self.paths_status.setText(status)

        if save:
            config.save_paths(self.context, self.binary, self.workspace)
        if self.workspace and ok:
            try:
                config.ensure_workspace_layout(self.workspace)
            except OSError as e:
                logger.warning("could not create workspace folders: %s", e)

        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if hasattr(tab, "set_workspace"):
                tab.set_workspace(self.workspace)

    # ---------------------------------------------------------- settings
    def load_settings(self):
        """Apply project settings on open (values saved with the project)."""
        last_dir = self.settings.get("last_input_dir", "")
        if last_dir:
            for i in range(self.tabs.count()):
                tab = self.tabs.widget(i)
                if hasattr(tab, "set_last_input_dir"):
                    tab.set_last_input_dir(last_dir)

    def update_internal_state(self):
        """Sync tab state into the project settings dict."""
        for i in range(self.tabs.count()):
            tab = self.tabs.widget(i)
            if hasattr(tab, "collect_settings"):
                tab.collect_settings(self.settings)

    def on_document_reset(self):
        self.load_settings()

    # ------------------------------------------------------------ console
    def log(self, text):
        tab = self._run_tab() if self.tabs is not None else None
        if hasattr(tab, "log_append") and not self.closing:
            tab.log_append(text)
        else:
            logger.debug("%s", text)

    # -------------------------------------------------------------- close
    def closeEvent(self, event):
        if self.runner.is_running():
            answer = QMessageBox.question(
                self,
                "Close ORCA Calculator",
                "A queue is running.\n\nClosing will stop the whole process tree\n"
                "(same as stopping the run now). Close and stop?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No,
            )
            if answer != QMessageBox.StandardButton.Yes:
                event.ignore()
                return
            self.runner.stop_sync()
        self.closing = True
        self.update_internal_state()
        super().closeEvent(event)

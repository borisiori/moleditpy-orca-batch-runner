"""Sort tab: preview table of the Run-to-project file moves."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from . import sort_core

logger = logging.getLogger(__name__)

RED = QBrush(QColor("#c62828"))
GREEN = QBrush(QColor("#2e7d32"))
AMBER = QBrush(QColor("#b8860b"))
GRAY = QBrush(QColor("#78909c"))

DECISION_TEXT = {
    "Move": "Move",
    "No move": "Don't move",
    "Overwrite": "Overwrite",
}


class SortTab(QWidget):
    def __init__(self, parent_dialog, context, settings):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.context = context
        self.settings = settings
        self.workspace = ""
        self.operations = []

        self.setup_ui()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel(
            "Move the files in Run to the project folders. Double-click a row "
            "to toggle its decision (Move / Don't move, or Overwrite / Don't "
            "move on conflicts)."
        ))

        mode_row = QHBoxLayout()
        mode_row.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Files — sort each file by type")
        self.mode_combo.addItem("Folders — keep each run together")
        self.mode_combo.setToolTip(
            "Files: every Run file goes to its type subfolder (input_files,\n"
            "output_files, Aux_files/...) using the standard mapping.\n\n"
            "Folders: each per-run folder moves intact to\n"
            "output_files/<name>/ (its .inp goes to input_files/), keeping\n"
            "the run identity together inside the target project."
        )
        self.mode_combo.currentIndexChanged.connect(lambda _i: self.refresh_plan())
        mode_row.addWidget(self.mode_combo)
        mode_row.addStretch()
        layout.addLayout(mode_row)

        project_row = QHBoxLayout()
        project_row.addWidget(QLabel("Target project:"))
        self.project_combo = QComboBox()
        self.project_combo.setMinimumWidth(420)
        self.project_combo.setToolTip(
            "Pick an existing project from the list.\n"
            "For a new one, type its name in the field beside it."
        )
        self.project_combo.currentIndexChanged.connect(lambda _i: self.refresh_plan())
        project_row.addWidget(self.project_combo)

        self.new_project_edit = QLineEdit()
        self.new_project_edit.setPlaceholderText("(optional) new project name…")
        self.new_project_edit.setToolTip(
            "If a name is typed here it becomes the target project instead of "
            "the list choice; folders are created on execution."
        )
        self.new_project_edit.textChanged.connect(lambda _t: self.refresh_plan())
        project_row.addWidget(self.new_project_edit, 1)

        rescan = QPushButton("Rescan")
        rescan.clicked.connect(self.rescan)
        project_row.addWidget(rescan)
        layout.addLayout(project_row)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(QLabel("Run folders to skip (comma-separated):"))
        self.exclude_edit = QLineEdit()
        self.exclude_edit.returnPressed.connect(self.apply_exclusions)
        exclude_row.addWidget(self.exclude_edit, 1)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_exclusions)
        exclude_row.addWidget(apply_button)
        layout.addLayout(exclude_row)

        actions = QHBoxLayout()
        for text, handler in (
            ("Conflicts → Overwrite", lambda: self._decide_conflicts("Overwrite")),
            ("Conflicts → Don't move", lambda: self._decide_conflicts("No move")),
            ("Selection → Don't move", self._no_move_selected),
            ("Select conflicts", self._select_conflicts),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch()
        self.execute_button = QPushButton("Execute plan")
        self.execute_button.setToolTip(
            "Run the marked operations (Move / Overwrite / Delete)"
        )
        self.execute_button.clicked.connect(self.execute_plan)
        actions.addWidget(self.execute_button)
        layout.addLayout(actions)

        self.table = QTableWidget(0, 5)
        self.table.setHorizontalHeaderLabels(
            ["File", "Action", "Destination", "Status", "Decision"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        self.table.cellDoubleClicked.connect(self._toggle_decision)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------ projects
    def set_workspace(self, workspace):
        self.workspace = workspace
        if workspace:
            self.exclude_edit.setText(
                ", ".join(sorted(sort_core.load_excluded_folders(workspace)))
            )
        self.rescan()

    def rescan(self):
        # Mode is a preference persisted with the project settings
        self.mode_combo.setCurrentIndex(
            1 if self.settings.get("sort_mode") == "folders" else 0
        )
        self.project_combo.blockSignals(True)
        self.project_combo.clear()
        if self.workspace:
            self.project_combo.addItem(sort_core.GENERAL_LABEL)
            self.project_combo.addItems(sort_core.discover_projects(self.workspace))
        self.project_combo.blockSignals(False)
        # restore the last used project, or the one saved with the project file
        saved = self.settings.get("sort_project", "")
        index = self.project_combo.findText(saved)
        if index >= 0:
            self.project_combo.setCurrentIndex(index)
        self.refresh_plan()

    def current_base_dir(self):
        if not self.workspace:
            return None
        # A typed new project wins over the combo selection
        project = self.new_project_edit.text().strip() or self.project_combo.currentText()
        return sort_core.resolve_base_folder(self.workspace, project)

    def apply_exclusions(self):
        if not self.workspace:
            return
        names = {p.strip() for p in self.exclude_edit.text().split(",") if p.strip()}
        sort_core.save_excluded_folders(self.workspace, names)
        self.exclude_edit.setText(", ".join(sorted(names)))
        self.refresh_plan()

    # ----------------------------------------------------------- preview
    def folder_mode(self):
        return self.mode_combo.currentIndex() == 1

    def refresh_plan(self):
        run_dir = sort_core.run_dir_of(self.workspace) if self.workspace else None
        base_dir = self.current_base_dir()

        # Keep the user decisions across rescans
        previous = {self._key(op): op.get("decision", "") for op in self.operations}

        self.operations = []
        self.table.setRowCount(0)
        if base_dir is None:
            self.status_label.setText(
                "Invalid project name: use a simple folder name, no slashes."
            )
            return
        if run_dir is None or not run_dir.exists():
            self.status_label.setText("Set the workspace to scan its Run folder.")
            return

        excluded = sort_core.load_excluded_folders(self.workspace)
        if self.folder_mode():
            self.operations = sort_core.build_run_operations(
                run_dir, base_dir, excluded
            )
        else:
            self.operations = sort_core.build_operations(run_dir, base_dir, excluded)
        for op in self.operations:
            saved = previous.get(self._key(op))
            if saved is None or op["type"] not in ("move", "run"):
                continue
            if op["status"] == "Conflict" and saved in {"Overwrite", "No move"}:
                op["decision"] = saved
            elif op["status"] == "Ready" and saved == "No move":
                op["decision"] = saved

        moves = conflicts = 0
        for row, op in enumerate(self.operations):
            self.table.insertRow(row)
            if op["type"] == "run":
                name = op["source"].name + "/"
                if op["inp"] is not None:
                    name += "  (+ .inp)"
                action = "Run folder"
            else:
                name = str(op["source"].relative_to(run_dir))
                action = "Move"
            self._set_item(row, 0, name)
            self._set_item(row, 1, action)
            self._set_item(row, 2, str(op["destination"]))
            status_item = self._set_item(row, 3, op["status"])
            if op["status"] == "Conflict":
                status_item.setForeground(AMBER)
                conflicts += 1
            self._paint_decision(row, op)
            self.table.item(row, 0).setData(Qt.UserRole, op)
            moves += 1

        self.status_label.setText(
            f"Target: {base_dir}  ·  Moves: {moves}  ·  Conflicts: {conflicts}"
        )

    @staticmethod
    def _key(op):
        return (op["type"], str(op["source"]), str(op["target"]))

    def _set_item(self, row, column, text):
        item = QTableWidgetItem(str(text))
        self.table.setItem(row, column, item)
        return item

    def _paint_decision(self, row, op):
        item = QTableWidgetItem(DECISION_TEXT.get(op["decision"], op["decision"]))
        if op["decision"] in {"Overwrite", "Delete"}:
            item.setForeground(RED)
        elif op["decision"] in {"No move", "Keep"}:
            item.setForeground(GRAY)
        else:
            item.setForeground(GREEN)
        self.table.setItem(row, 4, item)

    # ---------------------------------------------------------- decisions
    def _toggle_decision(self, row, _column):
        if not 0 <= row < len(self.operations):
            return
        op = self.operations[row]
        if op["type"] not in ("move", "run"):
            return
        if op["status"] == "Conflict":
            op["decision"] = "Overwrite" if op["decision"] == "No move" else "No move"
        else:
            op["decision"] = "No move" if op["decision"] == "Move" else "Move"
        self._paint_decision(row, op)

    def _decide_conflicts(self, decision):
        changed = 0
        for row in sorted({i.row() for i in self.table.selectedItems()}):
            op = self.operations[row]
            if op["type"] in ("move", "run") and op["status"] == "Conflict":
                op["decision"] = decision
                self._paint_decision(row, op)
                changed += 1
        if not changed:
            QMessageBox.information(
                self, "ORCA Calculator", "No conflict rows in the selection."
            )

    def _no_move_selected(self):
        changed = 0
        for row in sorted({i.row() for i in self.table.selectedItems()}):
            op = self.operations[row]
            if op["type"] in ("move", "run"):
                op["decision"] = "No move"
                self._paint_decision(row, op)
                changed += 1
        if not changed:
            QMessageBox.information(
                self, "ORCA Calculator", "No movable files in the selection."
            )

    def _select_conflicts(self):
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        model = self.table.selectionModel()
        found = False
        for row, op in enumerate(self.operations):
            if op["type"] in ("move", "run") and op["status"] == "Conflict":
                model.select(self.table.model().index(row, 0),
                             model.SelectionFlag.Select | model.SelectionFlag.Rows)
                found = True
        if not found:
            QMessageBox.information(
                self, "ORCA Calculator", "No conflicts in the current preview."
            )

    # ----------------------------------------------------------- execution
    def execute_plan(self):
        if not self.workspace:
            return
        run_dir = sort_core.run_dir_of(self.workspace)
        if not run_dir.exists():
            QMessageBox.critical(
                self, "ORCA Calculator", f"Run folder not found:\n{run_dir}"
            )
            return

        executable = sort_core.executable_operations(self.operations)
        if not executable:
            QMessageBox.information(
                self, "ORCA Calculator",
                "There are no executable operations in the current preview.",
            )
            return

        summary = []
        for op in executable[:20]:
            verb = "OVERWRITE" if op["status"] == "Conflict" else "MOVE"
            if op["type"] == "run":
                verb = "RUN " + verb
            summary.append(f"{verb:<14} {op['source'].name} -> {op['destination']}")
        if len(executable) > 20:
            summary.append(f"... and {len(executable) - 20} more operation(s)")

        answer = QMessageBox.question(
            self,
            "Confirm plan",
            "The following operations will run:\n\n" + "\n".join(summary) + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        # The user asked for target folders to be created when missing
        base_dir = self.current_base_dir()
        if base_dir is not None:
            try:
                sort_core.ensure_project_folders(base_dir)
            except OSError as e:
                QMessageBox.critical(
                    self, "ORCA Calculator", f"Cannot create target folders:\n{e}"
                )
                return

        counts = sort_core.execute_operations(self.operations, run_dir)
        self.status_label.setText(
            f"Done  ·  Moved: {counts['moved']}  ·  "
            f"Overwritten: {counts['overwritten']}  ·  "
            f"Skipped: {counts['skipped']}  ·  Errors: {counts['errors']}"
        )
        self.parent_dialog.update_internal_state()
        self.refresh_plan()

    # --------------------------------------------------------------- state
    def set_running(self, running):
        """A queue writes into Run: sorting must wait until it finishes."""
        self.execute_button.setEnabled(not running)

    def collect_settings(self, settings):
        settings["sort_mode"] = "folders" if self.folder_mode() else "files"
        if self.workspace:
            # A typed new project is not persistent until it exists
            saved = self.new_project_edit.text().strip() or None
            settings["sort_project"] = saved or self.project_combo.currentText()

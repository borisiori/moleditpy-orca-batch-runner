"""Delete tab: selective cleanup of leftover files in Run."""

import logging

from PySide6.QtCore import Qt
from PySide6.QtGui import QBrush, QColor
from PySide6.QtWidgets import (
    QAbstractItemView,
    QCheckBox,
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

from . import delete_core

logger = logging.getLogger(__name__)

RED = QBrush(QColor("#c62828"))
GRAY = QBrush(QColor("#78909c"))


class DeleteTab(QWidget):
    def __init__(self, parent_dialog, context, settings):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.context = context
        self.settings = settings
        self.workspace = ""
        self.candidates = []

        self.setup_ui()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        layout.addWidget(QLabel(
            "Preview of the Run items eligible for deletion. Double-click a "
            "row to toggle Delete / Keep."
        ))

        options = QHBoxLayout()
        options.addWidget(QLabel("Mode:"))
        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Files — delete individual files")
        self.mode_combo.addItem("Folders — delete whole runs")
        self.mode_combo.setToolTip(
            "Files: every file inside Run is listed for deletion one by one.\n\n"
            "Folders: each per-run folder is deleted as one unit; loose files\n"
            "in the Run root are not touched (that is the Files mode)."
        )
        self.mode_combo.currentIndexChanged.connect(lambda _i: self.refresh_candidates())
        options.addWidget(self.mode_combo)

        self.inp_check = QCheckBox("Include *.inp (unprotect input files)")
        self.inp_check.setToolTip(
            "Unchecked by default: input files are protected.\n"
            "Files mode: loose .inp files become deletable.\n"
            "Folders mode: each run's companion .inp is deleted with its folder."
        )
        self.inp_check.toggled.connect(self.refresh_candidates)
        options.addWidget(self.inp_check)
        options.addStretch()
        self.execute_button = QPushButton("Delete marked files")
        self.execute_button.clicked.connect(self.execute_delete)
        options.addWidget(self.execute_button)
        layout.addLayout(options)

        actions = QHBoxLayout()
        for text, handler in (
            ("Selection → Delete", lambda: self._decide_selection("Delete")),
            ("Selection → Keep", lambda: self._decide_selection("Keep")),
            ("Select all", self._select_all),
            ("Select marked for deletion", self._select_marked),
            ("Rescan Run", self.refresh_candidates),
        ):
            button = QPushButton(text)
            button.clicked.connect(handler)
            actions.addWidget(button)
        actions.addStretch()
        layout.addLayout(actions)

        exclude_row = QHBoxLayout()
        exclude_row.addWidget(QLabel("Run folders to skip (comma-separated):"))
        self.exclude_edit = QLineEdit()
        self.exclude_edit.returnPressed.connect(self.apply_exclusions)
        exclude_row.addWidget(self.exclude_edit, 1)
        apply_button = QPushButton("Apply")
        apply_button.clicked.connect(self.apply_exclusions)
        exclude_row.addWidget(apply_button)
        layout.addLayout(exclude_row)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(
            ["File", "Extension", "Size (KB)", "Decision"]
        )
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.Stretch
        )
        self.table.cellDoubleClicked.connect(self._toggle_decision)
        layout.addWidget(self.table, 1)

        self.status_label = QLabel("")
        layout.addWidget(self.status_label)

    # ------------------------------------------------------------ scanning
    def set_workspace(self, workspace):
        self.workspace = workspace
        if workspace:
            self.exclude_edit.setText(
                ", ".join(sorted(delete_core.load_excluded_folders(workspace)))
            )
        # Mode is a preference persisted with the project settings
        self.mode_combo.setCurrentIndex(
            1 if self.settings.get("delete_mode") == "folders" else 0
        )
        self.refresh_candidates()

    def folder_mode(self):
        return self.mode_combo.currentIndex() == 1

    def apply_exclusions(self):
        if not self.workspace:
            return
        names = {p.strip() for p in self.exclude_edit.text().split(",") if p.strip()}
        delete_core.save_excluded_folders(self.workspace, names)
        self.exclude_edit.setText(", ".join(sorted(names)))
        self.refresh_candidates()

    def refresh_candidates(self):
        previous = {
            str(c["path"]): c.get("decision", "Delete") for c in self.candidates
        }
        self.candidates = []
        if self.workspace:
            run_dir = delete_core.run_dir_of(self.workspace)
            builder = (delete_core.build_folder_candidates if self.folder_mode()
                       else delete_core.build_candidates)
            self.candidates = builder(
                run_dir,
                delete_core.load_excluded_folders(self.workspace),
                include_inp=self.inp_check.isChecked(),
            )
        for candidate in self.candidates:
            saved = previous.get(str(candidate["path"]))
            if saved in {"Delete", "Keep"}:
                candidate["decision"] = saved

        self.table.setRowCount(0)
        for row, candidate in enumerate(self.candidates):
            self.table.insertRow(row)
            self._set_item(row, 0, candidate["name"])
            self._set_item(row, 1, candidate["extension"])
            self._set_item(row, 2, candidate["size_kb"])
            self._paint_decision(row, candidate)
            self.table.item(row, 0).setData(Qt.UserRole, candidate)
        self._update_counts()

    def _set_item(self, row, column, text):
        item = QTableWidgetItem(str(text))
        self.table.setItem(row, column, item)
        return item

    def _paint_decision(self, row, candidate):
        text = "Delete" if candidate["decision"] == "Delete" else "Keep"
        item = QTableWidgetItem(text)
        item.setForeground(RED if candidate["decision"] == "Delete" else GRAY)
        self.table.setItem(row, 3, item)

    # ---------------------------------------------------------- decisions
    def _toggle_decision(self, row, _column):
        if not 0 <= row < len(self.candidates):
            return
        candidate = self.candidates[row]
        candidate["decision"] = "Keep" if candidate["decision"] == "Delete" else "Delete"
        self._paint_decision(row, candidate)
        self._update_counts()

    def _decide_selection(self, decision):
        rows = sorted({i.row() for i in self.table.selectedItems()})
        if not rows:
            QMessageBox.information(
                self, "ORCA Batch Runner", "Select one or more rows first."
            )
            return
        for row in rows:
            self.candidates[row]["decision"] = decision
            self._paint_decision(row, self.candidates[row])
        self._update_counts()

    def _select_all(self):
        self.table.selectAll()

    def _select_marked(self):
        self.table.clearSelection()
        self.table.setCurrentCell(-1, -1)
        model = self.table.selectionModel()
        for row, candidate in enumerate(self.candidates):
            if candidate["decision"] == "Delete":
                model.select(self.table.model().index(row, 0),
                             model.SelectionFlag.Select | model.SelectionFlag.Rows)

    def _update_counts(self):
        marked = sum(1 for c in self.candidates if c["decision"] == "Delete")
        keep = len(self.candidates) - marked
        self.status_label.setText(
            f"Candidates: {len(self.candidates)}  ·  Marked delete: {marked}  ·  "
            f"Keep: {keep}"
        )

    # ----------------------------------------------------------- execution
    def execute_delete(self):
        if not self.workspace:
            return
        run_dir = delete_core.run_dir_of(self.workspace)
        if not run_dir.exists():
            QMessageBox.critical(
                self, "ORCA Batch Runner", f"Run folder not found:\n{run_dir}"
            )
            return

        to_delete = [c for c in self.candidates if c["decision"] == "Delete"]
        if not to_delete:
            QMessageBox.information(
                self, "ORCA Batch Runner", "No files are marked for deletion."
            )
            return

        summary = [f"DELETE  {c['name']}" for c in to_delete[:20]]
        if len(to_delete) > 20:
            summary.append(f"... and {len(to_delete) - 20} more file(s)")

        answer = QMessageBox.question(
            self,
            "Confirm deletion",
            "The following files will be deleted from Run:\n\n"
            + "\n".join(summary) + "\n\nContinue?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return

        counts = delete_core.execute_deletions(self.candidates, run_dir)
        self.status_label.setText(
            f"Done  ·  Deleted: {counts['deleted']}  ·  Kept: {counts['kept']}  ·  "
            f"Errors: {counts['errors']}"
        )
        self.refresh_candidates()

    # --------------------------------------------------------------- state
    def set_running(self, running):
        """A queue writes into Run: deletion must wait until it finishes."""
        self.execute_button.setEnabled(not running)

    def collect_settings(self, settings):
        settings["delete_mode"] = "folders" if self.folder_mode() else "files"

"""Queue tab: pick input files, order them and launch the queue."""

import logging
import os
from datetime import datetime

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from . import config

logger = logging.getLogger(__name__)


class QueueTab(QWidget):
    def __init__(self, parent_dialog, context, settings):
        super().__init__(parent_dialog)
        self.parent_dialog = parent_dialog
        self.context = context
        self.settings = settings
        self.workspace = ""

        self.setup_ui()

    # ------------------------------------------------------------------ UI
    def setup_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(6)

        folder_row = QHBoxLayout()
        folder_row.addWidget(QLabel("Folder:"))
        self.folder_combo = QComboBox()
        self.folder_combo.setMinimumWidth(380)
        self.folder_combo.setToolTip(
            "Folder listed for .inp files (the workspace Run folder by default)"
        )
        self.folder_combo.currentIndexChanged.connect(self._refresh_available)
        folder_row.addWidget(self.folder_combo, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_folder)
        folder_row.addWidget(browse)
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self._refresh_available)
        folder_row.addWidget(refresh)
        layout.addLayout(folder_row)

        columns = QHBoxLayout()
        columns.setSpacing(8)

        left = QVBoxLayout()
        left.addWidget(QLabel("Available .inp files"))
        self.available_list = QListWidget()
        self.available_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.available_list.itemDoubleClicked.connect(self._add_to_queue)
        left.addWidget(self.available_list, 1)
        columns.addLayout(left, 1)

        buttons = QVBoxLayout()
        buttons.addStretch()
        for text, tip, handler in (
            ("Add →", "Add the selected files at the end of the queue\n(double-click also adds)", self._add_to_queue),
            ("← Remove", None, self._remove_from_queue),
            ("▲", "Move the selected file up in the queue", lambda: self._move_in_queue(-1)),
            ("▼", "Move the selected file down in the queue", lambda: self._move_in_queue(1)),
            ("Clear queue", None, self._clear_queue),
        ):
            button = QPushButton(text)
            if tip:
                button.setToolTip(tip)
            button.clicked.connect(handler)
            buttons.addWidget(button)
        buttons.addStretch()
        columns.addLayout(buttons)

        right = QVBoxLayout()
        self.queue_label = QLabel("Execution queue (drag to reorder)")
        right.addWidget(self.queue_label)
        self.queue_list = QListWidget()
        self.queue_list.setSelectionMode(QAbstractItemView.ExtendedSelection)
        self.queue_list.setDragDropMode(QAbstractItemView.InternalMove)
        self.queue_list.setDefaultDropAction(Qt.MoveAction)
        # Renumber [1] [2]... after a drag or the move buttons
        self.queue_list.model().rowsInserted.connect(self._renumber_queue)
        self.queue_list.model().rowsRemoved.connect(self._renumber_queue)
        right.addWidget(self.queue_list, 1)
        columns.addLayout(right, 1)
        layout.addLayout(columns, 1)

        launch_row = QHBoxLayout()
        self.status_label = QLabel("")
        launch_row.addWidget(self.status_label)
        launch_row.addStretch()
        self.launch_button = QPushButton("Launch queue")
        self.launch_button.setToolTip(
            "Run the queued files in order,\nshowing the output in the Run tab"
        )
        self.launch_button.clicked.connect(self._launch_queue)
        launch_row.addWidget(self.launch_button)
        layout.addLayout(launch_row)

    # ------------------------------------------------------------ folders
    def set_workspace(self, workspace):
        self.workspace = workspace
        self._fill_folders()

    def set_last_input_dir(self, path):
        index = self.folder_combo.findData(path)
        if index >= 0:
            self.folder_combo.setCurrentIndex(index)

    def _extra_folders(self):
        if self.context is None:
            return []
        return self.context.get_setting(config.SETTING_EXTRA_FOLDERS, []) or []

    def _fill_folders(self):
        current = self.folder_combo.currentData()
        self.folder_combo.blockSignals(True)
        self.folder_combo.clear()
        for path, label in config.input_folders(self.workspace, self._extra_folders()):
            self.folder_combo.addItem(label, path)
        self.folder_combo.blockSignals(False)

        target = current or self.settings.get("last_input_dir", "")
        index = self.folder_combo.findData(target)
        if index >= 0:
            self.folder_combo.setCurrentIndex(index)
        self._refresh_available()

    def _browse_folder(self):
        path = QFileDialog.getExistingDirectory(
            self, "Select the ORCA input folder", self.workspace or "/opt"
        )
        if not path:
            return
        if self.context is not None:
            extras = sorted(set(self._extra_folders()) | {path}, key=str.lower)
            self.context.set_setting(config.SETTING_EXTRA_FOLDERS, extras)
        self._fill_folders()
        index = self.folder_combo.findData(path)
        if index >= 0:
            self.folder_combo.setCurrentIndex(index)

    def _refresh_available(self):
        self.available_list.clear()
        folder = self.folder_combo.currentData()
        if not folder or not os.path.isdir(folder):
            return
        self.settings["last_input_dir"] = folder
        try:
            names = sorted(
                (n for n in os.listdir(folder) if n.lower().endswith(".inp")),
                key=str.lower,
            )
        except OSError:
            return
        for name in names:
            path = os.path.join(folder, name)
            try:
                stat = os.stat(path)
                modified = datetime.fromtimestamp(stat.st_mtime).strftime("%Y-%m-%d %H:%M")
                size = f"{stat.st_size / 1024:.1f} KB"
            except OSError:
                modified, size = "?", "?"
            item = QListWidgetItem(f"{name}    ·    {modified}    ·    {size}")
            item.setToolTip(path)
            item.setData(Qt.UserRole, path)
            self.available_list.addItem(item)

    # ------------------------------------------------------------- queue
    def queue_paths(self):
        return [
            self.queue_list.item(i).data(Qt.UserRole)
            for i in range(self.queue_list.count())
        ]

    def _add_to_queue(self, *_args):
        in_queue = set(self.queue_paths())
        for item in self.available_list.selectedItems():
            path = item.data(Qt.UserRole)
            if path in in_queue:
                continue
            entry = QListWidgetItem(os.path.basename(path))
            entry.setToolTip(path)
            entry.setData(Qt.UserRole, path)
            self.queue_list.addItem(entry)

    def _remove_from_queue(self):
        for item in self.queue_list.selectedItems():
            self.queue_list.takeItem(self.queue_list.row(item))
        self._renumber_queue()

    def _clear_queue(self):
        self.queue_list.clear()
        self._renumber_queue()

    def _move_in_queue(self, delta):
        row = self.queue_list.currentRow()
        if row < 0:
            return
        target = row + delta
        if not 0 <= target < self.queue_list.count():
            return
        item = self.queue_list.takeItem(row)
        self.queue_list.insertItem(target, item)
        self.queue_list.setCurrentRow(target)
        self._renumber_queue()

    def _renumber_queue(self):
        for i in range(self.queue_list.count()):
            item = self.queue_list.item(i)
            name = os.path.basename(item.data(Qt.UserRole) or "")
            item.setText(f"[{i + 1}]  {name}")
        count = self.queue_list.count()
        self.queue_label.setText(
            "Execution queue (drag to reorder)"
            if count == 0
            else f"Execution queue — {count} file(s) in run order"
        )

    # ------------------------------------------------------------ launch
    def _launch_queue(self):
        paths = [p for p in self.queue_paths() if p and os.path.isfile(p)]
        if not paths:
            self.status_label.setText(
                "The queue is empty: add .inp files from the left list."
            )
            return
        self.parent_dialog.start_queue(paths)

    # -------------------------------------------------------------- state
    def set_running(self, running):
        """Lock the queue while a run is active."""
        self.launch_button.setEnabled(not running)
        self.folder_combo.setEnabled(not running)
        self.queue_list.setEnabled(not running)
        if running:
            self.status_label.setText("Queue running — locked until it finishes.")

    def collect_settings(self, settings):
        folder = self.folder_combo.currentData()
        if folder:
            settings["last_input_dir"] = folder

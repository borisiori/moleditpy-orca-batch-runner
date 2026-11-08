# ORCA Batch Runner: job queue manager for the ORCA quantum chemistry package.
PLUGIN_NAME = "ORCA Batch Runner"
PLUGIN_VERSION = "2026.11.08"
PLUGIN_AUTHOR = "borisiori"
PLUGIN_LICENSE = "LGPL-3.0-or-later"
PLUGIN_DESCRIPTION = (
    "Run ORCA calculations as a sequential job queue: load input files, follow "
    "the live output, monitor running and finished jobs, and sort or delete "
    "the results. Input generation is provided by the ORCA Input Generator "
    "Pro plugin."
)
PLUGIN_DEPENDENCIES = ["PySide6-Essentials"]
PLUGIN_SUPPORTED_MOLEDITPY_VERSION = ">=5.0.0, <6.0.0"
PLUGIN_SUPPORTED_PYTHON_VERSION = ">=3.10, <3.15"
# Process-tree control and the run monitor rely on Linux /proc and fuser.
PLUGIN_SUPPORTED_OS = ["Linux"]
PLUGIN_TAGS = ["Optimization"]

from .gui import OrcaCalculatorDialog

# Global settings state (persisted in the project file)
PLUGIN_SETTINGS = {}


def initialize(context):
    """Register the project state hooks and the Extensions menu entry."""

    # --- Persistence Logic ---
    def save_project_state():
        return PLUGIN_SETTINGS

    def load_project_state(data):
        PLUGIN_SETTINGS.clear()
        PLUGIN_SETTINGS.update(data)

    context.register_save_handler(save_project_state)
    context.register_load_handler(load_project_state)

    # --- UI ---
    def handle_reset():
        for key in ("last_input_dir", "sort_project"):
            PLUGIN_SETTINGS.pop(key, None)

        dlg = context.get_window("dialog")
        if dlg is not None and hasattr(dlg, "on_document_reset"):
            dlg.on_document_reset()

    context.register_document_reset_handler(handle_reset)

    def show_dialog():
        dlg = context.get_window("dialog")
        if dlg is not None:
            try:
                if dlg.isVisible():
                    dlg.raise_()
                    dlg.activateWindow()
                    return
                if dlg.close() is False:
                    dlg.show()
                    dlg.raise_()
                    return  # A queue is still shutting down.
                dlg.deleteLater()
            except RuntimeError:
                pass
            # Unregister the stale window so it doesn't block creation of a new one
            context.register_window("dialog", None)

        mw = context.get_main_window()
        new_dlg = OrcaCalculatorDialog(
            mw, context, settings=PLUGIN_SETTINGS, version=PLUGIN_VERSION
        )
        context.register_window("dialog", new_dlg)
        new_dlg.show()

    # Register the menu action
    context.add_menu_action("Extensions/ORCA Batch Runner...", show_dialog)

"""ORCA binary and workspace discovery, plus machine-level settings."""

import logging
import os
import shutil
import sys
from pathlib import Path

logger = logging.getLogger(__name__)

# Keys stored through context.set_setting (app settings, survive restarts)
SETTING_BINARY = "orca_binary"
SETTING_WORKSPACE = "workspace_root"

# Scratch subfolder created inside the workspace while a job runs
TEMP_DIRNAME = "temp"
# Default input folder inside the workspace
RUN_DIRNAME = "Run"


def find_orca_binary():
    """Locate the ORCA launcher: env overrides, PATH, then common installs."""
    candidates = []

    env_bin = os.environ.get("ORCA_BIN", "").strip()
    if env_bin:
        candidates.append(Path(env_bin))

    env_home = os.environ.get("ORCA_HOME", "").strip()
    if env_home:
        candidates.append(Path(env_home) / _binary_names()[0])

    for name in _binary_names():
        on_path = shutil.which(name)
        if on_path:
            candidates.append(Path(on_path))
        found = _search_common_folders(name)
        if found:
            candidates.append(found)

    for candidate in candidates:
        if validate_binary(candidate)[0]:
            return str(candidate.resolve())

    return None


def _binary_names():
    """The launcher file name per platform ('orca.exe' on Windows)."""
    return ("orca.exe", "orca") if sys.platform.startswith("win") else ("orca",)


def _search_common_folders(binary_name):
    """First valid ORCA under the conventional install parents of the
    platform, case-insensitive (ORCA/Orca/orca_6...)."""
    for parent in _common_parents():
        try:
            names = [n for n in os.listdir(parent) if n.lower().startswith("orca")]
        except OSError:
            continue
        # Alphabetical order approximates newest-version-last inside a parent
        for name in sorted(names, key=str.lower):
            candidate = Path(parent) / name / binary_name
            if validate_binary(candidate)[0]:
                return candidate
    return None


def _common_parents():
    """Conventional install roots per platform."""
    parents = [Path("/opt"), Path("/usr/local"), Path.home(),
               Path.home() / "Programas", Path.home() / "apps"]
    if sys.platform == "darwin":
        parents.insert(0, Path("/Applications"))
    elif sys.platform.startswith("win"):
        for var in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA"):
            value = os.environ.get(var)
            if value:
                parents.insert(0, Path(value))
        parents.append(Path.home() / "Desktop")
    return parents


def validate_binary(path):
    """Return (ok, message) for a candidate ORCA launcher path."""
    if not path:
        return False, "not set"
    path = Path(path)
    if not path.is_file():
        return False, f"file not found: {path}"
    if not os.access(path, os.X_OK):
        return False, f"not executable: {path}"
    return True, str(path)


def default_workspace(binary_path):
    """ORCA installs keep inputs and results next to the binaries."""
    if not binary_path:
        return ""
    return str(Path(binary_path).resolve().parent)


def workspace_folders(workspace):
    """The managed subfolders of a workspace, as a dict of name -> Path."""
    root = Path(workspace)
    return {
        "run": root / RUN_DIRNAME,
        "temp": root / TEMP_DIRNAME,
    }


def ensure_workspace_layout(workspace):
    """Create the Run and temp folders so a fresh workspace is ready to use."""
    folders = workspace_folders(workspace)
    for folder in folders.values():
        folder.mkdir(parents=True, exist_ok=True)
    return folders


# Folders that mark a project directory inside the workspace
WORK_SUBDIRS = ("input_files", "output_files", "output_analysis", "Aux_files")

SETTING_EXTRA_FOLDERS = "extra_input_folders"


def input_folders(workspace, extra_folders=()):
    """Folders that can hold input files: Run first, then the workspace root,
    its work subfolders and every project (plus the user's extra folders)."""
    if not workspace:
        return []
    root = Path(workspace)
    run = root / RUN_DIRNAME
    folders = [(str(run), "Run (default)"), (str(root), "Workspace root")]
    seen = {str(run), str(root)}

    def add(path, label=None):
        if path.is_dir() and str(path) not in seen:
            seen.add(str(path))
            folders.append((str(path), label or str(path)))

    for name in WORK_SUBDIRS:
        add(root / name)
    try:
        children = sorted(root.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        children = []
    for child in children:
        if not child.is_dir() or child.name.startswith("."):
            continue
        # A project is any folder holding at least one work subfolder
        if any((child / s).is_dir() for s in WORK_SUBDIRS):
            add(child)
            for name in WORK_SUBDIRS + (RUN_DIRNAME,):
                add(child / name)
    for path in extra_folders:
        add(Path(path).expanduser(), f"{path} (extra)")
    return folders


def load_paths(context):
    """Read the persisted binary/workspace, filling gaps by detection."""
    binary = ""
    workspace = ""
    if context is not None:
        binary = (context.get_setting(SETTING_BINARY) or "").strip()
        workspace = (context.get_setting(SETTING_WORKSPACE) or "").strip()

    if not binary:
        binary = find_orca_binary() or ""
    if not workspace and binary:
        workspace = default_workspace(binary)
    return binary, workspace


def save_paths(context, binary, workspace):
    """Persist the paths in the app settings (machine-level, not project)."""
    if context is None:
        return
    context.set_setting(SETTING_BINARY, binary.strip())
    context.set_setting(SETTING_WORKSPACE, workspace.strip())


def extra_environment(binary_path):
    """Env updates ORCA needs: its helpers live next to the launcher and some
    MPI tools only find them through PATH (plus LD_LIBRARY_PATH on Linux)."""
    orca_dir = str(Path(binary_path).resolve().parent)
    keys = ("PATH", "LD_LIBRARY_PATH") if sys.platform.startswith("linux") \
        else ("PATH",)
    updates = {}
    for key in keys:
        current = os.environ.get(key, "")
        if orca_dir not in current.split(os.pathsep):
            updates[key] = orca_dir + (os.pathsep + current if current else "")
    return updates

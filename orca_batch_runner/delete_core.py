"""Pure logic to delete leftover files from the Run folder."""

import logging
import shutil
from pathlib import Path

from . import config
from .sort_core import remove_empty_subfolders

logger = logging.getLogger(__name__)

EXCLUDED_FILE = ".delete_excluded_folders.txt"


def load_excluded_folders(workspace):
    try:
        content = (Path(workspace) / EXCLUDED_FILE).read_text()
        return {line.strip() for line in content.splitlines() if line.strip()}
    except OSError:
        return set()


def save_excluded_folders(workspace, names):
    try:
        (Path(workspace) / EXCLUDED_FILE).write_text(
            "\n".join(sorted(names)) + ("\n" if names else "")
        )
    except OSError as e:
        logger.warning("could not save excluded folders: %s", e)


def is_in_excluded_folder(run_dir, file_path, excluded_folders):
    rel_parts = Path(file_path).relative_to(run_dir).parts
    return len(rel_parts) > 1 and rel_parts[0] in excluded_folders


def _folder_size_kb(folder):
    try:
        total = sum(f.stat().st_size for f in folder.rglob("*") if f.is_file())
    except OSError:
        return 0.0
    return total / 1024


def build_candidates(run_dir, excluded_folders=None, include_inp=False):
    """Files mode: Run files eligible for deletion. Inputs are protected
    unless the user explicitly opts in with include_inp=True."""
    candidates = []
    excluded_folders = excluded_folders or set()
    run_dir = Path(run_dir)

    if not run_dir.exists():
        return candidates

    for file_path in sorted(run_dir.rglob("*"),
                            key=lambda p: str(p.relative_to(run_dir)).lower()):
        if not file_path.is_file():
            continue
        if file_path.suffix.lower() == ".inp" and not include_inp:
            continue
        if is_in_excluded_folder(run_dir, file_path, excluded_folders):
            continue

        try:
            size_kb = file_path.stat().st_size / 1024
        except OSError:
            size_kb = 0.0

        candidates.append({
            "type": "file",
            "path": file_path,
            "name": str(file_path.relative_to(run_dir)),
            "extension": file_path.suffix if file_path.suffix else "(no extension)",
            "size_kb": f"{size_kb:.1f}",
            "decision": "Delete",
            "status": "Ready",
        })
    return candidates


def build_folder_candidates(run_dir, excluded_folders=None, include_inp=False):
    """Folders mode: each per-run folder is one deletion unit. The companion
    loose .inp of a run is deleted too only when include_inp is set."""
    candidates = []
    excluded_folders = excluded_folders or set()
    run_dir = Path(run_dir)

    if not run_dir.exists():
        return candidates

    for folder in sorted((p for p in run_dir.iterdir() if p.is_dir()),
                         key=lambda p: p.name.lower()):
        if folder.name.startswith("."):
            continue
        if is_in_excluded_folder(run_dir, folder, excluded_folders):
            continue

        inp = folder.parent / (folder.name + ".inp")
        has_inp = inp.is_file() and include_inp
        name = folder.name + "/"
        if has_inp:
            name += "  (+ .inp)"

        candidates.append({
            "type": "folder",
            "path": folder,
            "inp": inp if has_inp else None,
            "name": name,
            "extension": "(folder)",
            "size_kb": f"{_folder_size_kb(folder):.1f}",
            "decision": "Delete",
            "status": "Ready",
        })
    return candidates


def execute_deletions(candidates, run_dir):
    """Delete the items marked Delete, updating each candidate status."""
    counts = {"deleted": 0, "kept": 0, "errors": 0}
    for candidate in candidates:
        if candidate["decision"] != "Delete":
            counts["kept"] += 1
            continue
        try:
            if candidate.get("type") == "folder":
                shutil.rmtree(candidate["path"])
                if candidate.get("inp") is not None:
                    candidate["inp"].unlink()
            else:
                candidate["path"].unlink()
            candidate["status"] = "Done"
            counts["deleted"] += 1
        except Exception as e:  # noqa: BLE001 -- keep deleting the rest
            candidate["status"] = "Error"
            counts["errors"] += 1
            logger.warning("delete failed for %s: %s", candidate["path"], e)

    remove_empty_subfolders(run_dir)
    return counts


def run_dir_of(workspace):
    return Path(workspace) / config.RUN_DIRNAME

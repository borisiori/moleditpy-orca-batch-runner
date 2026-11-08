"""Pure logic to sort finished files out of Run into the project structure."""

import logging
import shutil
from pathlib import Path

from . import config

logger = logging.getLogger(__name__)

# First-match-wins: file pattern -> destination relative to the project folder
DESTINATION_MAPPING = [
    ("*.inp", Path("input_files")),
    ("*.smd.out", Path("Aux_files") / "smd"),
    ("*.smd.grd", Path("Aux_files") / "smd"),
    ("*.smd", Path("Aux_files") / "smd"),
    ("*.finalensemble.xyz", Path("output_analysis") / "goat"),
    ("*.globalminimum.xyz", Path("output_files") / "goat"),
    ("*.densities", Path("Aux_files") / "densities"),
    ("*.densitiesinfo", Path("Aux_files") / "densities"),
    ("*.engrad", Path("Aux_files") / "engrad"),
    ("*.gbw", Path("Aux_files") / "gbw"),
    ("*.hess", Path("Aux_files") / "hess"),
    ("*.opt", Path("Aux_files") / "opt"),
    ("*.property.txt", Path("Aux_files") / "property"),
    ("*_trj.xyz", Path("Aux_files") / "trajectory"),
    ("*.cpcm", Path("Aux_files") / "cpcm"),
    ("*.cpcm_corr", Path("Aux_files") / "cpcm"),
    ("*.xtbrestart", Path("Aux_files") / "goat"),
    ("*.relaxscanact.dat", Path("Aux_files") / "relax_scan"),
    ("*.relaxscanscf.dat", Path("Aux_files") / "relax_scan"),
    ("*.allxyz", Path("Aux_files") / "relax_scan"),
    ("*.ges", Path("Aux_files") / "ges"),
    # Kept for scientific transparency (citable ORCA references), never deleted
    ("*.bibtex", Path("Aux_files") / "bibtex"),
    ("*.out", Path("output_files")),
    ("*.xyz", Path("Aux_files") / "coordinates"),
]

GENERAL_LABEL = "General (workspace main folders)"

EXCLUDED_FILE = ".sort_excluded_folders.txt"


def run_dir_of(workspace):
    return Path(workspace) / config.RUN_DIRNAME


def discover_projects(workspace):
    """Find project folders sharing the general folder structure."""
    root = Path(workspace)
    if not root.is_dir():
        return []
    projects = []
    for child in sorted(root.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if any((child / marker).is_dir() for marker in config.WORK_SUBDIRS):
            projects.append(child.name)
    return projects


def resolve_base_folder(workspace, project_value):
    """Map the project selector value to the folder that receives the files."""
    value = (project_value or "").strip()
    root = Path(workspace)
    if not value or value == GENERAL_LABEL:
        return root
    if "/" in value or "\\" in value or value in {".", ".."}:
        return None  # reject invalid project names (must be a simple folder name)
    return root / value


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


def build_operations(run_dir, base_dir, excluded_folders=None):
    """Files-mode plan: one move operation per Run file, by file type."""
    operations = []
    claimed_files = set()
    excluded_folders = excluded_folders or set()
    run_dir = Path(run_dir)

    if not run_dir.exists():
        return operations

    def sort_key(path):
        return str(path.relative_to(run_dir)).lower()

    for pattern, relative_destination in DESTINATION_MAPPING:
        destination_folder = Path(base_dir) / relative_destination
        for file_path in sorted(run_dir.rglob(pattern), key=sort_key):
            if not file_path.is_file() or file_path in claimed_files:
                continue
            if is_in_excluded_folder(run_dir, file_path, excluded_folders):
                continue

            target_path = destination_folder / file_path.name
            target_exists = target_path.exists()
            operations.append({
                "type": "move",
                "source": file_path,
                "destination": destination_folder,
                "target": target_path,
                "status": "Conflict" if target_exists else "Ready",
                "decision": "No move" if target_exists else "Move",
            })
            claimed_files.add(file_path)

    return operations


def build_run_operations(run_dir, base_dir, excluded_folders=None):
    """Folders-mode plan: each per-run folder moves intact so a run keeps its
    identity inside the target project. A companion loose .inp joins the
    folder (the runner already stores a copy of it inside since v0.5.0)."""
    operations = []
    excluded_folders = excluded_folders or set()
    run_dir = Path(run_dir)

    if not run_dir.exists():
        return operations

    for folder in sorted((p for p in run_dir.iterdir() if p.is_dir()),
                         key=lambda p: p.name.lower()):
        if folder.name.startswith("."):
            continue
        if is_in_excluded_folder(run_dir, folder, excluded_folders):
            continue

        destination = Path(base_dir) / "output_files" / folder.name
        inp = folder.parent / (folder.name + ".inp")
        has_inp = inp.is_file()
        inp_target = destination / inp.name if has_inp else None
        conflict = destination.exists() or (has_inp and inp_target.exists())

        operations.append({
            "type": "run",
            "source": folder,
            "inp": inp if has_inp else None,
            "destination": destination,
            "target": destination,
            "inp_target": inp_target,
            "status": "Conflict" if conflict else "Ready",
            "decision": "No move" if conflict else "Move",
        })
    return operations


def ensure_project_folders(base_dir):
    """Create the target folder and its standard work subfolders when missing."""
    base_dir = Path(base_dir)
    base_dir.mkdir(parents=True, exist_ok=True)
    for name in config.WORK_SUBDIRS:
        (base_dir / name).mkdir(exist_ok=True)


def executable_operations(operations):
    """The subset of the preview that will really run with current decisions."""
    selected = []
    for op in operations:
        if op["type"] not in ("move", "run"):
            continue
        if (op["status"] == "Ready" and op["decision"] == "Move") or (
            op["status"] == "Conflict" and op["decision"] == "Overwrite"
        ):
            selected.append(op)
    return selected


def execute_operations(operations, run_dir):
    """Run the decided operations, updating each op status; returns counts."""
    counts = {"moved": 0, "overwritten": 0, "skipped": 0, "errors": 0}
    for op in operations:
        try:
            if op["type"] == "move":
                if op["status"] == "Ready" and op["decision"] == "Move":
                    op["destination"].mkdir(parents=True, exist_ok=True)
                    shutil.move(str(op["source"]), str(op["target"]))
                    op["status"] = "Done"
                    counts["moved"] += 1
                elif op["status"] == "Conflict" and op["decision"] == "Overwrite":
                    op["destination"].mkdir(parents=True, exist_ok=True)
                    op["source"].replace(op["target"])
                    op["status"] = "Done"
                    counts["overwritten"] += 1
                else:
                    counts["skipped"] += 1
            elif op["type"] == "run":
                wants = (op["status"] == "Ready" and op["decision"] == "Move") or (
                    op["status"] == "Conflict" and op["decision"] == "Overwrite"
                )
                if not wants:
                    counts["skipped"] += 1
                else:
                    # Overwrite replaces a previous run folder of the same name
                    overwrote = op["target"].exists()
                    if overwrote:
                        shutil.rmtree(op["target"])
                    op["target"].parent.mkdir(parents=True, exist_ok=True)
                    shutil.move(str(op["source"]), str(op["target"]))
                    if op["inp"] is not None and op["inp"].exists():
                        op["inp_target"].parent.mkdir(parents=True, exist_ok=True)
                        if op["inp_target"].exists():
                            op["inp_target"].unlink()
                        shutil.move(str(op["inp"]), str(op["inp_target"]))
                    op["status"] = "Done"
                    counts["overwritten" if overwrote else "moved"] += 1
        except Exception as e:  # noqa: BLE001 -- one bad file must not abort the plan
            op["status"] = "Error"
            counts["errors"] += 1
            logger.warning("sort failed for %s: %s", op["source"], e)

    remove_empty_subfolders(run_dir)
    return counts


def remove_empty_subfolders(run_dir):
    """Drop the emptied per-run folders left in Run after a sort/delete."""
    try:
        for child in sorted(Path(run_dir).iterdir()):
            if child.is_dir():
                try:
                    child.rmdir()
                except OSError:
                    pass
    except OSError:
        pass

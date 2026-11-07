"""Sort core tests: plan building, folder creation and execution."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_calculator import sort_core


def make_workspace(tmp_path):
    run = tmp_path / "Run"
    (run / "job1").mkdir(parents=True)
    (run / "job1.inp").write_text("! HF")
    (run / "job1.out").write_text("ORCA TERMINATED NORMALLY")
    (run / "job1" / "job1.gbw").write_text("basis")
    (run / "job1" / "job1.bibtex").write_text("@article{orca}")
    project = tmp_path / "Proj"
    project.mkdir()
    (project / "input_files").mkdir()
    (tmp_path / "input_files").mkdir()  # general folder used by the default target
    return tmp_path, run


def test_discover_projects(tmp_path):
    root, _ = make_workspace(tmp_path)
    assert sort_core.discover_projects(root) == ["Proj"]


def test_resolve_base_folder(tmp_path):
    root, _ = make_workspace(tmp_path)
    assert sort_core.resolve_base_folder(root, "") == root
    assert sort_core.resolve_base_folder(root, sort_core.GENERAL_LABEL) == root
    assert sort_core.resolve_base_folder(root, "NewProject") == root / "NewProject"
    assert sort_core.resolve_base_folder(root, "../escape") is None


def test_build_operations_and_first_match(tmp_path):
    root, run = make_workspace(tmp_path)
    ops = sort_core.build_operations(run, root)

    by_name = {op["source"].name: op for op in ops}
    assert by_name["job1.inp"]["destination"] == root / "input_files"
    assert by_name["job1.gbw"]["destination"] == root / "Aux_files" / "gbw"
    # bibtex files are moved for transparency, never deleted
    assert by_name["job1.bibtex"]["destination"] == root / "Aux_files" / "bibtex"
    assert all(op["type"] == "move" for op in ops)
    # a conflicting target marks the operation as such
    (root / "input_files" / "job1.inp").write_text("! HF old")
    ops = sort_core.build_operations(run, root)
    inp_op = next(op for op in ops if op["source"].name == "job1.inp")
    assert inp_op["status"] == "Conflict"
    assert inp_op["decision"] == "No move"


def test_excluded_folders_roundtrip(tmp_path):
    sort_core.save_excluded_folders(tmp_path, {"keepme", "other"})
    assert sort_core.load_excluded_folders(tmp_path) == {"keepme", "other"}


def test_exclusion_skips_nested_folder(tmp_path):
    root, run = make_workspace(tmp_path)
    ops = sort_core.build_operations(run, root, excluded_folders={"job1"})
    assert all(op["source"].parent.name != "job1" for op in ops)


def test_ensure_project_folders(tmp_path):
    base = tmp_path / "BrandNew"
    sort_core.ensure_project_folders(base)
    for name in ("input_files", "output_files", "output_analysis", "Aux_files"):
        assert (base / name).is_dir()


def test_execute_operations(tmp_path):
    root, run = make_workspace(tmp_path)
    # a conflict to overwrite
    (root / "input_files" / "job1.inp").write_text("! HF old")

    ops = sort_core.build_operations(run, root)
    sort_core.ensure_project_folders(root)
    counts = sort_core.execute_operations(ops, run)

    assert counts["errors"] == 0
    assert (root / "input_files" / "job1.inp").is_file()
    assert (root / "output_files" / "job1.out").is_file()
    assert (root / "Aux_files" / "gbw" / "job1.gbw").is_file()
    assert (root / "Aux_files" / "bibtex" / "job1.bibtex").is_file()
    # the emptied per-run folder is gone
    assert not (run / "job1").exists()


def test_destinations_created_from_scratch(tmp_path):
    """Sorting into a brand-new project must create every folder it needs."""
    root, run = make_workspace(tmp_path)
    fresh = root / "BrandNewProject"  # does not exist at all

    # Files mode: type subfolders (including the deep bibtex one) are created
    ops = sort_core.build_operations(run, fresh)
    counts = sort_core.execute_operations(ops, run)
    assert counts["errors"] == 0
    assert (fresh / "input_files" / "job1.inp").is_file()
    assert (fresh / "output_files" / "job1.out").is_file()
    assert (fresh / "Aux_files" / "bibtex" / "job1.bibtex").is_file()

    # Folders mode: same guarantee for a second, equally new project
    (run / "job2").mkdir()
    (run / "job2" / "job2.out").write_text("ORCA TERMINATED NORMALLY")
    (run / "job2.inp").write_text("! HF")
    fresh2 = root / "AnotherNewProject"
    ops = sort_core.build_run_operations(run, fresh2)
    counts = sort_core.execute_operations(ops, run)
    assert counts["errors"] == 0
    assert (fresh2 / "output_files" / "job2" / "job2.out").is_file()
    assert (fresh2 / "output_files" / "job2" / "job2.inp").is_file()


def test_sort_tab_execute_plan_end_to_end(tmp_path, monkeypatch):
    """GUI-level regression: the full execute path must not reference keys
    that no longer exist (a stale counts['deleted'] once crashed it)."""
    from PySide6.QtWidgets import QApplication, QMessageBox, QWidget

    from orca_calculator.sort_tab import SortTab

    app = QApplication.instance() or QApplication(sys.argv)
    root, run = make_workspace(tmp_path)
    monkeypatch.setattr(
        QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes
    )
    monkeypatch.setattr(QMessageBox, "information", lambda *a, **k: None)

    stub = QWidget()  # minimal dialog stand-in
    stub.update_internal_state = lambda: None
    tab = SortTab(stub, None, {})
    tab.set_workspace(root)
    tab.execute_plan()  # a stale counts key here raises KeyError (regression)

    assert (root / "input_files" / "job1.inp").is_file()
    assert (root / "Aux_files" / "bibtex" / "job1.bibtex").is_file()
    # the plan refreshes right after: Run is empty now
    assert tab.operations == []


def test_folder_mode_moves_runs_intact(tmp_path):
    root, run = make_workspace(tmp_path)
    project = root / "Proj"
    # The runner leaves everything inside Run/<name>/; keep that layout here
    (run / "job1.out").unlink()
    (run / "job1" / "job1.out").write_text("ORCA TERMINATED NORMALLY")

    ops = sort_core.build_run_operations(run, project)
    assert len(ops) == 1
    op = ops[0]
    assert op["type"] == "run"
    assert op["target"] == project / "output_files" / "job1"
    assert op["inp_target"] == project / "output_files" / "job1" / "job1.inp"
    assert op["status"] == "Ready"

    counts = sort_core.execute_operations(ops, run)
    assert counts == {"moved": 1, "overwritten": 0, "skipped": 0, "errors": 0}
    # the run keeps its identity: folder intact, its input INSIDE it
    assert (project / "output_files" / "job1" / "job1.out").is_file()
    assert (project / "output_files" / "job1" / "job1.gbw").is_file()
    assert (project / "output_files" / "job1" / "job1.inp").is_file()
    assert not (run / "job1").exists()
    assert not (run / "job1.inp").exists()

    # a rerun with the run back in place would conflict on the existing folder
    (run / "job1").mkdir()
    (run / "job1" / "job1.out").write_text("again")
    ops = sort_core.build_run_operations(run, project)
    assert ops[0]["status"] == "Conflict"
    ops[0]["decision"] = "Overwrite"
    counts = sort_core.execute_operations(ops, run)
    assert counts["overwritten"] == 1 and counts["moved"] == 0
    assert (project / "output_files" / "job1" / "job1.out").read_text() == "again"

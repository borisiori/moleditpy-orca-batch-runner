"""Delete core tests: candidate building, protection of inputs, execution."""

import os
import sys

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from orca_calculator import delete_core


def make_run(tmp_path):
    run = tmp_path / "Run"
    (run / "job1").mkdir(parents=True)
    (run / "job1.inp").write_text("! HF")
    (run / "job1.out").write_text("ORCA TERMINATED NORMALLY")
    (run / "job1" / "job1.gbw").write_text("basis")
    return run


def test_inputs_protected_by_default(tmp_path):
    run = make_run(tmp_path)
    names = [c["name"] for c in delete_core.build_candidates(run)]
    assert "job1.inp" not in names
    assert "job1.out" in names
    assert "job1/job1.gbw" in names


def test_include_inputs_opt_in(tmp_path):
    run = make_run(tmp_path)
    names = [c["name"] for c in delete_core.build_candidates(run, include_inp=True)]
    assert "job1.inp" in names


def test_exclusions_roundtrip_and_filter(tmp_path):
    delete_core.save_excluded_folders(tmp_path, {"job1"})
    assert delete_core.load_excluded_folders(tmp_path) == {"job1"}

    run = make_run(tmp_path)
    names = [
        c["name"]
        for c in delete_core.build_candidates(
            run, delete_core.load_excluded_folders(tmp_path)
        )
    ]
    assert names == ["job1.out"]  # only the top-level file remains


def test_execute_deletions(tmp_path):
    run = make_run(tmp_path)
    candidates = delete_core.build_candidates(run)
    # keep the .out, delete the rest
    for candidate in candidates:
        candidate["decision"] = "Keep" if candidate["name"] == "job1.out" else "Delete"

    counts = delete_core.execute_deletions(candidates, run)

    assert counts["deleted"] == 1
    assert counts["kept"] == 1
    assert (run / "job1.out").is_file()
    assert not (run / "job1" / "job1.gbw").exists()
    assert not (run / "job1").exists()  # emptied folder removed


def test_folder_mode_candidates(tmp_path):
    run = make_run(tmp_path)
    # a couple of KB so the folder total shows above one decimal of a KB
    (run / "job1" / "big.dat").write_text("x" * 3000)

    candidates = delete_core.build_folder_candidates(run)
    assert len(candidates) == 1
    candidate = candidates[0]
    assert candidate["type"] == "folder"
    assert candidate["name"] == "job1/"          # the .inp stays protected
    assert candidate["inp"] is None
    assert float(candidate["size_kb"]) > 0       # folder contents total

    # opting in attaches the run's companion .inp to the deletion
    candidates = delete_core.build_folder_candidates(run, include_inp=True)
    assert candidates[0]["name"] == "job1/  (+ .inp)"
    assert candidates[0]["inp"] == run / "job1.inp"


def test_folder_mode_execution(tmp_path):
    run = make_run(tmp_path)
    (run / "job2").mkdir()
    (run / "job2" / "job2.out").write_text("ORCA TERMINATED NORMALLY")
    (run / "job2.inp").write_text("! HF")

    # protected .inp: folders go, both inputs stay
    candidates = delete_core.build_folder_candidates(run)
    counts = delete_core.execute_deletions(candidates, run)
    assert counts == {"deleted": 2, "kept": 0, "errors": 0}
    assert not (run / "job1").exists()
    assert not (run / "job2").exists()
    assert (run / "job1.inp").is_file()
    assert (run / "job2.inp").is_file()

    # opted in: rebuilding is impossible (folders gone); fresh run instead
    (run / "job3").mkdir()
    (run / "job3" / "job3.out").write_text("x")
    (run / "job3.inp").write_text("! HF")
    candidates = delete_core.build_folder_candidates(run, include_inp=True)
    counts = delete_core.execute_deletions(candidates, run)
    assert counts["deleted"] == 1
    assert not (run / "job3").exists()
    assert not (run / "job3.inp").exists()       # companion input deleted too

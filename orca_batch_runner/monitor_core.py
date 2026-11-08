"""Pure scan/analyze logic for ORCA output files (no GUI, no rendering)."""

import logging
import re
import subprocess
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)

MAX_RUNS = 30

# mtime/size keyed caches so unchanged files are not re-parsed on every scan
_last_size = {}
_analysis_cache = {}

TAIL_MIN_BYTES = 25_000_000       # minimum tail read per .out file
TAIL_FRACTION = 0.40              # large files: read the final 40%
TAIL_MAX_BYTES = 120_000_000      # memory cap per file

BACKWARD_STEP = 8_000_000         # chunk size when searching backwards
BACKWARD_BUDGET = 40_000_000      # cap for the backward search

CONVERGENCE_RE = re.compile(
    r"^\s*(Energy change|RMS gradient|MAX gradient|RMS step|MAX step)\s+"
    r"(-?\d+\.\d+)\s+(\d+\.\d+)\s+(YES|NO)\s*$"
)
FREQ_RE = re.compile(r"^\s*\d+:\s*(-?\d+\.\d+)\s*cm\*\*-1", re.MULTILINE)
CYCLE_RE = re.compile(r"GEOMETRY OPTIMIZATION CYCLE\s+(\d+)")
ENERGY_RE = re.compile(r"FINAL SINGLE POINT ENERGY\s+(-?\d+\.\d+)")
ZPE_RE = re.compile(r"Zero point energy[\s:.]*(-?\d+\.\d+)\s*Eh", re.IGNORECASE)
SCF_RE = re.compile(r"SCF CONVERGED AFTER\s+(\d+)\s+CYCLES")
SCAN_STEP_RE = re.compile(r"RELAXED SURFACE SCAN STEP\s+(\d+)")
GOAT_ITER_RE = re.compile(r"GOAT Global Iter\s+(\d+)")
GOAT_CONF_RE = re.compile(r"Conformers below 3 kcal/mol:\s*(\d+)")
GOAT_BEST_RE = re.compile(r"Lowest energy conformer\s*:\s*(-?\d+\.\d+)\s*Eh")
GOAT_SCONF_RE = re.compile(r"Sconf at 298\.15 K\s*:\s*(-?\d+\.\d+)")
GOAT_TABLE_RE = re.compile(
    r"^\s*\d+\s+(-?\d+\.\d+)\s+\d+\.\d+\s+-?\d+\.\d+\s*$", re.MULTILINE
)
RUNTIME_RE = re.compile(r"TOTAL RUN TIME:\s*(.+)")
SCF_MAXITER_RE = re.compile(r"Maximum # iterations\s+MaxIter\s*\.+\s*(\d+)")
SCF_TOLE_RE = re.compile(r"Energy Change\s+TolE\s*\.+\s*([\d.eE+-]+)")
SCF_TOLERR_RE = re.compile(r"DIIS Error\s+TolErr\s*\.+\s*([\d.eE+-]+)")
SCF_ROW_RE = re.compile(
    r"^\s*(\d+)\s+(-?\d+\.\d+)\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.MULTILINE
)
SOSCF_TOL_RE = re.compile(r"\(grad\. norm\)\s*\.+\s*([\d.eE+-]+)")
SCF_FINAL_RE = re.compile(
    r"^\s*Last (Energy change|MAX-Density change|RMS-Density change|DIIS Error|Orbital Gradient|Orbital Rotation)"
    r"\s+\.\.\.\s+(-?[\d.]+(?:[eE][+-]?\d+)?)\s+Tolerance\s+:\s+([\d.]+(?:[eE][+-]?\d+)?)",
    re.MULTILINE,
)
# Analytic Hessian progress (SHARK CP-SCF):
#   ITERATION  8:   ||err||_max =   3.7654e-05 (  84.2 sec  84/ 90 done)
CPSCF_ITER_RE = re.compile(
    r"ITERATION\s+(\d+):\s*\|\|err\|\|_max\s*=\s*([\d.eEdD+-]+)"
    r"\s*\(\s*([\d.]+)\s*sec\s+(\d+)/\s*(\d+)\s+done"
)
CPSCF_TOL_RE = re.compile(r"Convergence Tolerance\s*\.{3}\s*([\d.eE+-]+)")
CPSCF_SOLVED_RE = re.compile(r"CP-SCF equations solved in\s+([\d.]+)\s*sec")
# Hessian prelude sub-steps:
#   => Chain of spheres response operator  ... done (  971.6 sec)
SUBSTEP_RE = re.compile(
    r"^\s*=>\s+(.*(?:derivative|response|U-coefficients).*?)\s*\.\.\.\s*done\s*\(\s*([\d.]+)\s*sec",
    re.MULTILINE,
)
# Hessian prelude batches: "BATCH   1: Atoms 26 - 29 ( 12 perturbations)"
# ORCA does not announce the total: it is ESTIMATED as 3*nuclei divided by the
# perturbations of the first batch (the dispatcher fills every batch equally).
BATCH_RE = re.compile(
    r"^BATCH\s+(\d+):\s*Atoms\s+\d+\s*-\s*\d+\s*\(\s*(\d+)\s+perturbations\)",
    re.MULTILINE,
)
NUCLEI_RE = re.compile(
    r"Geometric perturbations\s*\.{3}\s*\w+\s*\(\s*(\d+)\s+nuclei\)"
)
GIBBS_FINAL_RE = re.compile(r"Final Gibbs free energy\s*\.{3}\s*(-?\d+\.\d+)")
SCORR_RE = re.compile(r"Total entropy correction\s*\.{3}\s*(-?\d+\.\d+)")


def read_tail(path, tail_bytes=None):
    """Read the .out tail. Adaptive window: the max of TAIL_MIN_BYTES and 40%
    of the file, so huge OPT+FREQ runs (whose Hessian section is tens of MB)
    do not push the convergence table out of the window."""
    if tail_bytes is None:
        try:
            file_size = path.stat().st_size
        except OSError:
            file_size = TAIL_MIN_BYTES
        tail_bytes = min(
            TAIL_MAX_BYTES, max(TAIL_MIN_BYTES, int(file_size * TAIL_FRACTION))
        )
    with open(path, "rb") as f:
        f.seek(0, 2)
        size = f.tell()
        f.seek(max(0, size - tail_bytes))
        return f.read().decode("utf-8", errors="ignore")


def process_active(out_path):
    """True while some process holds the .out open. fuser on Linux, lsof on
    macOS; elsewhere analyze_run's growth/mtime heuristics carry the signal."""
    try:
        result = subprocess.run(
            ["fuser", "-s", str(out_path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=5,
        )
        return result.returncode == 0
    except FileNotFoundError:
        pass  # no fuser on this platform
    except Exception:  # noqa: BLE001 -- any failure simply means "not active"
        return False
    if sys.platform == "darwin":
        try:
            result = subprocess.run(
                ["lsof", "-t", "--", str(out_path)],
                capture_output=True, text=True, timeout=5, check=False,
            )
            return bool(result.stdout.strip())
        except Exception:  # noqa: BLE001
            return False
    return False


def detect_job_type(out_text, inp_text):
    """Classify the run as SP/OPT/FREQ/OPT+FREQ/OPTTS(+FREQ)/SCAN(+FREQ)/GOAT."""
    kl = inp_text.upper()
    if "GOAT" in kl or "A Global Optimizer Algorithm" in out_text:
        return "GOAT"
    # OptTS before OPT: "OPTTS" contains "OPT" and would masquerade as
    # OPT+FREQ; "Following TS mode number" gives it away without the input
    is_optts = "OPTTS" in kl or "Following TS mode number" in out_text
    is_opt = "OPT" in kl or "GEOMETRY OPTIMIZATION CYCLE" in out_text
    is_freq = ("NUMFREQ" in kl or re.search(r"\bFREQ", kl)
               or "VIBRATIONAL FREQUENCIES" in out_text)
    is_scan = "%GEOMSCAN" in kl.replace(" ", "") or "RELAXED SURFACE SCAN STEP" in out_text
    if is_scan:
        return "SCAN+FREQ" if is_freq else "SCAN"
    if is_optts:
        return "OPTTS+FREQ" if is_freq else "OPTTS"
    if is_opt and is_freq:
        return "OPT+FREQ"
    if is_opt:
        return "OPT"
    if is_freq:
        return "FREQ"
    return "SP"


def _previous_opt_window(path, end_offset):
    """Search BACKWARDS (8 MB steps, up to 40 MB) for the optimization
    criteria table and convergence line, ending at end_offset (where the
    frequency phase or the current tail window starts). The frequency phase
    with its UCX noise can separate both by tens of MB."""
    try:
        with open(path, "rb") as fh:
            fh.seek(0, 2)
            size = fh.tell()
            end = min(end_offset, size)
            read = 0
            while end > 0 and read < BACKWARD_BUDGET:
                start = max(0, end - BACKWARD_STEP)
                fh.seek(start)
                chunk = fh.read(end - start).decode("utf-8", errors="ignore")
                read += len(chunk)
                if "THE OPTIMIZATION HAS CONVERGED" in chunk or CONVERGENCE_RE.search(chunk):
                    # The criteria table PRECEDES the convergence line:
                    # include one more step backwards
                    start2 = max(0, start - BACKWARD_STEP)
                    fh.seek(start2)
                    previous = fh.read(start - start2).decode("utf-8", errors="ignore")
                    return previous + chunk
                end = start
    except OSError:
        pass
    return ""


def _find_input_for(out_path):
    """Locate the .inp of a .out; after sorting, inputs live in input_files/."""
    inp_path = out_path.with_suffix(".inp")
    if inp_path.exists():
        return inp_path
    for candidate in (
        out_path.parent.parent / (out_path.stem + ".inp"),
        out_path.parent.parent.parent / "input_files" / (out_path.stem + ".inp"),
    ):
        if candidate.exists():
            return candidate
    return inp_path


def _parse_criteria(text):
    criteria = []
    for line in text.splitlines():
        m = CONVERGENCE_RE.match(line)
        if m:
            criteria.append({
                "item": m.group(1), "value": m.group(2),
                "tol": m.group(3), "ok": m.group(4) == "YES",
            })
    return criteria


def analyze_run(out_path):
    """Parse one .out into a run record with a status (RUN/RDY/FAIL/STALE)."""
    try:
        st = out_path.stat()
        mtime, size = st.st_mtime, st.st_size
    except OSError:
        return None

    key = str(out_path)
    info = _analysis_cache.get(key)
    if not (info and info["mtime"] == mtime and info["size"] == size):
        inp_path = _find_input_for(out_path)
        inp_text = (inp_path.read_text(encoding="utf-8", errors="ignore")
                    if inp_path.exists() else "")

        try:
            text = read_tail(out_path)
        except OSError:
            return None
        job_type = detect_job_type(text, inp_text)

        criteria = _parse_criteria(text)

        freqs = [float(m) for m in FREQ_RE.findall(text)]
        vibrational = [f for f in freqs if abs(f) >= 0.5]

        cycles = [int(m) for m in CYCLE_RE.findall(text)]
        scan_steps = [int(m) for m in SCAN_STEP_RE.findall(text)]
        scf_cycles = SCF_RE.findall(text)
        m_maxiter = SCF_MAXITER_RE.search(text)
        m_tole = SCF_TOLE_RE.search(text)
        m_tolerr = SCF_TOLERR_RE.search(text)
        scf_row = None
        scf_soscf = False
        scf_table_at = text.rfind("Delta-E    RMSDP")
        if scf_table_at >= 0:
            scf_soscf = "MaxGrad" in text[scf_table_at: scf_table_at + 200]
            scf_rows = SCF_ROW_RE.findall(text[scf_table_at: scf_table_at + 60000])
            if scf_rows:
                scf_row = scf_rows[-1]
        m_soscf_tol = SOSCF_TOL_RE.search(text)
        scf_final = [m.groups() for m in SCF_FINAL_RE.finditer(text)][-6:]

        if not scf_cycles and not scf_final:
            # SCF info of early cycles can fall outside the tail window
            try:
                with open(out_path, "rb") as fh:
                    head = fh.read(15_000_000).decode("utf-8", errors="ignore")
            except OSError:
                head = ""
            if head:
                scf_cycles = SCF_RE.findall(head)
                scf_final = [m.groups() for m in SCF_FINAL_RE.finditer(head)][-6:]

        energies = ENERGY_RE.findall(text)
        zpe = ZPE_RE.search(text)

        scan_total = None
        m_scan = re.search(
            r"scan[^=]*=\s*[-\d.]+\s*,\s*[-\d.]+\s*,\s*(\d+)", inp_text, re.IGNORECASE
        )
        if m_scan:
            scan_total = int(m_scan.group(1))

        goat_iters = [int(x) for x in GOAT_ITER_RE.findall(text)]
        goat_best = None
        if goat_iters:
            window = text[text.rfind("GOAT Global Iter"):][:2000]
            rows = GOAT_TABLE_RE.findall(window)
            if rows:
                goat_best = rows[-1][0]
        m_conf = GOAT_CONF_RE.search(text)
        m_best = GOAT_BEST_RE.search(text)
        m_sconf = GOAT_SCONF_RE.search(text)
        runtime = RUNTIME_RE.findall(text)

        cpscf_iters = CPSCF_ITER_RE.findall(text)
        m_cpscf_tol = CPSCF_TOL_RE.search(text)
        m_cpscf_solved = CPSCF_SOLVED_RE.search(text)
        substeps = SUBSTEP_RE.findall(text)
        batches = [(int(m.group(1)), int(m.group(2)))
                   for m in BATCH_RE.finditer(text)]
        batch_marks = [m.start() for m in BATCH_RE.finditer(text)]
        # Sub-steps of the batch in progress: those printed after the last "BATCH n:"
        last_batch = batch_marks[-1] if batch_marks else -1
        substeps_batch = sum(1 for m in SUBSTEP_RE.finditer(text)
                             if m.start() > last_batch)
        m_nuclei = NUCLEI_RE.search(text)
        batches_estimated = None
        if m_nuclei and batches and int(m_nuclei.group(1)) > 0:
            total_pert = 3 * int(m_nuclei.group(1))
            batches_estimated = -(-total_pert // batches[0][1])  # ceil without math
        m_gfinal = GIBBS_FINAL_RE.search(text)
        m_scorr = SCORR_RE.search(text)

        converged_in_window = None
        # A pure FREQ input (no OPT) has no criteria table to look for
        inp_without_opt = bool(inp_text) and "OPT" not in inp_text.upper()
        if not criteria and not inp_without_opt and (
                "SHARK CP-SCF DRIVER" in text or cpscf_iters or substeps):
            marks = [text.find("SHARK CP-SCF DRIVER")]
            m_batch = re.search(r"^BATCH\s+\d+:", text, re.MULTILINE)
            if m_batch:
                marks.append(m_batch.start())
            m_sub = SUBSTEP_RE.search(text)
            if m_sub:
                marks.append(m_sub.start())
            positions = [p for p in marks if p >= 0]
            if positions or cpscf_iters:
                try:
                    with open(out_path, "rb") as fh:
                        fh.seek(0, 2)
                        total_size = fh.tell()
                    tail_start = max(0, total_size - len(text))
                    # Anchor: where the frequency phase starts if visible,
                    # else the start of the current tail window
                    end_offset = tail_start + min(positions) if positions else tail_start
                    window = _previous_opt_window(out_path, end_offset)
                    if window:
                        if not criteria:
                            criteria = _parse_criteria(window)
                        if criteria and job_type == "FREQ":
                            # The window revealed an optimization: it was OPT+FREQ
                            job_type = "OPT+FREQ"
                        if not cycles:
                            cycles = [int(x) for x in CYCLE_RE.findall(window)]
                        if "THE OPTIMIZATION HAS CONVERGED" in window:
                            converged_in_window = True
                        elif ("did not converge but reached the maximum number" in window
                              or "FAILED TO CONVERGE" in window.upper()):
                            converged_in_window = False
                except OSError:
                    pass

        # For scans, only the CURRENT step's segment decides convergence: a
        # banner from an earlier step must not leak into the running step
        segment = text
        last_scan_at = text.rfind("RELAXED SURFACE SCAN STEP")
        if last_scan_at >= 0:
            segment = text[last_scan_at:]
        converged = None
        if "THE OPTIMIZATION HAS CONVERGED" in segment:
            converged = True
        elif ("did not converge but reached the maximum number" in segment
              or "FAILED TO CONVERGE" in segment.upper()):
            converged = False
        if converged is None and converged_in_window is not None:
            converged = converged_in_window

        info = {
            "mtime": mtime,
            "size": size,
            "name": out_path.stem,
            "path": out_path,
            "type": job_type,
            "terminated": "ORCA TERMINATED NORMALLY" in text,
            "fatal_error": "ORCA finished by accident" in text,
            "converged": converged,
            "cycles": max(cycles) if cycles else (len(criteria) if criteria else 0),
            "criteria": criteria[-5:] if criteria else [],
            "energy": energies[-1] if energies else None,
            "zpe": zpe.group(1) if zpe else None,
            "freqs": vibrational,
            "imaginary": sorted(f for f in vibrational if f < 0),
            "scf_cycles": scf_cycles[-1] if scf_cycles else None,
            "scf_iter": int(scf_row[0]) if scf_row else None,
            "scf_delta": scf_row[2] if scf_row else None,
            "scf_rmsdp": scf_row[3] if scf_row else None,
            "scf_diis": scf_row[5] if scf_row else None,
            "scf_maxiter": int(m_maxiter.group(1)) if m_maxiter else None,
            "scf_tole": m_tole.group(1) if m_tole else None,
            "scf_tolerr": m_tolerr.group(1) if m_tolerr else None,
            "scf_soscf": scf_soscf,
            "scf_tolgrad": m_soscf_tol.group(1) if m_soscf_tol else None,
            "scf_criteria": scf_final,
            "scf_failed": ("SCF NOT CONVERGED" in text.upper())
                          or ("The SCF did not converge" in text),
            "scan_steps": max(scan_steps) if scan_steps else 0,
            "scan_total": scan_total,
            "goat_iters": max(goat_iters) if goat_iters else 0,
            "goat_best": goat_best,
            "goat_conformers": m_conf.group(1) if m_conf else None,
            "goat_lowest": m_best.group(1) if m_best else None,
            "goat_sconf": m_sconf.group(1) if m_sconf else None,
            "cpscf_iter": int(cpscf_iters[-1][0]) if cpscf_iters else None,
            "cpscf_err": cpscf_iters[-1][1] if cpscf_iters else None,
            "cpscf_done": int(cpscf_iters[-1][3]) if cpscf_iters else None,
            "cpscf_total": int(cpscf_iters[-1][4]) if cpscf_iters else None,
            "cpscf_seconds": sum(float(m[2]) for m in cpscf_iters) if cpscf_iters else 0.0,
            "cpscf_tol": m_cpscf_tol.group(1) if m_cpscf_tol else None,
            "cpscf_solved": m_cpscf_solved.group(1) if m_cpscf_solved else None,
            # OptTS with Calc_Hess: completed Hessians so far (the initial
            # one plus recalcs; the final FREQ adds its own)
            "hessians_done": len(CPSCF_SOLVED_RE.findall(text)),
            # Hessian-only signals: the SHARK header or "=> ... done (sec)"
            # sub-steps. Plain "derivative integrals" text is NOT enough: it
            # also shows up in the gradient phase of an OPT run.
            "hessian_active": ("SHARK CP-SCF DRIVER" in text) or bool(substeps),
            "substeps_n": len(substeps),
            "substeps_seconds": sum(float(s) for _n, s in substeps),
            "batch_n": len(batch_marks),
            "substeps_batch": substeps_batch,
            "batches_estimated": (max(batches_estimated, len(batch_marks))
                                  if batches_estimated else None),
            "g_final": m_gfinal.group(1) if m_gfinal else None,
            "s_corr": m_scorr.group(1) if m_scorr else None,
            "runtime": runtime[-1].strip() if runtime else None,
        }
        _analysis_cache[key] = info

    previous = _last_size.get(key)
    grew = previous is not None and size > previous
    _last_size[key] = size
    age = time.time() - mtime
    running = (not info["terminated"]) and (
        process_active(out_path) or grew or 0 <= age < 15
    )

    result = {k: v for k, v in info.items()
              if k not in ("terminated", "fatal_error", "size")}

    if info["fatal_error"]:
        result["status"] = "FAIL"
    elif running:
        result["status"] = "RUNNING"
    elif info["converged"] is False:
        result["status"] = "FAIL"
    elif info["converged"] is True or info["terminated"]:
        result["status"] = "FINISHED"
    else:
        result["status"] = "STALE"
    return result


def collect_runs(folders):
    """Scan folders recursively for .out files; running jobs first, newest first."""
    outs = {}
    for base in folders:
        base = Path(base)
        if not base.exists():
            continue
        for out_path in base.rglob("*.out"):
            outs[out_path.resolve()] = out_path
    runs = [r for r in (analyze_run(p) for p in outs.values()) if r]
    runs.sort(key=lambda r: (r["status"] != "RUNNING", -r["mtime"]))
    return runs[:MAX_RUNS]


# --------------------------------------------------------------------------
# Detail rendering (per-run block, faithful to the original terminal monitor)
# --------------------------------------------------------------------------

def _criteria_line(criterion):
    """Aligned convergence row; scientific notation reads at a glance how far
    the value is from its tolerance. YES/NO matches ORCA's own tables."""
    mark = "YES" if criterion["ok"] else "NO"
    value = f"{float(criterion['value']):.2e}"
    tol = f"{float(criterion['tol']):.1e}"
    return f"      {criterion['item']:<15} {value:>12}  tol {tol}  {mark}"


def _scf_line(name, value, tol):
    try:
        v = float(value)
    except (TypeError, ValueError):
        return ""
    text = f"      SCF {name:<18} {v:>10.2e}"
    if tol:
        try:
            t = float(tol)
        except ValueError:
            return text
        ok = abs(v) <= t
        text += f"  tol {t:.1e}  " + ("YES" if ok else "NO")
    return text


def _stage_line(done, running, name, detail=""):
    """Checklist row of one frequency-calculation stage:
    [x] reached · [.] in progress · [ ] pending."""
    if done:
        mark = "[x]"
    elif running:
        mark = "[.]"
    else:
        mark = "[ ]"
    line = f"      {mark} {name:<30}"
    if detail:
        line += f"  {detail}"
    return line


def _frequency_block(run, running):
    """Stage checklist of the Hessian calculation, mirroring the original."""
    lines = []
    lines.append("  -- Frequency calculation (Hessian) -----------------------")
    has_freqs = bool(run["freqs"])
    cpscf_solved = run.get("cpscf_solved")

    # The Hessian only starts after the geometry has converged
    opt_pending = (
        run["type"] in ("OPT+FREQ", "SCAN+FREQ")
        and run.get("converged") is not True
        and not has_freqs and run["status"] != "FINISHED"
    )
    if opt_pending:
        detail = f" · cycle {run['cycles']}" if run["cycles"] else ""
        lines.append("      [ ] Waiting for the geometry optimization" + detail)

    # ---- stage checklist ----
    n_sub = run.get("substeps_n") or 0
    sub_min = (run.get("substeps_seconds") or 0.0) / 60
    integrals_done = bool(run.get("cpscf_total")) or cpscf_solved or has_freqs
    if integrals_done:
        detail = f"{n_sub}/{n_sub} sub-steps" if n_sub else ""
        lines.append(_stage_line(True, False, "Derivative integrals", detail))
    else:
        detail = ""
        if n_sub:
            batch = run.get("batch_n") or 0
            total_batches = run.get("batches_estimated")
            batch_label = f"{batch}/{total_batches}" if total_batches else f"{batch}"
            in_batch = run.get("substeps_batch")
            if batch and in_batch is not None:
                previous = n_sub - in_batch        # sub-steps of closed batches
                completed = batch - 1              # the current batch doesn't count
                if previous > 0 and completed > 0 and previous % completed == 0:
                    # closed batches reveal the batch size
                    size = previous // completed
                    detail = f"batch {batch_label} · {in_batch}/{size} sub-steps"
                elif batch > 1:
                    detail = f"batch {batch_label} · {in_batch} sub-steps"
                else:
                    detail = f"{in_batch} sub-steps"
            else:
                detail = f"{n_sub} sub-steps"
            if sub_min >= 0.1:
                detail += f" · {sub_min:.1f} min"
        lines.append(_stage_line(
            False, running and run.get("hessian_active"),
            "Derivative integrals", detail))

    if cpscf_solved:
        detail = ""
        if run.get("cpscf_err"):
            detail = f"final err {float(run['cpscf_err']):.1e}"
            if run.get("cpscf_tol"):
                detail += f" (tol {float(run['cpscf_tol']):.1e})"
        if run.get("cpscf_total"):
            detail += f" · {run['cpscf_done']}/{run['cpscf_total']} rhs"
        detail += f" · {float(cpscf_solved):.0f} s"
        lines.append(_stage_line(True, False, "CP-SCF equations", detail.strip(" ·")))
    elif run.get("cpscf_total"):
        detail = f"err {float(run['cpscf_err']):.1e}"
        if run.get("cpscf_tol"):
            detail += f" (tol {float(run['cpscf_tol']):.1e})"
        detail += f" · {run['cpscf_done']}/{run['cpscf_total']} rhs"
        minutes = run["cpscf_seconds"] / 60
        if minutes >= 1:
            detail += f" · {minutes:.0f} min"
        if 0 < run["cpscf_done"] < run["cpscf_total"]:
            # Linear ETA from the accumulated rate: the "how much is left" hint
            remaining = run["cpscf_seconds"] * (run["cpscf_total"]
                                                - run["cpscf_done"]) / run["cpscf_done"]
            detail += f" · ~{remaining / 60:.0f} min left"
        lines.append(_stage_line(False, running, "CP-SCF equations", detail))
    else:
        detail = f"tol {float(run['cpscf_tol']):.1e}" if run.get("cpscf_tol") else ""
        lines.append(_stage_line(False, False, "CP-SCF equations", detail))

    lines.append(_stage_line(
        has_freqs, running and cpscf_solved, "Diagonalization and frequencies"))

    thermo_done = bool(run.get("zpe")) or bool(run.get("g_final"))
    thermo_detail = ""
    if thermo_done:
        parts = []
        if run.get("s_corr"):
            parts.append(f"Scorr {float(run['s_corr']):.4f}")
        if run.get("g_final"):
            parts.append(f"G {float(run['g_final']):.4f} Eh")
        thermo_detail = " · ".join(parts)
    lines.append(_stage_line(
        thermo_done, running and has_freqs, "Thermochemistry", thermo_detail))

    # ---- final result ----
    if has_freqs:
        if not running:
            lines.append("      Frequency calculation complete")
        n_imag = len(run["imaginary"])
        imag_text = (f"{n_imag} IMAGINARY: "
                     + ", ".join(f"{f:.1f}" for f in run["imaginary"])
                     if n_imag else "0 imaginary")
        lines.append(f"      Frequencies: {len(run['freqs'])} | {imag_text}")
        extra = f"lowest: {min(run['freqs']):.1f} cm-1"
        if run["zpe"]:
            extra += f" | ZPE: {run['zpe']} Eh"
        lines.append(f"      {extra}")
    elif not running:
        lines.append("      Incomplete Hessian (no frequencies)")
    return lines


def _optts_block(run, running):
    """Transition state search checklist. The ORDER differs from OPT+FREQ:
    with Calc_Hess the initial Hessian runs BEFORE cycle 1 and is recomputed
    every N cycles; the frequencies only come at the end."""
    lines = []
    has_freqs = bool(run["freqs"])
    k_hess = run.get("hessians_done") or 0
    optts_ok = run.get("converged") is True or has_freqs

    lines.append("  -- Transition state search (OptTS) ------------------------")

    # Initial Hessian (and Calc_Hess recomputes)
    hess_done = k_hess > 0 or run["cycles"] > 0 or has_freqs
    hess_running = (not hess_done and running) or (
        running and not optts_ok and run.get("cpscf_total")
        and not run.get("cpscf_solved"))
    if hess_running and run.get("cpscf_total"):
        detail = f"err {float(run['cpscf_err']):.1e}"
        if run.get("cpscf_tol"):
            detail += f" (tol {float(run['cpscf_tol']):.1e})"
        detail += f" · {run['cpscf_done']}/{run['cpscf_total']} rhs"
        lines.append(_stage_line(False, True, "Hessian (initial/recalc)", detail))
    else:
        detail = f"{k_hess} done" if k_hess else ""
        lines.append(_stage_line(hess_done, hess_running,
                                 "Hessian (initial/recalc)", detail))

    # TS optimization: pending until cycle 1 exists (the initial Hessian
    # runs first)
    detail = f"cycle {run['cycles']}" if run["cycles"] else ""
    lines.append(_stage_line(
        optts_ok, running and not optts_ok and run["cycles"] > 0,
        "TS optimization", detail))

    # Final frequencies and thermochemistry
    lines.append(_stage_line(has_freqs, running and optts_ok, "Final frequencies"))
    lines.append(_stage_line(
        bool(run.get("zpe")) or bool(run.get("g_final")),
        running and has_freqs, "Thermochemistry"))

    if has_freqs:
        n_imag = len(run["imaginary"])
        lowest = min(run["freqs"])
        if n_imag == 1:
            lines.append(f"      TS CONFIRMED · imaginary mode: "
                         f"{run['imaginary'][0]:.1f} cm-1")
        else:
            imag_text = (f"{n_imag} IMAGINARY: "
                         + ", ".join(f"{f:.1f}" for f in run["imaginary"])
                         if n_imag else "0 imaginary")
            lines.append(f"      Frequencies: {len(run['freqs'])} | {imag_text}"
                         f" (a TS needs exactly 1)")
        extra = f"lowest: {lowest:.1f} cm-1"
        if run["zpe"]:
            extra += f" | ZPE: {run['zpe']} Eh"
        if run.get("g_final"):
            extra += f" | G: {float(run['g_final']):.4f} Eh"
        lines.append(f"      {extra}")
    elif not running:
        lines.append("      Finished without final frequencies "
                     "(incomplete Hessian)")
    return lines


def render_detail(run):
    """The per-run detail block shown in the panel/tooltip, mirroring the
    layout of the original terminal monitor."""
    lines = [str(run["path"])]

    summary = []
    if run["type"] in ("OPT", "OPT+FREQ", "OPTTS", "OPTTS+FREQ", "GOAT") \
            and run["cycles"]:
        summary.append(f"cycle {run['cycles']}")
    if run["type"] == "SCAN" and run["scan_steps"]:
        total = f"/{run['scan_total']}" if run["scan_total"] else ""
        summary.append(f"step {run['scan_steps']}{total}")
    if run["type"] == "GOAT" and run["goat_iters"]:
        summary.append(f"global iter {run['goat_iters']}")
    if run.get("converged") and run["status"] == "RUNNING":
        summary.append("GEOM READY")
    if run["type"] == "SP" and run.get("scf_iter") is not None and not run["scf_cycles"]:
        total = f"/{run['scf_maxiter']}" if run.get("scf_maxiter") else ""
        summary.append(f"SCF iter {run['scf_iter']}{total}")
    elif run["scf_cycles"]:
        summary.append(f"SCF: {run['scf_cycles']} cycles")
    if run["runtime"] and run["status"] != "RUNNING":
        summary.append(f"t: {run['runtime']}")
    if run["energy"]:
        summary.append(f"E = {float(run['energy']):.6f} Eh")
    lines.append(f"{run['name']}  ({run['type']})   {run['status']}"
                 + (f"   {'   '.join(summary)}" if summary else ""))
    if run["scf_failed"]:
        lines.append("      WARNING: the SCF did not converge at some point")

    if run["type"] in ("OPT", "OPT+FREQ", "OPTTS", "OPTTS+FREQ", "GOAT",
                       "SCAN", "SCAN+FREQ"):
        if run["criteria"]:
            lines.extend(_criteria_line(c) for c in run["criteria"])
            if run.get("converged"):
                if any(not c["ok"] for c in run["criteria"]):
                    lines.append(
                        "      (ORCA declared CONVERGENCE; red-flag criteria are informative)"
                    )
                else:
                    lines.append("      CONVERGENCE")
        elif run["status"] == "RUNNING":
            lines.append("      (no criteria table yet: first cycle in progress)")

    if run["type"] == "GOAT":
        if run["goat_lowest"]:
            extras = [f"lowest E: {float(run['goat_lowest']):.6f} Eh"]
            if run["goat_conformers"]:
                extras.append(f"conformers <3 kcal/mol: {run['goat_conformers']}")
            if run["goat_sconf"]:
                extras.append(f"Sconf: {run['goat_sconf']} cal/(molK)")
            lines.append("      " + " | ".join(extras))
        elif run["goat_best"]:
            lines.append(f"      global search: best E so far "
                         f"{float(run['goat_best']):.6f} Eh")
        elif run["status"] == "RUNNING":
            lines.append("      initial optimization in progress...")

    if run["type"] == "SP":
        if run["scf_cycles"]:
            for name, value, tol in run.get("scf_criteria") or []:
                line = _scf_line(name, value, tol)
                if line:
                    lines.append(line)
            lines.append(f"      CONVERGED ({run['scf_cycles']} cycles)")
        elif run.get("scf_iter") is not None and run["status"] == "RUNNING":
            if run.get("scf_soscf"):
                grad_line = _scf_line("MaxGrad", run.get("scf_diis"),
                                      run.get("scf_tolgrad"))
            else:
                grad_line = _scf_line("DIISErr", run.get("scf_diis"),
                                      run.get("scf_tolerr"))
            for text in (
                _scf_line("Delta-E", run.get("scf_delta"), run.get("scf_tole")),
                grad_line,
                _scf_line("RMSDP", run.get("scf_rmsdp"), None),
            ):
                if text:
                    lines.append(text)
            maxiter = run.get("scf_maxiter")
            if maxiter and run["scf_iter"] >= 0.85 * maxiter:
                lines.append(f"      WARNING: iter {run['scf_iter']} of {maxiter}, "
                             f"close to the SCF limit")
        elif run["status"] == "RUNNING":
            lines.append("      preparing the SCF calculation...")
        elif run.get("scf_failed"):
            lines.append("      SP FAILED: SCF did not converge")
        else:
            lines.append("      SP HAS NOT FINISHED")

    if run["type"] in ("OPTTS", "OPTTS+FREQ"):
        lines.extend(_optts_block(run, run["status"] == "RUNNING"))
    elif run["type"] in ("FREQ", "OPT+FREQ", "SCAN+FREQ"):
        lines.extend(_frequency_block(run, run["status"] == "RUNNING"))

    return "\n".join(lines)

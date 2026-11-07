# Release Notes — ORCA Calculator 2026.11.04

Initial release of the MoleditPy ORCA Calculator plugin.

## What it does

Turns MoleditPy into a job-queue front end for a local ORCA installation
(tested with ORCA 6.1.1 on Linux):

- **Queue**: browse the workspace input folders, build an ordered queue of
  `.inp` files and launch it. Each job runs in its own scratch folder under
  `<workspace>/temp/` and its results are collected into `Run/<name>/` —
  on success the input file is moved into that folder too, so every run
  lives together with no loose duplicates.
- **Running**: live ORCA output with a big launcher-style completion banner;
  **Stop** (whole process tree), **Stop after current** and **Retry failed**.
- **Monitor**: status table (Running / Finished / FAIL / STALE) refreshed
  every 10 s in a background thread, with a per-job-type Progress column
  (SCF `n/max` for SP, cycles + criteria for OPT, Hessian `done/total` with
  ETA for FREQ, `step n/total` for relaxed scans, OptTS support) and a
  detail panel with the convergence tables, thermochemistry and the
  frequency-calculation stage checklist. A per-job **Open** button launches
  the `.out` in a fresh MoleditPy session (routed to the ORCA Result
  Analyzer plugin when installed).
- **Sort / Delete**: organize finished runs into the workspace project
  structure — Files mode (by file type, `.bibtex` kept under
  `Aux_files/bibtex/`, never deleted) or Folders mode (each run moves
  intact with its input) — and clean up leftovers with inputs protected by
  default.

## Platform support

Linux only (process-tree control reads `/proc`; the monitor uses `fuser`).
Windows and macOS are not supported. Best-effort branches for the other
platforms exist in `proc.py` / `monitor_core.py` and are clearly marked.

## Testing

- 43 pytest tests (headless, offscreen Qt, fake ORCA executable) — all
  passing on Python 3.14 / Linux.
- End-to-end pipeline against a real ORCA binary
  (`tests/e2e_real_orca.py`, H2/HF/STO-3G): queue run, results collection,
  monitor analysis and sort — verified.

## Known limitations

- Only Linux is supported (see above).
- Monitor parsing verified for SP, OPT, FREQ, OPT+FREQ, relaxed SCAN and
  OptTS outputs; GOAT parsing is inherited and unit-tested only; TDDFT, NEB,
  IRC and other job types are not specially parsed.
- Stopping a queue off Linux would not guarantee killing ORCA's helper
  children (Linux walks `/proc` and kills the whole tree).

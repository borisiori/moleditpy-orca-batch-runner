# Registry request draft — Register New Plugin: ORCA Calculator

Fill the placeholders (`<...>`) after publishing the release, then submit at
https://github.com/HiroYokoyama/moleditpy-plugins/issues/new/choose using the
"Request Plugin Registration / Update" template. Metadata below must match the
`PLUGIN_*` constants in the release code exactly (the workflow validates
them, plus tag/version alignment and the SHA-256 of the asset).

---

**Title:** Register New Plugin: ORCA Calculator

- **Plugin Source URL:** `https://github.com/borisiori/moleditpy-orca-calculator/releases/download/v2026.11.04/orca_calculator.zip`
- **Target Action:** Register a New Plugin
- **Plugin ID:** `orca_calculator`
- **Expected SHA-256 Hash:** `bd51df9226c1f496227a54242b595350a115f410f4a966509705848df322c870`
- **Tags:** Optimization
- **Dependencies:** PySide6-Essentials
- **Visibility:** Yes, make this plugin visible in the registry manager.
- **Supported MoleditPy Version:** `>=5.0.0, <6.0.0`
- **Supported Python Version:** `>=3.10, <3.15`
- **Additional Information:**

  > Job queue manager for a local ORCA installation (tested with ORCA 6.1.1
  > on Linux): sequential queue with live output and stop controls, a
  > background monitor with per-job-type progress (SCF, optimization
  > criteria, analytic Hessian with ETA, relaxed scans, OptTS), and
  > Sort/Delete tools for the results workspace (two modes: by file type or
  > whole-run folders). Input files are preserved with their results, and
  > `.bibtex` references are kept, never deleted. Linux-only by design
  > (`/proc` process-tree control, `fuser`-based activity detection);
  > `PLUGIN_SUPPORTED_OS` declares it. Input generation is intentionally out
  > of scope (covered by ORCA Input Generator Pro); result visualization
  > delegates to ORCA Result Analyzer through the standard file-opener
  > registry.

---

## Pre-submission steps (checklist)

1. [ ] Create the GitHub repository (suggested name: `moleditpy-orca-calculator`).
2. [ ] Push this project (`.gitignore` already excludes caches and venvs).
3. [ ] Verify `PLUGIN_VERSION` (2026.11.04) in `orca_calculator/__init__.py`.
4. [ ] Commit and tag: `git tag v2026.11.04` (tag must equal PLUGIN_VERSION).
5. [ ] Build the asset from the tag:
       `git archive --format=zip -o orca_calculator.zip v2026.11.04 orca_calculator`
       (or zip the `orca_calculator/` folder from a clean checkout).
6. [ ] Create the GitHub Release `v2026.11.04` and attach `orca_calculator.zip`.
7. [ ] Compute the hash of the uploaded file: `shasum -a 256 orca_calculator.zip`.
8. [ ] Fill the placeholders above with the real asset URL and hash.
9. [ ] Submit the issue and mention the demo video/screenshots if desired.

> **Submitted**: https://github.com/HiroYokoyama/moleditpy-plugins/issues/59 (2026-11-07)

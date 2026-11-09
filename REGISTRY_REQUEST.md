# Registry request — Register New Plugin: ORCA Batch Runner

Submitted and under review: https://github.com/HiroYokoyama/moleditpy-plugins/issues/59
Renamed from "ORCA Calculator" following the maintainer's review; v2026.11.08
is the asset proposed for registration.

---

**Title:** Register New Plugin: ORCA Batch Runner

- **Plugin Source URL:** https://github.com/borisiori/moleditpy-orca-batch-runner/releases/download/v2026.11.08/orca_batch_runner.zip
- **Target Action:** Register a New Plugin
- **Plugin ID:** `orca_batch_runner`
- **Expected SHA-256 Hash:** 7a5df747dfab8cf219c1c331d67c8896584c870ff2f3f2af3402069872e6ba24
- **Tags:** Optimization
- **Dependencies:** PySide6-Essentials
- **Visibility:** Yes, make this plugin visible in the registry manager.
- **Supported MoleditPy Version:** >=5.0.0, <6.0.0
- **Supported Python Version:** >=3.10, <3.15
- **Additional Information:** job queue manager for a local ORCA installation;
  see the issue thread for the full description.

## Release procedure (for future versions)

1. Update `PLUGIN_VERSION` in `orca_batch_runner/__init__.py` (date format).
2. Commit on `main`; the CI suite must be green.
3. `git tag v<VERSION>` and push the tag.
4. `git archive --format=zip -o orca_batch_runner.zip v<VERSION> orca_batch_runner`
5. GitHub Release `v<VERSION>` attaching that zip; download it back and run
   `shasum -a 256` over the downloaded file.
6. Open an "Update an Existing Plugin" issue with the new URL + hash,
   keeping the Plugin ID.

> **Approved and registered: 2026-11-09.** Security review passed; tested by the
> maintainer on MoleditPy 5.1.3 (43 tests, load, mock end-to-end). The plugin is
> live in the official registry (id `orca_batch_runner`, visible) and appears in
> the Plugin Installer. Future updates: new version -> release -> "Update an
> Existing Plugin" issue with the same plugin ID.

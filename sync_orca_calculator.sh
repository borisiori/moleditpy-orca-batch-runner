#!/usr/bin/env bash
# Sync the ORCA Calculator plugin into the local MoleditPy plugins folder.
# (A symlink would not be discovered: plugin discovery walks without following links.)
set -euo pipefail

ROOT="$(cd "$(dirname "$0")" && pwd)"

rsync -a --delete \
  --exclude '__pycache__' \
  --exclude '.git' \
  --exclude 'tests' \
  "${ROOT}/orca_calculator/" "${HOME}/.moleditpy/plugins/orca_calculator/"

echo "Synced ${ROOT}/orca_calculator -> ${HOME}/.moleditpy/plugins/orca_calculator"

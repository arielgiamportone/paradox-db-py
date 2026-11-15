#!/usr/bin/env bash
# Runs the demo scripts for real and captures the terminal transcript used by build_video.py.
# Usage: run_demo.sh WORKDIR   (creates a venv, installs paradoxdb from PyPI, writes WORKDIR/transcript.txt)
set -euo pipefail
work="${1:?workdir}"
here="$(cd "$(dirname "$0")" && pwd)"
mkdir -p "$work" && cp "$here"/demo/*.py "$work"/ && cd "$work"
python3 -m venv .venv && . .venv/bin/activate && pip install -q --upgrade pip
export PIP_NO_CACHE_DIR=1 PIP_DISABLE_PIP_VERSION_CHECK=1
run() { echo "\$ $*"; "$@" 2>&1; echo; }
{
  run pip install paradoxdb
  run python 01_make_table.py
  run python 02_read.py
  echo '$ cat 03_cell_state.py'; cat 03_cell_state.py; echo
  run python 03_cell_state.py
  run paradoxdb profile demo_tables --output profile.json
  run python 04_summary.py
  run python 05_write.py
  run paradoxdb verify-index out/ASSETS.DB
} > transcript.txt 2>&1
echo "transcript written to $work/transcript.txt"

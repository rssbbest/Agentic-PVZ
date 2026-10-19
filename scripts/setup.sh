#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")/.."
if command -v uv >/dev/null 2>&1; then
  if [ ! -x .venv/bin/python ]; then uv venv --python 3.11 .venv; fi
  uv pip install --python .venv/bin/python -r requirements.txt
else
  task_python="${AUTOPILOT_PYTHON:-python3}"
  "$task_python" -c 'import sys; assert (3,10) <= sys.version_info[:2] <= (3,12), "Use Python 3.10-3.12 (3.11 tested), or install uv."'
  if [ ! -x .venv/bin/python ]; then "$task_python" -m venv .venv; fi
  .venv/bin/python -m pip install -r requirements.txt
fi
if [ ! -f .env ]; then
  cp .env.example .env
  chmod 600 .env
fi
echo "Setup complete. Edit .env, then run make portable or build Unity and run make start."

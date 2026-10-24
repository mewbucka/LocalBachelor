#!/usr/bin/env bash
# Quality gate (roadmap task 0.5): lint + tests + types.
# All three checks must pass; any failure exits non-zero immediately.
# Run from anywhere: the script anchors to the repo root itself.
set -euo pipefail

cd "$(dirname "$0")/.."

# Use the repo venv when present (Linux: .venv/bin, Windows: .venv/Scripts),
# so the pinned dev tools are used without activating anything.
if [ -x ".venv/bin/python" ]; then
  PY=.venv/bin/python
elif [ -x ".venv/Scripts/python.exe" ]; then
  PY=.venv/Scripts/python.exe
else
  PY=python3
fi

echo "==> ruff check app tests (lint)"
"$PY" -m ruff check app tests

echo "==> pytest -q (tests)"
"$PY" -m pytest -q

echo "==> mypy app (types)"
"$PY" -m mypy app

echo "OK: ruff, pytest, mypy all passed."

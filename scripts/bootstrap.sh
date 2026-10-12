#!/bin/sh
# All dependencies and pip downloads are confined to this checkout.
set -eu
cd "$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
"$PYTHON_BIN" -c 'import sys; assert sys.version_info >= (3, 12), "Python 3.12+ is required"'
"$PYTHON_BIN" -m venv .venv
.venv/bin/python -m pip install --cache-dir .local/pip-cache -r requirements-local.lock
.venv/bin/python -m pip install --cache-dir .local/pip-cache --no-deps -e .
printf '%s\n' 'Ready: .venv/bin/python scripts/dev.py start'

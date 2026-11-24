#!/bin/bash
# macOS / Linux one-click launcher: creates a virtualenv on first run, then starts QuantTerm.
cd "$(dirname "$0")"
if [ ! -d .venv ]; then
  python3 -m venv .venv && ./.venv/bin/pip install -q --upgrade pip && ./.venv/bin/pip install -q -r requirements.txt
fi
./.venv/bin/python run.py "$@"

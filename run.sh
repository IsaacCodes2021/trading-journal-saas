#!/usr/bin/env sh
# Start the Trading Journal. First run creates a Python environment and installs packages.
set -e
cd "$(dirname "$0")/backend"

if [ ! -x .venv/bin/python ]; then
    echo "Setting up for the first time..."
    # The project is tested on Python 3.12; prefer it when several versions are installed.
    if command -v python3.12 >/dev/null 2>&1; then python3.12 -m venv .venv; else python3 -m venv .venv; fi
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m tradejournal.ui "$@"

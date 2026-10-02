#!/usr/bin/env sh
# Start the Trading Journal. First run creates a Python environment and installs packages.
set -e
cd "$(dirname "$0")/backend"

if [ ! -x .venv/bin/python ]; then
    echo "Setting up for the first time..."
    python3 -m venv .venv
fi
.venv/bin/python -m pip install --quiet --disable-pip-version-check -r requirements.txt
exec .venv/bin/python -m tradejournal.ui "$@"

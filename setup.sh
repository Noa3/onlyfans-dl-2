#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
echo 'Creating a local .venv and installing desktop packages from PyPI.'
python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else "Python 3.10 or newer is required.")'
if [[ ! -x .venv/bin/python ]]; then
  python3 -m venv .venv
fi
.venv/bin/python -m pip install -r requirements.txt
if ! .venv/bin/python -c 'import tkinter'; then
  echo 'Install Tcl/Tk support for this Python. On Debian/Ubuntu this is normally python3-tk.' >&2
  exit 1
fi
echo 'Setup finished. Run bash start.sh. For Browser login, also run bash install_browser.sh.'

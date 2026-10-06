#!/usr/bin/env python3
"""Launch the desktop UI, or use --cli. Keep the ofdl folder beside this file."""
import sys

if sys.version_info < (3, 10):
    raise SystemExit('Python 3.10 or newer is required.')

if __name__ == '__main__':
    try:
        from ofdl.cli import main
        raise SystemExit(main())
    except ModuleNotFoundError as exc:
        name = exc.name or 'unknown'
        if name in {'tkinter','_tkinter'}:
            raise SystemExit('Tkinter is missing. Install Python with Tcl/Tk support (Debian/Ubuntu: python3-tk), or use --cli.') from None
        raise SystemExit(f'Missing dependency: {name}. Run setup_windows.bat or python -m pip install -r requirements.txt. Keep the ofdl folder beside this script.') from None

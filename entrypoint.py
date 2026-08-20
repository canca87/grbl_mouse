"""PyInstaller entry point.

PyInstaller needs a script with absolute imports as its target — app.py
itself uses relative imports (`from . import safety`, etc.) and can only
run as part of the `grbl_mouse` package (`python -m grbl_mouse.app`), not
as a standalone script. This just re-exposes the same `main()`.
"""

import sys

from grbl_mouse.app import main

if __name__ == "__main__":
    sys.exit(main())

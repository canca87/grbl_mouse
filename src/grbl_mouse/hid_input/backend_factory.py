"""Platform-based HidBackend selection.

macOS/Linux use the cross-platform `hidapi` library (hidapi_backend.py).
Windows needs a different mechanism entirely — see
win32_raw_input_backend.py's docstring for why `hidapi` doesn't work there
the same way (it doesn't preempt the OS's own mouse-class driver, unlike
macOS's IOHIDManager exclusive-open).
"""

from __future__ import annotations

import sys

from .backend import HidBackend


def make_hid_backend() -> HidBackend:
    if sys.platform == "win32":
        from .win32_raw_input_backend import Win32RawInputBackend

        return Win32RawInputBackend()

    from .hidapi_backend import HidApiBackend

    return HidApiBackend()

"""Decode raw HID input reports into dx, dy, wheel, and button state.

Empirically determined in milestone M1 from captures of this Kensington
Expert Mouse's usage_page=0x0001 usage=0x02 ("Mouse") HID collection (see
tests/fixtures/ for the raw capture and tests/test_report_parser.py for the
segments this was derived from). The report is 4 bytes:

    [buttons, dx, dy, wheel]

- buttons: bitmask, one bit per button (see Buttons below).
- dx: signed 8-bit, relative ball movement. Positive = right.
- dy: signed 8-bit, relative ball movement. Positive = down, negative = up.
- wheel: signed 8-bit, relative scroll-ring rotation. One detent = +-1.

Button bit assignments (physical layout, going clockwise from the left main
button):

    0x01  left main button   ("button 1")
    0x04  top-left button    ("button 2")
    0x08  top-right button   ("button 3")
    0x02  right main button  ("button 4")
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

_STRUCT = struct.Struct("<Bbbb")  # buttons (u8), dx, dy, wheel (i8 each)

BUTTON_LEFT_MAIN = 0x01
BUTTON_TOP_LEFT = 0x04
BUTTON_TOP_RIGHT = 0x08
BUTTON_RIGHT_MAIN = 0x02


@dataclass(frozen=True)
class MouseReport:
    buttons: int
    dx: int
    dy: int
    wheel: int

    def button_pressed(self, button: int) -> bool:
        return bool(self.buttons & button)


def decode(report: bytes) -> MouseReport:
    if len(report) < _STRUCT.size:
        raise ValueError(f"expected at least {_STRUCT.size} bytes, got {len(report)}")
    buttons, dx, dy, wheel = _STRUCT.unpack(report[: _STRUCT.size])
    return MouseReport(buttons=buttons, dx=dx, dy=dy, wheel=wheel)

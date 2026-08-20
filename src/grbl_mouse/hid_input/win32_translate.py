"""Pure translation logic: Windows Raw Input `RAWMOUSE` fields -> the same
4-byte `[buttons, dx, dy, wheel]` report format `report_parser.py` decodes
on macOS/Linux (via `hidapi`). Kept separate from
`win32_raw_input_backend.py`'s ctypes/Win32 plumbing specifically so this
part can be unit-tested without Windows — everything in this file is pure
Python with no ctypes/ctypes.wintypes dependency.

RAWMOUSE gives semantically-parsed data (Windows' own HID mouse-class
driver has already decoded button transitions and dx/dy) rather than raw
report bytes — Windows recognizes this device's top-level collection as a
standard "Mouse" and Raw Input never hands out undecoded bytes for a usage
type it has a built-in parser for (RIM_TYPEHID is only used for usage
types Windows doesn't recognize, e.g. vendor-defined ones). Translating
back into our own 4-byte format keeps report_parser.py/ButtonPressDetector/
debug_dump.py/etc. working unchanged on this platform too, instead of
needing a second parallel decoding path throughout the codebase.

Known assumptions, UNVERIFIED against real Windows hardware (no Windows
access at all when this was written — see the project's failure-mode notes
for how this was diagnosed from a bug report alone):

- Button mapping: Windows' RI_MOUSE_LEFT/RIGHT/MIDDLE/BUTTON_4 are assumed
  to correspond to this device's buttons 1-4 in the same first-declared-bit
  order our macOS raw-report capture found (left-main=0x01, right-main=
  0x02, top-left=0x04, top-right=0x08) — i.e. that Windows assigns semantic
  button labels in the same order the raw HID report descriptor declares
  them, which is standard behavior for a generic boot-protocol mouse but
  has not been confirmed for this specific device on Windows.
- Wheel scaling: RAWMOUSE.usButtonData for RI_MOUSE_WHEEL is assumed to be
  pre-scaled by WHEEL_DELTA (120), per Windows' documented wheel-message
  convention, so it's divided by 120 here to recover the "one detent = +-1"
  semantics report_parser.py expects. If this assumption is wrong, Z-axis
  jog will be either ~120x too sensitive (if it's not actually pre-scaled)
  or always zero (if it was already in detent units and got divided again).

If real-hardware testing shows either of these is wrong, fix it here only —
nothing else in the codebase needs to change, since this module's whole job
is producing bytes report_parser.py can decode correctly.
"""

from __future__ import annotations

import struct

from .report_parser import BUTTON_LEFT_MAIN, BUTTON_RIGHT_MAIN, BUTTON_TOP_LEFT, BUTTON_TOP_RIGHT

RI_MOUSE_LEFT_BUTTON_DOWN = 0x0001
RI_MOUSE_LEFT_BUTTON_UP = 0x0002
RI_MOUSE_RIGHT_BUTTON_DOWN = 0x0004
RI_MOUSE_RIGHT_BUTTON_UP = 0x0008
RI_MOUSE_MIDDLE_BUTTON_DOWN = 0x0010
RI_MOUSE_MIDDLE_BUTTON_UP = 0x0020
RI_MOUSE_BUTTON_4_DOWN = 0x0040
RI_MOUSE_BUTTON_4_UP = 0x0080
RI_MOUSE_WHEEL = 0x0400

WHEEL_DELTA = 120

_STRUCT = struct.Struct("<Bbbb")  # matches report_parser.py's format exactly

_DOWN_BITS = {
    RI_MOUSE_LEFT_BUTTON_DOWN: BUTTON_LEFT_MAIN,
    RI_MOUSE_RIGHT_BUTTON_DOWN: BUTTON_RIGHT_MAIN,
    RI_MOUSE_MIDDLE_BUTTON_DOWN: BUTTON_TOP_LEFT,
    RI_MOUSE_BUTTON_4_DOWN: BUTTON_TOP_RIGHT,
}
_UP_BITS = {
    RI_MOUSE_LEFT_BUTTON_UP: BUTTON_LEFT_MAIN,
    RI_MOUSE_RIGHT_BUTTON_UP: BUTTON_RIGHT_MAIN,
    RI_MOUSE_MIDDLE_BUTTON_UP: BUTTON_TOP_LEFT,
    RI_MOUSE_BUTTON_4_UP: BUTTON_TOP_RIGHT,
}


def _clamp_i8(value: int) -> int:
    return max(-128, min(127, value))


class ButtonStateTracker:
    """RAWMOUSE.usButtonFlags carries DOWN/UP *transition* events, not a
    held-state snapshot — report_parser.py/ButtonPressDetector expect the
    latter (the full "currently held" bitmask on every report, same as
    this device's real raw HID reports give directly on macOS/Linux). This
    reconstructs that snapshot from a stream of transition events.
    """

    def __init__(self) -> None:
        self.buttons = 0

    def apply(self, button_flags: int) -> int:
        for flag, bit in _DOWN_BITS.items():
            if button_flags & flag:
                self.buttons |= bit
        for flag, bit in _UP_BITS.items():
            if button_flags & flag:
                self.buttons &= ~bit
        return self.buttons


def _signed16(value: int) -> int:
    return value - 0x10000 if value >= 0x8000 else value


def build_report(*, buttons: int, last_x: int, last_y: int, button_flags: int, button_data: int) -> bytes:
    """Build one synthetic report matching report_parser.py's
    `[buttons, dx, dy, wheel]` format from one RAWMOUSE event's fields,
    plus the current (already-updated) held-button state from a
    ButtonStateTracker.
    """
    wheel = 0
    if button_flags & RI_MOUSE_WHEEL:
        wheel = _signed16(button_data) // WHEEL_DELTA
    return _STRUCT.pack(buttons, _clamp_i8(last_x), _clamp_i8(last_y), _clamp_i8(wheel))

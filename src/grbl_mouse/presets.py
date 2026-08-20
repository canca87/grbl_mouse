"""Button-driven jog sensitivity ("gain") control.

Per Option B (see project discussion — grounded in GRBL's own jogging.md
`s = v*dt` recommendation): feed rate is held fixed, and the buttons
instead step a gain multiplier that scales ball-roll counts into stage
distance, decoupling "how briskly motion feels" (constant, FIXED_FEEDRATE)
from "how far the ball's input reaches" (tunable here).

Left-main/right-main step gain down/up, clamped to [MIN_GAIN, MAX_GAIN].
Top-left/top-right stay unassigned for now.

All pure logic — safe to unit-test without hardware. Gain bounds/step are
a starting point; tune against real-machine feel.
"""

from __future__ import annotations

from .hid_input.report_parser import BUTTON_LEFT_MAIN, BUTTON_RIGHT_MAIN

BUTTON_DECREASE_GAIN = BUTTON_LEFT_MAIN
BUTTON_INCREASE_GAIN = BUTTON_RIGHT_MAIN

FIXED_FEEDRATE = 500.0  # mm/min — see grbl_link/velocity_jog.py

MIN_GAIN = 0.005
MAX_GAIN = 0.5
GAIN_STEP = 0.02
DEFAULT_GAIN = 0.1

# The scroll ring sends ~1 raw count per detent, vs. many counts for an
# equivalent ball roll, so Z needs its own multiplier to feel comparable to
# X/Y at the same gain. Starting guess — tune against real-machine feel.
Z_GAIN_MULTIPLIER = 8.0


def _clamp(gain: float) -> float:
    return max(MIN_GAIN, min(MAX_GAIN, gain))


class GainControl:
    """Tracks the current jog gain and steps it up/down, clamped to
    [MIN_GAIN, MAX_GAIN] in increments of GAIN_STEP.
    """

    def __init__(self, gain: float = DEFAULT_GAIN):
        self._gain = _clamp(gain)

    @property
    def current(self) -> float:
        return self._gain

    def increase(self) -> float:
        self._gain = _clamp(self._gain + GAIN_STEP)
        return self._gain

    def decrease(self) -> float:
        self._gain = _clamp(self._gain - GAIN_STEP)
        return self._gain


class ButtonPressDetector:
    """Detects new button-down edges across successive raw button bitmasks,
    so a held button triggers once rather than continuously.
    """

    def __init__(self) -> None:
        self._previous_buttons = 0

    def pressed_since_last(self, buttons: int) -> int:
        """Return the bitmask of buttons newly pressed since the last call."""
        newly_pressed = buttons & ~self._previous_buttons
        self._previous_buttons = buttons
        return newly_pressed


def handle_buttons(control: GainControl, newly_pressed: int) -> float | None:
    """Adjust `control`'s gain given this report's newly-pressed button
    bitmask (from ButtonPressDetector.pressed_since_last — computed once
    per report by the caller and shared with any other button handling, so
    edge state isn't tracked in two places). Returns the new gain if it
    changed, else None. If both buttons are newly pressed in the same
    report, increase takes priority.
    """
    if newly_pressed & BUTTON_INCREASE_GAIN:
        return control.increase()
    if newly_pressed & BUTTON_DECREASE_GAIN:
        return control.decrease()
    return None

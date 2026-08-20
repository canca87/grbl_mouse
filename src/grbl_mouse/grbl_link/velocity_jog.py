"""Velocity-based continuous jogging ("Option B").

Grounded in GRBL's own jogging.md recommendation: each segment's distance
is `s = v * dt`, where `dt` (TICK_INTERVAL, chosen by the caller — see
app.py) must be long enough for the segment to actually reach cruise speed
at the machine's configured acceleration. Too short, and every jog spends
its whole duration accelerating and is superseded before ever reaching
feed — which is what produced the "awkward wheeling motion needed for
smooth jog" feel with a 20ms tick. Feed rate is held fixed; the
button-adjustable "gain" (see presets.py) scales ball-roll distance into
stage distance, decoupling "how briskly motion feels" (constant) from "how
far the ball's input reaches" (tunable).

Two independent safety layers, following the postmortem of a real
joystick-jog "runaway" bug in Universal-G-Code-Sender (a single tracking
bug there left a physical machine jogging after input had stopped):

1. Direction-reversal detection: a sign flip on any axis sends a
   jog-cancel *before* the new-direction jog, rather than relying only on
   GRBL's own in-flight jog override.
2. An unconditional deadman timeout: if no HID report has arrived at all
   for `deadman_timeout`, force a cancel regardless of what the computed
   deltas say — independent of the has-motion tracking below, so a bug in
   *that* logic can't leave the machine jogging indefinitely.
"""

from __future__ import annotations

from typing import List, NamedTuple, Union


class SendAction(NamedTuple):
    dx: float
    dy: float
    dz: float


class CancelAction(NamedTuple):
    pass


TickAction = Union[SendAction, CancelAction]


def _opposite_sign(a: float, b: float) -> bool:
    return a != 0 and b != 0 and (a > 0) != (b > 0)


class VelocityJogController:
    def __init__(self, deadman_timeout: float):
        if deadman_timeout <= 0:
            raise ValueError(f"deadman_timeout must be positive, got {deadman_timeout}")
        self._deadman_timeout = deadman_timeout
        self._was_moving = False
        self._last_dx = 0.0
        self._last_dy = 0.0
        self._last_dz = 0.0
        self._last_input_time: float | None = None

    def tick(self, dx: float, dy: float, dz: float, had_input: bool, now: float) -> List[TickAction]:
        """Call once per fixed tick interval with this tick's summed,
        gain-scaled delta. `had_input` is True if at least one HID report
        was read this tick (regardless of its delta), used to drive the
        deadman timeout independently of dx/dy/dz.
        """
        if had_input:
            self._last_input_time = now

        deadman_expired = (
            self._last_input_time is not None and (now - self._last_input_time) > self._deadman_timeout
        )
        if deadman_expired:
            dx = dy = dz = 0.0

        has_motion = bool(dx or dy or dz)
        actions: List[TickAction] = []

        if has_motion:
            if self._was_moving and self._is_reversal(dx, dy, dz):
                actions.append(CancelAction())
            actions.append(SendAction(dx, dy, dz))
            self._last_dx, self._last_dy, self._last_dz = dx, dy, dz
            self._was_moving = True
        elif self._was_moving:
            actions.append(CancelAction())
            self._was_moving = False
            self._last_dx = self._last_dy = self._last_dz = 0.0

        return actions

    def _is_reversal(self, dx: float, dy: float, dz: float) -> bool:
        return (
            _opposite_sign(self._last_dx, dx)
            or _opposite_sign(self._last_dy, dy)
            or _opposite_sign(self._last_dz, dz)
        )

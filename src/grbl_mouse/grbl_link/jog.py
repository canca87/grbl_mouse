"""`$J=` jog command building, jog-cancel (0x85), and the `$X` alarm unlock.

Building a jog command string is pure logic — safe to unit-test without
hardware or motion authorization. Actually sending one goes through
`send_jog`, which requires `safety.require_motion_enabled()` to be true
first (see CLAUDE.md's hardware safety rule and safety.py) — it raises
MotionNotAuthorizedError, without writing anything to the serial port,
if motion hasn't been explicitly authorized for this run.

`cancel_jog` is the one exception: it's a safety-positive "stop moving"
action, so it is intentionally NOT gated by safety.py — it must always
work, including if motion was enabled and something has gone wrong.

`unlock_alarm` sends `$X` (kill alarm lock), which permits jogging without
a completed homing cycle. `resume_hold` sends `~` (cycle start/resume) to
continue a held (Hold state) cycle. Neither moves anything by itself, but
since both exist only to enable/continue subsequent motion, they're gated
the same as send_jog.
"""

from __future__ import annotations

from .. import safety
from .serial_link import SerialLink

JOG_CANCEL_BYTE = b"\x85"
CYCLE_START_RESUME_BYTE = b"~"


def build_jog_command(dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, feedrate: float = 100.0) -> str:
    """Build a `$J=` relative jog command string. Pure formatting — does not
    send anything. At least one axis delta must be non-zero.
    """
    if feedrate <= 0:
        raise ValueError(f"feedrate must be positive, got {feedrate}")
    axes = []
    if dx:
        axes.append(f"X{dx:g}")
    if dy:
        axes.append(f"Y{dy:g}")
    if dz:
        axes.append(f"Z{dz:g}")
    if not axes:
        raise ValueError("at least one axis delta must be non-zero")
    return f"$J=G91 G21 {' '.join(axes)} F{feedrate:g}"


def send_jog(link: SerialLink, dx: float = 0.0, dy: float = 0.0, dz: float = 0.0, feedrate: float = 100.0) -> None:
    """Send a relative jog command.

    Raises safety.MotionNotAuthorizedError, without writing to the serial
    port, unless motion has been explicitly authorized
    (safety.enable_motion()) for this run.
    """
    safety.require_motion_enabled()
    command = build_jog_command(dx, dy, dz, feedrate)
    link.send_line(command)


def cancel_jog(link: SerialLink) -> None:
    """Send the real-time jog-cancel byte (0x85).

    Always allowed regardless of safety.py's motion-enable state —
    cancelling motion must never be blocked.
    """
    link.write_realtime(JOG_CANCEL_BYTE)


def unlock_alarm(link: SerialLink) -> None:
    """Send `$X` (kill alarm lock) so jogging is permitted without homing.

    Gated the same as send_jog, since its only purpose is to enable
    subsequent motion.
    """
    safety.require_motion_enabled()
    link.send_line("$X")


def resume_hold(link: SerialLink) -> None:
    """Send `~` (cycle start/resume) to continue a held (Hold state) cycle.

    Gated the same as send_jog, since its only purpose is to let motion
    continue.
    """
    safety.require_motion_enabled()
    link.write_realtime(CYCLE_START_RESUME_BYTE)

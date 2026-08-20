"""Central choke point for anything that can move the physical stage.

Every code path that writes a motion command (jog, homing, raw G-code) to the
GRBL serial link must check `motion_enabled()` first and refuse to write if it
is False. Motion defaults to disabled; nothing enables it implicitly. See the
hardware safety rule in CLAUDE.md.
"""

from __future__ import annotations

_motion_enabled = False


class MotionNotAuthorizedError(RuntimeError):
    """Raised when motion-capable code runs without an explicit authorization."""


def motion_enabled() -> bool:
    return _motion_enabled


def enable_motion() -> None:
    """Explicitly authorize motion for the remainder of this process's run.

    Callers (e.g. the CLI) should only call this in direct response to the
    user explicitly authorizing physical motion for this session.
    """
    global _motion_enabled
    _motion_enabled = True


def disable_motion() -> None:
    global _motion_enabled
    _motion_enabled = False


def require_motion_enabled() -> None:
    """Raise if motion has not been explicitly authorized."""
    if not _motion_enabled:
        raise MotionNotAuthorizedError(
            "Motion is disabled by default. Call enable_motion() only in "
            "direct response to explicit user authorization."
        )

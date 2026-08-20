"""GRBL serial connection: welcome-message handling and response parsing.

Read-only in this milestone (M4): connecting, reading the startup welcome
message, and classifying ok/error/ALARM response lines. `write_realtime` is
only ever called here with the '?' status-query byte, which GRBL documents
as safe to send at any time — it doesn't queue or move anything. Anything
that can move the machine belongs in jog.py (M5), gated through
safety.py's motion-enable check; this module does not enforce that gate
itself, since it has no way to distinguish a status query from a future
motion command at this layer.

Depends only on the SerialTransport protocol (transport.py), not pyserial
directly, so it can be tested against a fake transport without a real
serial port. See pyserial_transport.py for the real implementation.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass

from .transport import SerialTransport

_WELCOME_RE = re.compile(r"^Grbl\s+(?P<version>\S+)\s*\[.*\]\s*$")

# GRBL 1.1's documented alarm codes (https://github.com/gnea/grbl/wiki/Grbl-v1.1-Interface).
ALARM_DESCRIPTIONS: dict[int, str] = {
    1: "Hard limit triggered.",
    2: "Soft limit: G-code motion target exceeds machine travel. Position retained.",
    3: "Reset while in motion or jog state.",
    4: "Probe fail: probe not in the expected initial state before the probing cycle.",
    5: "Probe fail: probe did not contact the workpiece within the programmed travel.",
    6: "Homing fail: the active homing cycle was reset.",
    7: "Homing fail: safety door was opened during the homing cycle.",
    8: "Homing fail: pull-off travel failed to clear the limit switch.",
    9: "Homing fail: could not find the limit switch within the search distance.",
}

# Alarms where GRBL's position tracking is likely wrong, not just motion
# blocked — re-homing (or at least a visual position check) is warranted
# before trusting relative jogs again, per GRBL's own alarm documentation.
POSITION_LOSS_ALARM_CODES = {1, 3, 6, 7, 8, 9}


class GrblError(RuntimeError):
    def __init__(self, code: int, raw: str):
        self.code = code
        self.raw = raw
        super().__init__(f"GRBL error:{code}")


class GrblAlarm(RuntimeError):
    def __init__(self, code: int, raw: str):
        self.code = code
        self.raw = raw
        self.description = ALARM_DESCRIPTIONS.get(code, "Unknown alarm code.")
        self.position_likely_lost = code in POSITION_LOSS_ALARM_CODES
        message = f"GRBL ALARM:{code} — {self.description}"
        if self.position_likely_lost:
            message += " Position may be lost; consider re-homing before continuing."
        super().__init__(message)


class GrblReset(RuntimeError):
    """Raised when GRBL's startup welcome banner appears unexpectedly
    mid-session — the board itself restarted (e.g. a power-cutting e-stop,
    brown-out, or manual reset), not just a GRBL-level alarm. Treat all
    previously-known state (position, alarm/unlock status) as invalid.
    """

    def __init__(self, raw: str):
        self.raw = raw
        super().__init__(f"GRBL appears to have reset (fresh welcome banner seen mid-session): {raw!r}")


@dataclass(frozen=True)
class Welcome:
    version: str
    raw: str


def parse_response_line(line: str) -> str | None:
    """Classify a single response line from GRBL.

    Returns None for 'ok'. Raises GrblError/GrblAlarm for error/ALARM
    lines — GRBL can push these *unprompted* (e.g. a hard-limit trip isn't
    necessarily the direct reply to whatever we just sent), so this is
    applied to every line read, not just ones we were specifically
    expecting a command response on (see query_status_line). Raises
    GrblReset if the line is GRBL's startup welcome banner, which
    shouldn't appear mid-session unless the board itself restarted.
    Returns the line unchanged for anything else (status reports,
    "[MSG:...]" feedback) so callers can decide what to do with it.
    """
    if line == "ok":
        return None
    if line.startswith("error:"):
        raise GrblError(int(line[len("error:"):]), line)
    if line.startswith("ALARM:"):
        raise GrblAlarm(int(line[len("ALARM:"):]), line)
    if _WELCOME_RE.match(line):
        raise GrblReset(line)
    return line


class SerialLink:
    def __init__(self, transport: SerialTransport):
        self._transport = transport

    def close(self) -> None:
        self._transport.close()

    def __enter__(self) -> SerialLink:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def read_line(self) -> str | None:
        raw = self._transport.readline()
        if not raw:
            return None
        return raw.decode("ascii", errors="replace").strip()

    def read_welcome(self, timeout: float = 5.0) -> Welcome:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line()
            if not line:
                continue
            match = _WELCOME_RE.match(line)
            if match:
                return Welcome(version=match.group("version"), raw=line)
        raise TimeoutError(
            f"no GRBL welcome message within {timeout:g}s. This can mean: "
            "(a) --port is wrong, (b) the board has no power or is held in reset "
            "(e.g. an e-stop that cuts power rather than just triggering a GRBL "
            "alarm), or (c) --baudrate doesn't match the board. These look "
            "identical from here — check the board's power/reset state and the "
            "port before assuming it's a software issue."
        )

    def write_realtime(self, command: bytes) -> None:
        """Write a single real-time command byte (only '?' in this milestone)."""
        self._transport.write(command)

    def send_line(self, line: str) -> str | None:
        """Write one line (command + newline) and return its single response.

        Returns None for 'ok'. Raises GrblError/GrblAlarm for error/ALARM
        responses. This is a generic mechanical write — it has no way to
        know whether `line` can cause motion, so any caller that might
        send a motion command (see jog.py) must check
        safety.require_motion_enabled() itself before calling this.
        """
        self._transport.write((line + "\n").encode("ascii"))
        response = self.read_line()
        if response is None:
            raise TimeoutError(f"no response from GRBL for: {line!r}")
        return parse_response_line(response)

    def send_line_multiline(self, line: str) -> list[str]:
        """Write one line and collect every response line up to and
        including the terminating 'ok' (e.g. for `$$`, which replies with
        many `$N=value` lines before 'ok'). Raises GrblError/GrblAlarm if
        an error/alarm arrives instead. Returns the intermediate lines.
        """
        self._transport.write((line + "\n").encode("ascii"))
        lines: list[str] = []
        while True:
            response = self.read_line()
            if response is None:
                raise TimeoutError(f"no response from GRBL for: {line!r}")
            classified = parse_response_line(response)
            if classified is None:
                return lines
            lines.append(classified)

    def query_status_line(self, timeout: float = 2.0) -> str:
        self.write_realtime(b"?")
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            line = self.read_line()
            if line is None:
                continue
            if line.startswith("<") and line.endswith(">"):
                return line
            # Not a status line - could be a stray 'ok'/feedback message
            # (benign, ignore and keep waiting), or an unprompted
            # ALARM/error/reset push, which must not be silently
            # discarded: without this, a hard-limit alarm or a board
            # reset that happens to arrive while we're polling status
            # would just look like GRBL never responded at all, with no
            # indication of what actually happened.
            parse_response_line(line)
        raise TimeoutError(
            f"no status response from GRBL within {timeout:g}s. If nothing else was "
            "reported first, this likely means the board has stopped responding "
            "entirely (e.g. power cut/held in reset) rather than just being in an "
            "alarm state — check the board's power/reset/e-stop status."
        )

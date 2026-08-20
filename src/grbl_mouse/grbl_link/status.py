"""GRBL `?` real-time status query and status report parsing.

Read-only; '?' is a real-time byte GRBL documents as safe to send at any
time, including mid-motion — it doesn't queue or move anything.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .serial_link import SerialLink

_STATUS_RE = re.compile(r"^<([^|>]+)(\|.*)?>$")


@dataclass(frozen=True)
class StatusReport:
    state: str
    raw: str
    fields: dict[str, str]

    @property
    def machine_position(self) -> tuple[float, float, float] | None:
        return self._position("MPos")

    @property
    def work_position(self) -> tuple[float, float, float] | None:
        return self._position("WPos")

    def _position(self, key: str) -> tuple[float, float, float] | None:
        value = self.fields.get(key)
        if value is None:
            return None
        parts = value.split(",")
        if len(parts) != 3:
            return None
        x, y, z = (float(p) for p in parts)
        return (x, y, z)


def parse_status_report(line: str) -> StatusReport:
    match = _STATUS_RE.match(line.strip())
    if not match:
        raise ValueError(f"not a GRBL status report: {line!r}")
    state = match.group(1)
    rest = match.group(2) or ""
    fields: dict[str, str] = {}
    for chunk in rest.split("|"):
        if not chunk or ":" not in chunk:
            continue
        key, value = chunk.split(":", 1)
        fields[key] = value
    return StatusReport(state=state, raw=line.strip(), fields=fields)


def query_status(link: SerialLink, timeout: float = 2.0) -> StatusReport:
    """Send the real-time status query and return the parsed report."""
    line = link.query_status_line(timeout=timeout)
    return parse_status_report(line)

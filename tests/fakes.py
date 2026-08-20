"""Shared fake SerialTransport for grbl_link tests — no real serial port."""

from __future__ import annotations


class FakeTransport:
    def __init__(self, lines: list[bytes] | None = None):
        self._lines = list(lines or [])
        self.written: list[bytes] = []
        self.closed = False

    def readline(self) -> bytes:
        if self._lines:
            return self._lines.pop(0)
        return b""

    def write(self, data: bytes) -> int:
        self.written.append(data)
        return len(data)

    def close(self) -> None:
        self.closed = True

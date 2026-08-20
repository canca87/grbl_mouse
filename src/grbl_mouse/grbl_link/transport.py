"""Minimal transport interface `SerialLink` depends on.

Kept separate from pyserial so `serial_link.py`/`status.py` can be tested
against a fake transport instead of a real serial port. See
pyserial_transport.py for the real pyserial-backed implementation.
"""

from __future__ import annotations

from typing import Protocol


class SerialTransport(Protocol):
    def readline(self) -> bytes:
        """Read one line (including the trailing newline), or b'' if no
        complete line arrived before the transport's own read timeout."""
        ...

    def write(self, data: bytes) -> int: ...

    def close(self) -> None: ...

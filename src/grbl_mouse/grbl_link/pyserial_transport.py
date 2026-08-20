"""Real serial port transport, backed by pyserial."""

from __future__ import annotations

import serial


class PySerialTransport:
    """SerialTransport implementation wrapping a real pyserial connection."""

    def __init__(self, port: str, baudrate: int = 115200, timeout: float = 2.0) -> None:
        self._serial = serial.Serial(port, baudrate=baudrate, timeout=timeout)

    def readline(self) -> bytes:
        return self._serial.readline()

    def write(self, data: bytes) -> int:
        return self._serial.write(data)

    def close(self) -> None:
        self._serial.close()

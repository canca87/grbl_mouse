"""CLI tool: dump GRBL's `$$` settings, highlighting the max-rate and
acceleration settings ($110-$132) needed to compute a safe jog tick
interval for the velocity-based jog controller (see velocity_jog.py).

Read-only — `$$` only requests a settings dump, it cannot move anything.

Usage:
    python -m grbl_mouse.grbl_link.debug_settings --port /dev/cu.usbmodem13201
"""

from __future__ import annotations

import argparse
import sys

from .pyserial_transport import PySerialTransport
from .serial_link import SerialLink

_ACCEL_RATE_KEYS = {
    "$110": "X max rate (mm/min)",
    "$111": "Y max rate (mm/min)",
    "$112": "Z max rate (mm/min)",
    "$120": "X acceleration (mm/sec^2)",
    "$121": "Y acceleration (mm/sec^2)",
    "$122": "Z acceleration (mm/sec^2)",
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="e.g. /dev/cu.usbmodem13201")
    parser.add_argument("--baudrate", type=int, default=115200)
    args = parser.parse_args(argv)

    transport = PySerialTransport(args.port, baudrate=args.baudrate)
    link = SerialLink(transport)
    try:
        welcome = link.read_welcome(timeout=10.0)
        print(f"Welcome: {welcome.raw}\n")
        lines = link.send_line_multiline("$$")

        print("Jog-relevant settings (max rate / acceleration):")
        for line in lines:
            key = line.split("=", 1)[0]
            if key in _ACCEL_RATE_KEYS:
                print(f"  {line:<12} {_ACCEL_RATE_KEYS[key]}")

        print("\nFull $$ dump:")
        for line in lines:
            print(f"  {line}")
    finally:
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

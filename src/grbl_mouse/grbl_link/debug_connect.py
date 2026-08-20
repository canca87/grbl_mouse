"""CLI tool: connect to GRBL over serial, read the welcome banner, and poll
status. Milestone M4 deliverable.

This tool is read-only: it only ever writes the '?' real-time status-query
byte. It never sends `$J=`, homing, or any G-code line, so no motion is
possible here regardless of safety.py's motion-enable state.

Usage:
    python -m grbl_mouse.grbl_link.debug_connect --list-ports
    python -m grbl_mouse.grbl_link.debug_connect --port /dev/cu.usbmodem1101

On macOS, prefer the /dev/cu.* device over /dev/tty.* for the same port —
cu. doesn't wait for carrier-detect and is the conventional choice for
programmatic access.
"""

from __future__ import annotations

import argparse
import sys
import time

from .pyserial_transport import PySerialTransport
from .serial_link import GrblAlarm, GrblError, SerialLink
from .status import query_status


def _list_ports() -> None:
    from serial.tools import list_ports

    ports = list(list_ports.comports())
    if not ports:
        print("No serial ports found.")
        return
    for p in ports:
        print(f"{p.device}  {p.description}")


def _run(port: str, baudrate: int, poll_seconds: float) -> None:
    transport = PySerialTransport(port, baudrate=baudrate)
    link = SerialLink(transport)
    try:
        print(f"Connected to {port} at {baudrate} baud.")
        print("Waiting for GRBL's welcome message (the board may be resetting)...")
        welcome = link.read_welcome(timeout=10.0)
        print(f"Welcome: {welcome.raw}\n")

        print(f"Polling status for {poll_seconds:.0f}s (read-only — no motion commands are sent)...\n")
        deadline = time.monotonic() + poll_seconds
        while time.monotonic() < deadline:
            try:
                report = query_status(link)
                print(f"  {report.state:<8} MPos={report.machine_position}")
            except (GrblError, GrblAlarm) as e:
                print(f"  {e}")
            time.sleep(0.5)
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        transport.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list-ports", action="store_true", help="list available serial ports and exit")
    parser.add_argument("--port", help="serial port, e.g. /dev/cu.usbmodem1101")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument("--poll-seconds", type=float, default=10.0, help="how long to poll status for")
    args = parser.parse_args(argv)

    if args.list_ports or not args.port:
        _list_ports()
        if not args.port:
            print("\nRe-run with --port <device> to connect.")
        return 0

    _run(args.port, args.baudrate, args.poll_seconds)
    return 0


if __name__ == "__main__":
    sys.exit(main())

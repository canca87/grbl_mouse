"""CLI tool: milestone M5 hardware gate — send small real jog moves.

THIS TOOL CAN MOVE THE PHYSICAL MACHINE. Before running it with
--confirm-motion:
  - Confirm GRBL's soft limits ($20-$22) are configured as expected.
  - Confirm the stage's travel area is clear.
  - Confirm an e-stop is within reach.

Without --confirm-motion, this tool only connects, reads the welcome
message and status, and exits — exactly as read-only as debug_connect.py.
--confirm-motion is required to send $X (unlock) or any $J= jog command.

Usage:
    python -m grbl_mouse.grbl_link.debug_jog --port /dev/cu.usbmodem13201
    python -m grbl_mouse.grbl_link.debug_jog --port /dev/cu.usbmodem13201 \
        --confirm-motion --unlock --axis X --distance 1 --feedrate 100
"""

from __future__ import annotations

import argparse
import sys
import time

from .. import safety
from .jog import cancel_jog, send_jog, unlock_alarm
from .pyserial_transport import PySerialTransport
from .serial_link import GrblAlarm, GrblError, SerialLink
from .status import query_status


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--port", required=True, help="e.g. /dev/cu.usbmodem13201")
    parser.add_argument("--baudrate", type=int, default=115200)
    parser.add_argument(
        "--confirm-motion",
        action="store_true",
        help="required to actually send $X/jog commands; without it, only connects and reports status",
    )
    parser.add_argument("--unlock", action="store_true", help="send $X to clear an Alarm state before jogging")
    parser.add_argument("--axis", choices=["X", "Y", "Z"], default="X")
    parser.add_argument("--distance", type=float, default=1.0, help="relative jog distance in mm")
    parser.add_argument("--feedrate", type=float, default=100.0, help="jog feedrate in mm/min")
    args = parser.parse_args(argv)

    transport = PySerialTransport(args.port, baudrate=args.baudrate)
    link = SerialLink(transport)
    try:
        print(f"Connected to {args.port} at {args.baudrate} baud.")
        welcome = link.read_welcome(timeout=10.0)
        print(f"Welcome: {welcome.raw}")

        report = query_status(link)
        print(f"Status: {report.state}  MPos={report.machine_position}\n")

        if not args.confirm_motion:
            print("--confirm-motion not given: stopping here. No motion sent.")
            return 0

        safety.enable_motion()
        print("Motion explicitly authorized for this run.\n")

        if args.unlock:
            print("Sending $X (kill alarm lock)...")
            unlock_alarm(link)
            report = query_status(link)
            print(f"Status after unlock: {report.state}  MPos={report.machine_position}\n")

        deltas = {"X": 0.0, "Y": 0.0, "Z": 0.0}
        deltas[args.axis] = args.distance
        print(f"Sending jog: axis={args.axis} distance={args.distance} feedrate={args.feedrate}")
        send_jog(link, dx=deltas["X"], dy=deltas["Y"], dz=deltas["Z"], feedrate=args.feedrate)

        time.sleep(0.5)
        report = query_status(link)
        print(f"Status after jog: {report.state}  MPos={report.machine_position}")

    except KeyboardInterrupt:
        print("\nInterrupted — sending jog-cancel.")
        cancel_jog(link)
    except (GrblError, GrblAlarm) as e:
        print(f"GRBL reported: {e}")
        return 1
    finally:
        safety.disable_motion()
        transport.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())

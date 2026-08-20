"""Milestone M3: hardware-confirmed OS pointer detachment via plain open().

Confirmed on the real device (macOS): opening the "Mouse" collection via
HidApiBackend already seizes it from the OS pointer pipeline — the system
cursor stops responding to the trackball/buttons while this tool holds the
device open, and normal behavior returns as soon as it's closed (Ctrl+C or
otherwise, via the `finally` block below). No separate grab/seize logic was
needed beyond HidApiBackend's default open() behavior. See
hidapi_backend.py for the note on why, and re-verify this assumption when
Windows/Linux backends are added later.

This tool just holds the device open and reads continuously, identically to
debug_dump.py's read loop, but is framed around confirming that behavior
rather than byte-layout inspection. It is read-only HID access — no
GRBL/serial involved, so this is safe to run without any motion-
authorization concerns.

Usage:
    python -m grbl_mouse.hid_input.exclusive_capture --vendor 0x047d \
        --product 0x1020 --usage-page 0x0001 --usage 0x02
"""

from __future__ import annotations

import argparse
import sys

from . import cli_common
from .backend import HidDeviceInfo
from .hidapi_backend import HidApiBackend


def _run_capture_test(backend: HidApiBackend, device: HidDeviceInfo, index: int) -> None:
    print(f"Opening {cli_common.format_device(index, device)}\n")
    backend.open(device)
    print("Device is open and being read continuously.")
    print("Now, WHILE THIS IS RUNNING:")
    print("  1. Roll the trackball and watch your on-screen system cursor.")
    print("     Does it move, or does it stay still?")
    print("  2. Try clicking a button. Does it register as a system click")
    print("     (e.g. in the Dock, or selecting text)?")
    print("Press Ctrl+C to stop, then check that the mouse drives the")
    print("system cursor normally again afterward.\n")
    report_count = 0
    try:
        while True:
            report = backend.read(timeout_ms=1000)
            if report is None:
                continue
            report_count += 1
            print(f"\rreports read: {report_count}", end="", flush=True)
    except KeyboardInterrupt:
        print("\n\nStopped. Closing device — check the system cursor now.")
    finally:
        backend.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    cli_common.add_device_selection_args(parser)
    args = parser.parse_args(argv)

    backend = HidApiBackend()
    devices, selected_index = cli_common.resolve_selected_device(backend, args)

    if not devices:
        print("No HID devices found.")
        return 1

    if not cli_common.has_selection_args(args) or selected_index is None:
        print("No single matching device selected. Available devices:\n")
        for i, d in enumerate(devices):
            print(cli_common.format_device(i, d))
        print("\nRe-run with --vendor/--product/--usage-page/--usage to select one.")
        return 0 if not cli_common.has_selection_args(args) else 1

    _run_capture_test(backend, devices[selected_index], selected_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())

"""CLI debug tool: enumerate HID devices and print raw input reports live.

Milestone M1 deliverable — identifies the Expert Mouse's VID/PID and lets us
empirically determine the raw report byte layout for X/Y motion, wheel, and
buttons. Read-only HID access only; no GRBL/serial involved, so this is safe
to run without any motion-authorization concerns.

Usage:
    python -m grbl_mouse.hid_input.debug_dump                # list devices
    python -m grbl_mouse.hid_input.debug_dump --name kensington
    python -m grbl_mouse.hid_input.debug_dump --vendor 0x047d --product 0x1020 \
        --usage-page 0x0001 --usage 0x02

See cli_common.py for why --vendor/--product/--usage-page/--usage (or
--name) is preferred over --index across separate runs.
"""

from __future__ import annotations

import argparse
import sys

from . import cli_common
from .backend import HidDeviceInfo
from .hidapi_backend import HidApiBackend


def _dump_reports(backend: HidApiBackend, device: HidDeviceInfo, index: int) -> None:
    print(f"Opening {cli_common.format_device(index, device)}")
    print("Reading raw reports. Move the ball, scroll the wheel, press buttons.")
    print("Press Ctrl+C to stop.\n")
    backend.open(device)
    try:
        while True:
            report = backend.read(timeout_ms=1000)
            if report is None:
                continue
            hex_bytes = " ".join(f"{b:02x}" for b in report)
            print(f"len={len(report):2d}  {hex_bytes}")
    except KeyboardInterrupt:
        print("\nStopped.")
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

    if not cli_common.has_selection_args(args):
        print(f"{len(devices)} HID device(s) found:\n")
        for i, d in enumerate(devices):
            print(cli_common.format_device(i, d))
        print("\nRe-run with --vendor/--product/--usage-page/--usage/--name to dump raw reports.")
        return 0

    if selected_index is None:
        print("No single matching device found. Matches:")
        for i, d in enumerate(devices):
            print(cli_common.format_device(i, d))
        return 1

    _dump_reports(backend, devices[selected_index], selected_index)
    return 0


if __name__ == "__main__":
    sys.exit(main())

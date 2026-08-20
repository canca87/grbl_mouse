"""Shared device-selection plumbing for the HID debug CLI tools.

macOS HID enumeration order is not guaranteed stable across separate
process invocations, so a plain list index only means something within the
same run's listing. Prefer selecting by vendor/product/usage_page/usage (or
a name substring), which match on the device's actual identity. When a
physical device exposes multiple HID collections (as the Expert Mouse
does), usage_page/usage is what tells them apart, since vendor/product/path
can be identical across collections. --serial disambiguates two physically
identical devices (same vendor/product/usage), when the hardware reports
one.
"""

from __future__ import annotations

import argparse

from .backend import HidBackend, HidDeviceInfo


def add_device_selection_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--index",
        type=int,
        help="select the device at this list index (only valid within the same run's listing; see module note)",
    )
    parser.add_argument("--vendor", type=lambda s: int(s, 0), help="filter by vendor id (e.g. 0x047d)")
    parser.add_argument("--product", type=lambda s: int(s, 0), help="filter by product id (e.g. 0x1020)")
    parser.add_argument("--usage-page", type=lambda s: int(s, 0), help="filter by HID usage page (e.g. 0x0001)")
    parser.add_argument("--usage", type=lambda s: int(s, 0), help="filter by HID usage (e.g. 0x02)")
    parser.add_argument("--serial", help="filter by exact serial number, to disambiguate two identical devices")
    parser.add_argument("--name", help="filter by substring of product/manufacturer string")


def has_selection_args(args: argparse.Namespace) -> bool:
    return any(
        v is not None
        for v in (args.index, args.vendor, args.product, args.usage_page, args.usage, args.serial, args.name)
    )


def format_device(index: int, device: HidDeviceInfo) -> str:
    usage = (
        f"usage_page=0x{device.usage_page:04x} usage=0x{device.usage:02x}"
        if device.usage_page is not None and device.usage is not None
        else "usage_page=? usage=?"
    )
    serial = f" serial={device.serial_number}" if device.serial_number else ""
    return (
        f"[{index}] VID=0x{device.vendor_id:04x} PID=0x{device.product_id:04x}  "
        f"{device.manufacturer_string or '?'} - {device.product_string or '?'}  "
        f"{usage}{serial}  path={device.path}"
    )


def select_device(
    devices: list[HidDeviceInfo],
    *,
    index: int | None,
    vendor_id: int | None,
    product_id: int | None,
    usage_page: int | None,
    usage: int | None,
    serial_number: str | None = None,
    name: str | None,
) -> int | None:
    """Return the index into `devices` of the single matching device, or None."""
    if index is not None:
        if 0 <= index < len(devices):
            return index
        return None

    candidate_indices = range(len(devices))
    if vendor_id is not None:
        candidate_indices = [i for i in candidate_indices if devices[i].vendor_id == vendor_id]
    if product_id is not None:
        candidate_indices = [i for i in candidate_indices if devices[i].product_id == product_id]
    if usage_page is not None:
        candidate_indices = [i for i in candidate_indices if devices[i].usage_page == usage_page]
    if usage is not None:
        candidate_indices = [i for i in candidate_indices if devices[i].usage == usage]
    if serial_number is not None:
        candidate_indices = [i for i in candidate_indices if devices[i].serial_number == serial_number]
    if name is not None:
        needle = name.lower()
        candidate_indices = [
            i
            for i in candidate_indices
            if needle in (devices[i].product_string or "").lower()
            or needle in (devices[i].manufacturer_string or "").lower()
        ]

    if len(candidate_indices) == 1:
        return candidate_indices[0]
    return None


def resolve_selected_device(
    backend: HidBackend, args: argparse.Namespace
) -> tuple[list[HidDeviceInfo], int | None]:
    """List devices and resolve `args`' selection flags to a single index.

    Returns (devices, selected_index). selected_index is None if there is
    no device list, no selection flags were given, or the flags don't
    uniquely match one device — callers should print `devices` in that case
    to help the user narrow it down.
    """
    devices = backend.list_devices()
    if not devices or not has_selection_args(args):
        return devices, None

    selected_index = select_device(
        devices,
        index=args.index,
        vendor_id=args.vendor,
        product_id=args.product,
        usage_page=args.usage_page,
        usage=args.usage,
        serial_number=args.serial,
        name=args.name,
    )
    return devices, selected_index

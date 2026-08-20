"""`hidapi`-based implementation of `HidBackend`.

Uses the `hidapi` PyPI package (cython-hidapi, imported as `hid`), which
ships prebuilt wheels for macOS/Windows/Linux and needs no separate native
library install. See CLAUDE.md's platform-abstraction rule: application
code should depend on `HidBackend` (backend.py), not on this module,
wherever practical.

Confirmed on macOS (milestone M3, hardware-verified): opening a device via
`open()` already seizes it from the OS pointer pipeline — while open, the
device stops driving the system cursor/clicks, and closing it (including
via the `finally` blocks in the CLI tools) restores normal behavior. No
separate "exclusive grab" mechanism was needed beyond this default `open()`
behavior. This has not yet been verified on Windows/Linux; when those
backends are added, re-confirm this assumption rather than carrying it over
unchecked.
"""

from __future__ import annotations

import hid

from .backend import HidDeviceInfo

_PATH_ENCODING_ERRORS = "surrogateescape"


def _decode_path(raw_path: bytes) -> str:
    return raw_path.decode("utf-8", errors=_PATH_ENCODING_ERRORS)


def _encode_path(path: str) -> bytes:
    return path.encode("utf-8", errors=_PATH_ENCODING_ERRORS)


class HidApiBackend:
    """HidBackend implementation backed by the `hidapi` package."""

    def __init__(self) -> None:
        self._device: hid.device | None = None

    def list_devices(self) -> list[HidDeviceInfo]:
        return [
            HidDeviceInfo(
                vendor_id=d["vendor_id"],
                product_id=d["product_id"],
                path=_decode_path(d["path"]),
                product_string=d.get("product_string") or None,
                manufacturer_string=d.get("manufacturer_string") or None,
                usage_page=d.get("usage_page"),
                usage=d.get("usage"),
                serial_number=d.get("serial_number") or None,
            )
            for d in hid.enumerate()
        ]

    def open(self, device: HidDeviceInfo) -> None:
        if self._device is not None:
            raise RuntimeError("device already open; call close() first")
        h = hid.device()
        h.open_path(_encode_path(device.path))
        self._device = h

    def read(self, timeout_ms: int | None = None) -> bytes | None:
        if self._device is None:
            raise RuntimeError("device not open; call open() first")
        data = self._device.read(64, timeout_ms if timeout_ms is not None else 0)
        return bytes(data) if data else None

    def close(self) -> None:
        if self._device is not None:
            self._device.close()
            self._device = None

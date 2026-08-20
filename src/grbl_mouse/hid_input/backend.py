"""Platform-abstracted raw HID access.

Application code should depend on this interface, not on a specific
platform's HID API, so that Windows/Linux backends can be added later
(see CLAUDE.md's platform-abstraction rule) without touching callers.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class HidDeviceInfo:
    vendor_id: int
    product_id: int
    path: str
    product_string: str | None = None
    manufacturer_string: str | None = None
    usage_page: int | None = None
    usage: int | None = None
    serial_number: str | None = None


class HidBackend(Protocol):
    """A raw HID device: enumerate, open, read reports, close."""

    def list_devices(self) -> list[HidDeviceInfo]:
        """Return all connected HID devices visible to this backend."""
        ...

    def open(self, device: HidDeviceInfo) -> None:
        """Open the given device for reading raw input reports."""
        ...

    def read(self, timeout_ms: int | None = None) -> bytes | None:
        """Read one raw input report, or None on timeout."""
        ...

    def close(self) -> None:
        """Close the device."""
        ...

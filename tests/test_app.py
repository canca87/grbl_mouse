import argparse

from grbl_mouse.app import JogSettings, _matching_hid_devices
from grbl_mouse.hid_input.backend import HidDeviceInfo

MOUSE_A = HidDeviceInfo(
    vendor_id=0x047D,
    product_id=0x1020,
    path="mouse-a-path",
    product_string="Expert Mouse",
    manufacturer_string="Kensington",
    usage_page=0x0001,
    usage=0x02,
    serial_number="AAA111",
)
MOUSE_B = HidDeviceInfo(
    vendor_id=0x047D,
    product_id=0x1020,
    path="mouse-b-path",
    product_string="Expert Mouse",
    manufacturer_string="Kensington",
    usage_page=0x0001,
    usage=0x02,
    serial_number="BBB222",
)
OTHER = HidDeviceInfo(
    vendor_id=0x05AC,
    product_id=0x0250,
    path="keyboard-path",
    usage_page=0x0001,
    usage=0x06,
)


def _match(devices, serial_number=None):
    return _matching_hid_devices(
        devices,
        vendor_id=0x047D,
        product_id=0x1020,
        usage_page=0x0001,
        usage=0x02,
        serial_number=serial_number,
    )


def test_single_match():
    assert _match([MOUSE_A, OTHER]) == [MOUSE_A]


def test_no_match():
    assert _match([OTHER]) == []


def test_two_identical_devices_both_match_without_serial():
    # Ambiguous - caller must require len() == 1 and reject this case.
    assert _match([MOUSE_A, MOUSE_B]) == [MOUSE_A, MOUSE_B]


def test_two_identical_devices_disambiguated_by_serial():
    assert _match([MOUSE_A, MOUSE_B], serial_number="BBB222") == [MOUSE_B]


def test_unknown_serial_matches_nothing():
    assert _match([MOUSE_A, MOUSE_B], serial_number="does-not-exist") == []


def test_jog_settings_from_args_copies_the_relevant_fields():
    args = argparse.Namespace(swap_xy=True, invert_x=False, invert_y=True, invert_z=False)
    settings = JogSettings.from_args(args)
    assert settings == JogSettings(swap_xy=True, invert_x=False, invert_y=True, invert_z=False)


def test_jog_settings_is_independently_mutable_from_args():
    args = argparse.Namespace(swap_xy=True, invert_x=False, invert_y=False, invert_z=False)
    settings = JogSettings.from_args(args)
    settings.invert_x = True
    assert args.invert_x is False  # the source args are untouched

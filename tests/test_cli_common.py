from grbl_mouse.hid_input import cli_common
from grbl_mouse.hid_input.backend import HidDeviceInfo

MOUSE = HidDeviceInfo(
    vendor_id=0x047D,
    product_id=0x1020,
    path="mouse-path",
    product_string="Expert Mouse",
    manufacturer_string="Kensington",
    usage_page=0x0001,
    usage=0x02,
)
MOUSE_POINTER_COLLECTION = HidDeviceInfo(
    vendor_id=0x047D,
    product_id=0x1020,
    path="mouse-path",  # same physical device, different HID collection
    product_string="Expert Mouse",
    manufacturer_string="Kensington",
    usage_page=0x0001,
    usage=0x01,
)
KEYBOARD = HidDeviceInfo(
    vendor_id=0x05AC,
    product_id=0x0250,
    path="keyboard-path",
    product_string="Apple Keyboard",
    manufacturer_string="Apple",
    usage_page=0x0001,
    usage=0x06,
)
DEVICES = [KEYBOARD, MOUSE, MOUSE_POINTER_COLLECTION]

# Two physically identical Expert Mice plugged in at once.
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
DUAL_DEVICES = [MOUSE_A, MOUSE_B]


def _select(
    devices=DEVICES, index=None, vendor_id=None, product_id=None, usage_page=None, usage=None, serial_number=None, name=None
):
    return cli_common.select_device(
        devices,
        index=index,
        vendor_id=vendor_id,
        product_id=product_id,
        usage_page=usage_page,
        usage=usage,
        serial_number=serial_number,
        name=name,
    )


def test_select_by_index():
    assert _select(index=1) == 1


def test_select_by_out_of_range_index_returns_none():
    assert _select(index=5) is None


def test_select_by_vendor_and_product_is_ambiguous_across_collections():
    # Both MOUSE and MOUSE_POINTER_COLLECTION share vendor/product/path.
    assert _select(vendor_id=0x047D, product_id=0x1020) is None


def test_select_by_usage_page_and_usage_disambiguates_collections():
    assert _select(vendor_id=0x047D, product_id=0x1020, usage_page=0x0001, usage=0x02) == 1
    assert _select(vendor_id=0x047D, product_id=0x1020, usage_page=0x0001, usage=0x01) == 2


def test_select_by_name_substring_case_insensitive_is_still_ambiguous():
    # "kensington" matches both collections of the same device.
    assert _select(name="kensington") is None
    assert _select(name="kensington", usage=0x02) == 1


def test_select_with_no_unique_match_returns_none():
    assert _select(name="apple") == 0  # only one Apple match, still unique
    assert _select(name="") is None  # empty needle matches everything, not unique


def test_select_two_identical_devices_is_ambiguous_without_serial():
    assert _select(devices=DUAL_DEVICES, vendor_id=0x047D, product_id=0x1020, usage_page=0x0001, usage=0x02) is None


def test_select_two_identical_devices_disambiguated_by_serial():
    result = _select(
        devices=DUAL_DEVICES,
        vendor_id=0x047D,
        product_id=0x1020,
        usage_page=0x0001,
        usage=0x02,
        serial_number="BBB222",
    )
    assert result == 1


def test_select_unknown_serial_matches_nothing():
    result = _select(devices=DUAL_DEVICES, serial_number="does-not-exist")
    assert result is None

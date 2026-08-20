from grbl_mouse.hid_input.report_parser import (
    BUTTON_LEFT_MAIN,
    BUTTON_RIGHT_MAIN,
    BUTTON_TOP_LEFT,
    BUTTON_TOP_RIGHT,
    decode,
)
from grbl_mouse.hid_input.win32_translate import (
    RI_MOUSE_LEFT_BUTTON_DOWN,
    RI_MOUSE_LEFT_BUTTON_UP,
    RI_MOUSE_MIDDLE_BUTTON_DOWN,
    RI_MOUSE_MIDDLE_BUTTON_UP,
    RI_MOUSE_RIGHT_BUTTON_DOWN,
    RI_MOUSE_RIGHT_BUTTON_UP,
    RI_MOUSE_WHEEL,
    ButtonStateTracker,
    build_report,
)


def test_button_state_tracker_starts_at_zero():
    assert ButtonStateTracker().buttons == 0


def test_button_down_sets_bit():
    tracker = ButtonStateTracker()
    assert tracker.apply(RI_MOUSE_LEFT_BUTTON_DOWN) == BUTTON_LEFT_MAIN


def test_button_up_clears_bit():
    tracker = ButtonStateTracker()
    tracker.apply(RI_MOUSE_LEFT_BUTTON_DOWN)
    assert tracker.apply(RI_MOUSE_LEFT_BUTTON_UP) == 0


def test_multiple_buttons_held_at_once():
    tracker = ButtonStateTracker()
    tracker.apply(RI_MOUSE_LEFT_BUTTON_DOWN)
    assert tracker.apply(RI_MOUSE_RIGHT_BUTTON_DOWN) == BUTTON_LEFT_MAIN | BUTTON_RIGHT_MAIN


def test_releasing_one_button_leaves_other_held():
    tracker = ButtonStateTracker()
    tracker.apply(RI_MOUSE_LEFT_BUTTON_DOWN | RI_MOUSE_RIGHT_BUTTON_DOWN)
    assert tracker.apply(RI_MOUSE_RIGHT_BUTTON_UP) == BUTTON_LEFT_MAIN


def test_middle_button_maps_to_top_left():
    tracker = ButtonStateTracker()
    assert tracker.apply(RI_MOUSE_MIDDLE_BUTTON_DOWN) == BUTTON_TOP_LEFT
    assert tracker.apply(RI_MOUSE_MIDDLE_BUTTON_UP) == 0


def test_unrelated_flags_are_ignored():
    tracker = ButtonStateTracker()
    assert tracker.apply(0) == 0


def test_build_report_roundtrips_through_report_parser_decode():
    report_bytes = build_report(buttons=BUTTON_LEFT_MAIN, last_x=5, last_y=-3, button_flags=0, button_data=0)
    decoded = decode(report_bytes)
    assert decoded.buttons == BUTTON_LEFT_MAIN
    assert decoded.dx == 5
    assert decoded.dy == -3
    assert decoded.wheel == 0


def test_build_report_wheel_scroll_forward_one_detent():
    report_bytes = build_report(buttons=0, last_x=0, last_y=0, button_flags=RI_MOUSE_WHEEL, button_data=120)
    assert decode(report_bytes).wheel == 1


def test_build_report_wheel_scroll_backward_one_detent():
    # -120 as an unsigned 16-bit value (two's complement).
    report_bytes = build_report(buttons=0, last_x=0, last_y=0, button_flags=RI_MOUSE_WHEEL, button_data=0x10000 - 120)
    assert decode(report_bytes).wheel == -1


def test_build_report_no_wheel_flag_ignores_button_data():
    report_bytes = build_report(buttons=0, last_x=0, last_y=0, button_flags=0, button_data=120)
    assert decode(report_bytes).wheel == 0


def test_build_report_clamps_large_positive_deltas():
    report_bytes = build_report(buttons=0, last_x=500, last_y=500, button_flags=0, button_data=0)
    decoded = decode(report_bytes)
    assert decoded.dx == 127
    assert decoded.dy == 127


def test_build_report_clamps_large_negative_deltas():
    report_bytes = build_report(buttons=0, last_x=-500, last_y=-500, button_flags=0, button_data=0)
    decoded = decode(report_bytes)
    assert decoded.dx == -128
    assert decoded.dy == -128

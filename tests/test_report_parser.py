"""Decoder tests against representative lines from the real M1 capture.

See tests/fixtures/expert_mouse_m1_capture_2026-08-18.txt for the full raw
capture and the action-sequence this was derived from.
"""

from grbl_mouse.hid_input.report_parser import (
    BUTTON_LEFT_MAIN,
    BUTTON_RIGHT_MAIN,
    BUTTON_TOP_LEFT,
    BUTTON_TOP_RIGHT,
    decode,
)


def _report(hex_bytes: str) -> bytes:
    return bytes.fromhex(hex_bytes.replace(" ", ""))


def test_decode_ball_roll_right_is_positive_dx():
    report = decode(_report("00 04 fe 00"))  # from the "roll ball right" segment
    assert report.dx == 4
    assert report.buttons == 0


def test_decode_ball_roll_left_is_negative_dx():
    report = decode(_report("00 fa 00 00"))  # from the "roll ball left" segment
    assert report.dx == -6


def test_decode_ball_roll_up_is_negative_dy():
    report = decode(_report("00 00 fd 00"))  # from the "roll ball up" segment
    assert report.dy == -3


def test_decode_ball_roll_down_is_positive_dy():
    report = decode(_report("00 00 05 00"))  # from the "roll ball down" segment
    assert report.dy == 5


def test_decode_scroll_ring_cw_is_negative_wheel_tick():
    report = decode(_report("00 00 00 ff"))  # from the "scroll ring CW" segment
    assert report.wheel == -1


def test_decode_scroll_ring_ccw_is_positive_wheel_tick():
    report = decode(_report("00 00 00 01"))  # from the "scroll ring CCW" segment
    assert report.wheel == 1


def test_decode_left_main_button():
    report = decode(_report("01 00 00 00"))
    assert report.buttons == BUTTON_LEFT_MAIN
    assert report.button_pressed(BUTTON_LEFT_MAIN)
    assert not report.button_pressed(BUTTON_TOP_LEFT)


def test_decode_top_left_button():
    report = decode(_report("04 00 00 00"))
    assert report.buttons == BUTTON_TOP_LEFT
    assert report.button_pressed(BUTTON_TOP_LEFT)


def test_decode_top_right_button():
    report = decode(_report("08 00 00 00"))
    assert report.buttons == BUTTON_TOP_RIGHT
    assert report.button_pressed(BUTTON_TOP_RIGHT)


def test_decode_right_main_button():
    report = decode(_report("02 00 00 00"))
    assert report.buttons == BUTTON_RIGHT_MAIN
    assert report.button_pressed(BUTTON_RIGHT_MAIN)


def test_decode_idle_report_is_all_zero():
    report = decode(_report("00 00 00 00"))
    assert (report.buttons, report.dx, report.dy, report.wheel) == (0, 0, 0, 0)

import pytest

from grbl_mouse.presets import (
    BUTTON_DECREASE_GAIN,
    BUTTON_INCREASE_GAIN,
    DEFAULT_GAIN,
    GAIN_STEP,
    MAX_GAIN,
    MIN_GAIN,
    ButtonPressDetector,
    GainControl,
    handle_buttons,
)


def test_default_gain():
    control = GainControl()
    assert control.current == DEFAULT_GAIN


def test_increase_steps_by_gain_step():
    control = GainControl(gain=0.1)
    assert control.increase() == pytest.approx(0.1 + GAIN_STEP)


def test_decrease_steps_by_gain_step():
    control = GainControl(gain=0.1)
    assert control.decrease() == pytest.approx(0.1 - GAIN_STEP)


def test_increase_clamps_at_max():
    control = GainControl(gain=MAX_GAIN)
    assert control.increase() == MAX_GAIN


def test_decrease_clamps_at_min():
    control = GainControl(gain=MIN_GAIN)
    assert control.decrease() == MIN_GAIN


def test_constructor_clamps_out_of_range_gain():
    assert GainControl(gain=100.0).current == MAX_GAIN
    assert GainControl(gain=-1.0).current == MIN_GAIN


def test_button_press_detector_only_fires_on_new_press():
    detector = ButtonPressDetector()
    assert detector.pressed_since_last(0x01) == 0x01
    assert detector.pressed_since_last(0x01) == 0x00
    assert detector.pressed_since_last(0x00) == 0x00
    assert detector.pressed_since_last(0x01) == 0x01


def test_handle_buttons_right_main_increases():
    control = GainControl(gain=0.1)
    result = handle_buttons(control, BUTTON_INCREASE_GAIN)
    assert result == pytest.approx(0.1 + GAIN_STEP)


def test_handle_buttons_left_main_decreases():
    control = GainControl(gain=0.1)
    result = handle_buttons(control, BUTTON_DECREASE_GAIN)
    assert result == pytest.approx(0.1 - GAIN_STEP)


def test_handle_buttons_unrelated_button_returns_none():
    control = GainControl()
    assert handle_buttons(control, 0x04) is None  # top-left, used for alarm-resume in app.py


def test_handle_buttons_no_bits_returns_none():
    control = GainControl()
    assert handle_buttons(control, 0x00) is None

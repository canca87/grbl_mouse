import pytest

from grbl_mouse.grbl_link.velocity_jog import CancelAction, SendAction, VelocityJogController


def test_rejects_non_positive_deadman_timeout():
    with pytest.raises(ValueError):
        VelocityJogController(deadman_timeout=0.0)
    with pytest.raises(ValueError):
        VelocityJogController(deadman_timeout=-1.0)


def test_no_input_no_motion_no_action():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    assert ctrl.tick(0.0, 0.0, 0.0, had_input=False, now=0.0) == []


def test_motion_sends():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    assert ctrl.tick(1.0, 2.0, 0.0, had_input=True, now=0.0) == [SendAction(1.0, 2.0, 0.0)]


def test_continued_same_direction_motion_just_sends():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.2) == [SendAction(1.0, 0.0, 0.0)]


def test_idle_after_motion_cancels_once():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(0.0, 0.0, 0.0, had_input=True, now=0.2) == [CancelAction()]
    assert ctrl.tick(0.0, 0.0, 0.0, had_input=True, now=0.4) == []


def test_direction_reversal_cancels_before_sending():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(5.0, 0.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(-3.0, 0.0, 0.0, had_input=True, now=0.2) == [
        CancelAction(),
        SendAction(-3.0, 0.0, 0.0),
    ]


def test_reversal_on_any_axis_triggers_cancel():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(1.0, 5.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(1.0, -5.0, 0.0, had_input=True, now=0.2) == [
        CancelAction(),
        SendAction(1.0, -5.0, 0.0),
    ]


def test_no_reversal_when_axis_goes_from_zero_to_nonzero():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(5.0, 0.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(5.0, 3.0, 0.0, had_input=True, now=0.2) == [SendAction(5.0, 3.0, 0.0)]


def test_reversal_after_idle_cancel_does_not_double_cancel():
    ctrl = VelocityJogController(deadman_timeout=1.0)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    ctrl.tick(0.0, 0.0, 0.0, had_input=True, now=0.2)  # idle cancel resets last direction
    assert ctrl.tick(-1.0, 0.0, 0.0, had_input=True, now=0.4) == [SendAction(-1.0, 0.0, 0.0)]


def test_deadman_forces_cancel_when_input_stops_even_if_deltas_stay_nonzero():
    ctrl = VelocityJogController(deadman_timeout=0.5)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    # No HID input arriving at all (had_input=False), but simulate a bug
    # where the caller still computed a nonzero delta - the deadman must
    # override this and force a stop once its timeout elapses.
    assert ctrl.tick(1.0, 0.0, 0.0, had_input=False, now=0.6) == [CancelAction()]


def test_deadman_does_not_fire_before_its_timeout():
    ctrl = VelocityJogController(deadman_timeout=0.5)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    assert ctrl.tick(1.0, 0.0, 0.0, had_input=False, now=0.3) == [SendAction(1.0, 0.0, 0.0)]


def test_deadman_resets_on_fresh_input():
    ctrl = VelocityJogController(deadman_timeout=0.5)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.0)
    ctrl.tick(1.0, 0.0, 0.0, had_input=True, now=0.4)  # fresh input, resets deadman clock
    # Without the reset, now=0.7 would be 0.7s since t=0.0 (> 0.5 timeout).
    # With the reset, it's only 0.3s since t=0.4 - should still send.
    assert ctrl.tick(1.0, 0.0, 0.0, had_input=False, now=0.7) == [SendAction(1.0, 0.0, 0.0)]


def test_deadman_with_no_input_ever_does_nothing():
    ctrl = VelocityJogController(deadman_timeout=0.5)
    assert ctrl.tick(0.0, 0.0, 0.0, had_input=False, now=10.0) == []

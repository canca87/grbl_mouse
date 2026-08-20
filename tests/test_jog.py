import pytest

from fakes import FakeTransport
from grbl_mouse import safety
from grbl_mouse.grbl_link.jog import (
    CYCLE_START_RESUME_BYTE,
    JOG_CANCEL_BYTE,
    build_jog_command,
    cancel_jog,
    resume_hold,
    send_jog,
    unlock_alarm,
)
from grbl_mouse.grbl_link.serial_link import GrblError, SerialLink


def test_build_jog_command_single_axis():
    assert build_jog_command(dx=10, feedrate=500) == "$J=G91 G21 X10 F500"


def test_build_jog_command_multiple_axes():
    assert build_jog_command(dx=1, dy=-2, dz=0.5, feedrate=200) == "$J=G91 G21 X1 Y-2 Z0.5 F200"


def test_build_jog_command_omits_zero_axes():
    assert build_jog_command(dy=5, feedrate=100) == "$J=G91 G21 Y5 F100"


def test_build_jog_command_requires_at_least_one_axis():
    with pytest.raises(ValueError):
        build_jog_command(feedrate=100)


def test_build_jog_command_requires_positive_feedrate():
    with pytest.raises(ValueError):
        build_jog_command(dx=1, feedrate=0)
    with pytest.raises(ValueError):
        build_jog_command(dx=1, feedrate=-5)


def test_send_jog_blocked_without_authorization_and_writes_nothing():
    transport = FakeTransport([])
    link = SerialLink(transport)
    assert safety.motion_enabled() is False
    with pytest.raises(safety.MotionNotAuthorizedError):
        send_jog(link, dx=1, feedrate=100)
    assert transport.written == []


def test_send_jog_writes_command_when_authorized():
    transport = FakeTransport([b"ok\r\n"])
    link = SerialLink(transport)
    safety.enable_motion()
    send_jog(link, dx=1, feedrate=100)
    assert transport.written == [b"$J=G91 G21 X1 F100\n"]


def test_send_jog_raises_grbl_error_on_error_response():
    transport = FakeTransport([b"error:9\r\n"])
    link = SerialLink(transport)
    safety.enable_motion()
    with pytest.raises(GrblError) as exc_info:
        send_jog(link, dx=1, feedrate=100)
    assert exc_info.value.code == 9


def test_cancel_jog_always_writes_regardless_of_authorization():
    transport = FakeTransport([])
    link = SerialLink(transport)
    assert safety.motion_enabled() is False
    cancel_jog(link)
    assert transport.written == [JOG_CANCEL_BYTE]

    transport2 = FakeTransport([])
    link2 = SerialLink(transport2)
    safety.enable_motion()
    cancel_jog(link2)
    assert transport2.written == [JOG_CANCEL_BYTE]


def test_unlock_alarm_blocked_without_authorization_and_writes_nothing():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(safety.MotionNotAuthorizedError):
        unlock_alarm(link)
    assert transport.written == []


def test_unlock_alarm_sends_dollar_x_when_authorized():
    transport = FakeTransport([b"ok\r\n"])
    link = SerialLink(transport)
    safety.enable_motion()
    unlock_alarm(link)
    assert transport.written == [b"$X\n"]


def test_resume_hold_blocked_without_authorization_and_writes_nothing():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(safety.MotionNotAuthorizedError):
        resume_hold(link)
    assert transport.written == []


def test_resume_hold_sends_tilde_when_authorized():
    transport = FakeTransport([])
    link = SerialLink(transport)
    safety.enable_motion()
    resume_hold(link)
    assert transport.written == [CYCLE_START_RESUME_BYTE]

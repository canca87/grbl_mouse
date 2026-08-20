import pytest

from grbl_mouse import safety


def test_motion_disabled_by_default():
    assert safety.motion_enabled() is False


def test_require_motion_enabled_raises_when_disabled():
    with pytest.raises(safety.MotionNotAuthorizedError):
        safety.require_motion_enabled()


def test_enable_motion_allows_require_to_pass():
    safety.enable_motion()
    safety.require_motion_enabled()  # does not raise


def test_disable_motion_revokes_authorization():
    safety.enable_motion()
    safety.disable_motion()
    assert safety.motion_enabled() is False
    with pytest.raises(safety.MotionNotAuthorizedError):
        safety.require_motion_enabled()

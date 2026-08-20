import pytest

from grbl_mouse import safety


@pytest.fixture(autouse=True)
def _reset_motion_state():
    """Motion defaults to disabled at the start and end of every test.

    Prevents one test's safety.enable_motion() call from leaking into
    another test — motion authorization must never be assumed.
    """
    safety.disable_motion()
    yield
    safety.disable_motion()

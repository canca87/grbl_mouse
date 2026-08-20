import pytest

from fakes import FakeTransport
from grbl_mouse.grbl_link.serial_link import (
    GrblAlarm,
    GrblError,
    GrblReset,
    SerialLink,
    Welcome,
    parse_response_line,
)


def test_parse_response_line_ok_returns_none():
    assert parse_response_line("ok") is None


def test_parse_response_line_error_raises_grbl_error():
    with pytest.raises(GrblError) as exc_info:
        parse_response_line("error:9")
    assert exc_info.value.code == 9


def test_parse_response_line_alarm_raises_grbl_alarm():
    with pytest.raises(GrblAlarm) as exc_info:
        parse_response_line("ALARM:1")
    assert exc_info.value.code == 1


def test_grbl_alarm_hard_limit_flags_position_loss():
    alarm = GrblAlarm(1, "ALARM:1")
    assert alarm.position_likely_lost is True
    assert "Hard limit" in alarm.description
    assert "re-homing" in str(alarm)


def test_grbl_alarm_soft_limit_does_not_flag_position_loss():
    alarm = GrblAlarm(2, "ALARM:2")
    assert alarm.position_likely_lost is False
    assert "re-homing" not in str(alarm)


def test_grbl_alarm_unknown_code_has_fallback_description():
    alarm = GrblAlarm(99, "ALARM:99")
    assert alarm.description == "Unknown alarm code."
    assert alarm.position_likely_lost is False


def test_parse_response_line_other_returns_line_unchanged():
    assert parse_response_line("[MSG:Caution: Unlocked]") == "[MSG:Caution: Unlocked]"


def test_parse_response_line_welcome_banner_raises_grbl_reset():
    with pytest.raises(GrblReset):
        parse_response_line("Grbl 1.1h ['$' for help]")


def test_read_welcome_parses_banner():
    transport = FakeTransport([b"Grbl 1.1h ['$' for help]\r\n"])
    link = SerialLink(transport)
    welcome = link.read_welcome(timeout=1.0)
    assert welcome == Welcome(version="1.1h", raw="Grbl 1.1h ['$' for help]")


def test_read_welcome_skips_blank_lines_before_banner():
    transport = FakeTransport([b"\r\n", b"Grbl 1.1h ['$' for help]\r\n"])
    link = SerialLink(transport)
    welcome = link.read_welcome(timeout=1.0)
    assert welcome.version == "1.1h"


def test_read_welcome_times_out_without_banner():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(TimeoutError):
        link.read_welcome(timeout=0.1)


def test_read_welcome_timeout_message_explains_ambiguous_causes():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(TimeoutError, match="e-stop"):
        link.read_welcome(timeout=0.05)


def test_query_status_line_sends_question_mark_and_returns_status_line():
    transport = FakeTransport([b"<Idle|MPos:0.000,0.000,0.000|FS:0,0>\r\n"])
    link = SerialLink(transport)
    line = link.query_status_line(timeout=1.0)
    assert line == "<Idle|MPos:0.000,0.000,0.000|FS:0,0>"
    assert transport.written == [b"?"]


def test_query_status_line_ignores_interleaved_ok_before_status():
    transport = FakeTransport([b"ok\r\n", b"<Idle|MPos:0.000,0.000,0.000>\r\n"])
    link = SerialLink(transport)
    line = link.query_status_line(timeout=1.0)
    assert line.startswith("<Idle")


def test_query_status_line_times_out_without_status():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(TimeoutError):
        link.query_status_line(timeout=0.1)


def test_query_status_line_timeout_message_mentions_power():
    transport = FakeTransport([])
    link = SerialLink(transport)
    with pytest.raises(TimeoutError, match="power"):
        link.query_status_line(timeout=0.05)


def test_query_status_line_surfaces_unprompted_alarm_instead_of_discarding_it():
    # GRBL can push ALARM: asynchronously (e.g. a hard-limit trip), not
    # necessarily as the direct reply to our '?' - this must not be
    # silently swallowed while scanning for a status line.
    transport = FakeTransport([b"ALARM:1\r\n"])
    link = SerialLink(transport)
    with pytest.raises(GrblAlarm) as exc_info:
        link.query_status_line(timeout=1.0)
    assert exc_info.value.code == 1


def test_query_status_line_surfaces_unprompted_reset_instead_of_discarding_it():
    transport = FakeTransport([b"Grbl 1.1h ['$' for help]\r\n"])
    link = SerialLink(transport)
    with pytest.raises(GrblReset):
        link.query_status_line(timeout=1.0)


def test_query_status_line_still_ignores_benign_interleaved_lines():
    transport = FakeTransport([b"[MSG:Caution: Unlocked]\r\n", b"<Idle|MPos:0.000,0.000,0.000>\r\n"])
    link = SerialLink(transport)
    line = link.query_status_line(timeout=1.0)
    assert line.startswith("<Idle")


def test_send_line_raises_grbl_reset_on_unexpected_welcome_banner():
    # If the board resets mid-session, the "response" to whatever we just
    # sent could actually be a fresh welcome banner - must not be treated
    # as an ordinary informational response.
    transport = FakeTransport([b"Grbl 1.1h ['$' for help]\r\n"])
    link = SerialLink(transport)
    with pytest.raises(GrblReset):
        link.send_line("$X")


def test_send_line_multiline_collects_lines_until_ok():
    transport = FakeTransport([b"$0=10\r\n", b"$1=25\r\n", b"ok\r\n"])
    link = SerialLink(transport)
    lines = link.send_line_multiline("$$")
    assert lines == ["$0=10", "$1=25"]
    assert transport.written == [b"$$\n"]


def test_send_line_multiline_raises_on_error():
    transport = FakeTransport([b"$0=10\r\n", b"error:3\r\n"])
    link = SerialLink(transport)
    with pytest.raises(GrblError):
        link.send_line_multiline("$$")


def test_send_line_multiline_times_out_without_ok():
    transport = FakeTransport([b"$0=10\r\n"])
    link = SerialLink(transport)
    with pytest.raises(TimeoutError):
        link.send_line_multiline("$$")


def test_close_closes_transport():
    transport = FakeTransport([])
    link = SerialLink(transport)
    link.close()
    assert transport.closed is True


def test_context_manager_closes_transport():
    transport = FakeTransport([])
    with SerialLink(transport):
        pass
    assert transport.closed is True

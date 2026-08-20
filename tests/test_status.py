import pytest

from fakes import FakeTransport
from grbl_mouse.grbl_link.serial_link import SerialLink
from grbl_mouse.grbl_link.status import parse_status_report, query_status


def test_parse_status_report_idle_with_mpos():
    report = parse_status_report("<Idle|MPos:1.000,-2.500,0.000|FS:0,0>")
    assert report.state == "Idle"
    assert report.machine_position == (1.0, -2.5, 0.0)


def test_parse_status_report_no_position_fields():
    report = parse_status_report("<Alarm>")
    assert report.state == "Alarm"
    assert report.machine_position is None
    assert report.work_position is None


def test_parse_status_report_rejects_non_status_line():
    with pytest.raises(ValueError):
        parse_status_report("ok")


def test_query_status_returns_parsed_report():
    transport = FakeTransport([b"<Run|MPos:0.500,0.000,0.000|FS:500,0>\r\n"])
    link = SerialLink(transport)
    report = query_status(link)
    assert report.state == "Run"
    assert report.machine_position == (0.5, 0.0, 0.0)

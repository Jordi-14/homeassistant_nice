"""Tests for T4_LOGS controller event-history parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from custom_components.nice_bidiwifi.errors import NiceProtocolError
from custom_components.nice_bidiwifi.protocol.nhk.administration import (
    build_t4_logs_body,
    parse_t4_logs_xml,
)

FIXTURE = Path(__file__).parent / "fixtures" / "t4_logs_reply.xml"


def test_fixture_reply_decodes_four_rows_newest_first_and_skips_malformed() -> None:
    rows = parse_t4_logs_xml(FIXTURE.read_text(encoding="utf-8"))

    assert [
        (r.id, r.time, r.protocol, r.dep, r.data_hex, r.change_state, r.cause)
        for r in rows
    ] == [
        (1028, "2026-11-12T19:53:58", "DEP", "04/02", "05 19 00 00", "stopped_in_closed", "oview"),
        (1027, "2026-11-12T19:53:56", "DEP", "04/40", "03 00 5a 00", None, None),
        (1013, "2026-11-12T19:49:14", "DEP", "04/02", "04 00 00 00", "stopped_in_opened", "none"),
        (655, "2026-11-12T14:02:23", "DEP", "04/02", "88 00 00 00", "stopped_for_command_arrival", "none"),
    ]


def test_rows_serialize_without_key_or_raw_text() -> None:
    row = parse_t4_logs_xml(FIXTURE.read_text(encoding="utf-8"))[0]

    assert row.as_dict() == {
        "id": 1028,
        "time": "2026-11-12T19:53:58",
        "dep": "04/02",
        "change_state": "stopped_in_closed",
        "cause": "oview",
        "data_hex": "05 19 00 00",
    }


def test_namespaced_rows_and_non_dep_rows() -> None:
    xml = FIXTURE.read_text(encoding="utf-8")
    namespaced = xml.replace("<Response ", '<Response xmlns="urn:x" ', 1)
    assert len(parse_t4_logs_xml(namespaced)) == 4
    assert parse_t4_logs_xml(xml.replace('protocol="DEP"', 'protocol="DMP"')) == ()


def test_invalid_xml_raises_protocol_error() -> None:
    with pytest.raises(NiceProtocolError):
        parse_t4_logs_xml("<Response")


def test_t4_logs_body_is_the_read_only_history_selector() -> None:
    assert build_t4_logs_body(1) == (
        '<Interface id="1">\r\n<SAddress>00</SAddress>\r\n'
        '<SEndpoint>03</SEndpoint>\r\n<Start max="100"/>\r\n</Interface>\r\n'
    )
    with pytest.raises(ValueError):
        build_t4_logs_body(1, 101)
    with pytest.raises(ValueError):
        build_t4_logs_body(0)

"""Tests for bounded shared Wi-Fi administration protocol helpers."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from custom_components.nice_bidiwifi.errors import NiceProtocolError
from custom_components.nice_bidiwifi.models.administration import NiceInterfaceClock
from custom_components.nice_bidiwifi.protocol.nhk.administration import (
    LOG_EVENTS_PER_SCOPE,
    MAX_ADMINISTRATION_XML_BYTES,
    MAX_GROUPS,
    MAX_INTERFACE_NAME_LENGTH,
    MAX_LOG_EVENTS,
    build_logs_body,
    build_reboot_body,
    build_update_clock_body,
    build_update_name_body,
    interface_clock_for,
    parse_groups_xml,
    parse_logs_xml,
    validate_interface_name,
)
from custom_components.nice_bidiwifi.protocol.nhk.info import parse_info_xml


def test_logs_parser_is_bounded_and_drops_identifying_fields() -> None:
    """Only reviewed event fields survive the fixed event bounds."""
    interface_events = "".join(
        (
            f'<Service type="interface-{index}" source="private-source" '
            f'time="2026-07-24T12:00:{index % 60:02d}Z" values="private">'
            "<Name>Private home name</Name>"
            "<Location>Private location</Location>"
            "<DoorStatus>open</DoorStatus>"
            "</Service>"
        )
        for index in range(40)
    )
    device_events = "".join(
        (
            f'<Property type="device-{index}" id="secret-id" idx="secret-index">'
            "<DoorAction>close</DoorAction>"
            "<Obstruct>false</Obstruct>"
            "</Property>"
        )
        for index in range(40)
    )

    snapshot = parse_logs_xml(
        "<Response>"
        f"<Interface><Events>{interface_events}</Events></Interface>"
        f"<Devices><Device id='private'><Events>{device_events}</Events></Device></Devices>"
        "</Response>"
    )

    assert snapshot.count == MAX_LOG_EVENTS
    assert sum(event.scope == "interface" for event in snapshot.events) == (
        LOG_EVENTS_PER_SCOPE
    )
    assert sum(event.scope == "device" for event in snapshot.events) == (
        LOG_EVENTS_PER_SCOPE
    )
    serialized = repr([event.as_dict() for event in snapshot.events])
    assert "Private home name" not in serialized
    assert "Private location" not in serialized
    assert "private-source" not in serialized
    assert "secret-id" not in serialized
    assert "private" not in serialized
    assert snapshot.events[0].door_status == "open"
    assert snapshot.events[-1].door_action == "close"


def test_group_parser_returns_count_only_bounded_summaries() -> None:
    """Group output never retains group, device, rule, or value identifiers."""
    groups = "".join(
        (
            f"<Group id='private-group-{index}'>"
            "<Interface><Allow id='private-rule' value='private-value'/></Interface>"
            "<Devices>"
            "<Device id='99'><Allow id='door' value='write'/></Device>"
            "<Device id='100'><Allow id='door' value='read'/></Device>"
            "</Devices>"
            "</Group>"
        )
        for index in range(MAX_GROUPS + 5)
    )

    snapshot = parse_groups_xml(f"<Response><Groups>{groups}</Groups></Response>")

    assert snapshot.count == MAX_GROUPS
    assert snapshot.groups[0].as_dict() == {
        "ordinal": 1,
        "interface_rule_count": 1,
        "device_count": 2,
        "device_rule_count": 2,
    }
    serialized = repr([group.as_dict() for group in snapshot.groups])
    assert "private" not in serialized
    assert "99" not in serialized


def test_administration_request_builders_match_nhk_shapes() -> None:
    """Request builders use the app-compatible signed-request bodies."""
    assert build_logs_body(2, 8) == (
        '<Interface events="8" />\r\n'
        '<Device id="2" events="8" />\r\n'
    )
    assert build_update_name_body("Gate & garage") == (
        "<Interface>\r\n"
        "<Settings>\r\n"
        "<Name>Gate &amp; garage</Name>\r\n"
        "</Settings>\r\n"
        "</Interface>\r\n"
    )
    assert build_reboot_body() == (
        "<Interface>\r\n"
        "<Commands>\r\n"
        "<Reboot>true</Reboot>\r\n"
        "</Commands>\r\n"
        "</Interface>\r\n"
    )


def test_info_parser_extracts_administration_round_trip_fields() -> None:
    info = parse_info_xml(
        """
        <Response>
          <Interface>
            <Date>2026-07-24T12:00:00Z</Date>
            <Zone>+01:00</Zone>
            <DST>+01:00</DST>
            <Settings><Name>Courtyard gate</Name></Settings>
            <Commands><Reboot type="bool" perm="w"/></Commands>
          </Interface>
        </Response>
        """
    )

    assert info.interface_name == "Courtyard gate"
    assert info.interface_date == "2026-07-24T12:00:00Z"
    assert info.interface_zone == "+01:00"
    assert info.interface_dst == "+01:00"
    assert info.interface_commands == ("Reboot",)


def test_interface_clock_separates_standard_offset_and_dst() -> None:
    """Europe/Madrid emits the standard zone separately from summer DST."""
    summer = interface_clock_for(
        datetime(2026, 7, 24, 12, 34, 56, tzinfo=UTC),
        "Europe/Madrid",
    )
    winter = interface_clock_for(
        datetime(2026, 1, 24, 12, 34, 56, tzinfo=UTC),
        "Europe/Madrid",
    )

    assert summer == NiceInterfaceClock(
        date="2026-07-24T12:34:56Z",
        zone="+01:00",
        dst="+01:00",
    )
    assert winter.zone == "+01:00"
    assert winter.dst == "+00:00"
    assert "<Zone>+01:00</Zone>" in build_update_clock_body(summer)
    assert "<DST>+01:00</DST>" in build_update_clock_body(summer)


@pytest.mark.parametrize(
    "name",
    [
        "",
        "   ",
        "line\nbreak",
        "x" * (MAX_INTERFACE_NAME_LENGTH + 1),
    ],
)
def test_interface_name_validation_rejects_unsafe_values(name: str) -> None:
    """Names are neither silently truncated nor allowed to carry controls."""
    with pytest.raises(ValueError):
        validate_interface_name(name)


@pytest.mark.parametrize("event_count", [0, LOG_EVENTS_PER_SCOPE + 1])
def test_logs_request_rejects_unbounded_counts(event_count: int) -> None:
    with pytest.raises(ValueError):
        build_logs_body(1, event_count)


def test_administration_parser_rejects_oversized_payload() -> None:
    """A device cannot force unbounded administration XML retention."""
    with pytest.raises(NiceProtocolError, match="response limit"):
        parse_logs_xml(
            "<Response>"
            + ("x" * MAX_ADMINISTRATION_XML_BYTES)
            + "</Response>"
        )

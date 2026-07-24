"""Shared Wi-Fi administration protocol helpers."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
import re
import xml.etree.ElementTree as ET
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ...errors import NiceProtocolError
from ...models.administration import (
    NiceGroupSnapshot,
    NiceGroupSummary,
    NiceInterfaceClock,
    NiceLogEvent,
    NiceLogSnapshot,
)
from .codec import xml_escape

LOG_EVENTS_PER_SCOPE = 32
MAX_LOG_EVENTS = LOG_EVENTS_PER_SCOPE * 2
MAX_GROUPS = 32
MAX_GROUP_DEVICES = 32
MAX_GROUP_RULES = 128
MAX_PROTOCOL_TEXT_LENGTH = 96
MAX_INTERFACE_NAME_LENGTH = 64
MAX_ADMINISTRATION_XML_BYTES = 256 * 1024
_OFFSET_PATTERN = re.compile(r"^[+-](?:0\d|1[0-4]):[0-5]\d$")


def _parse_xml(xml: str, response_type: str) -> ET.Element:
    if len(xml.encode("utf-8", errors="replace")) > MAX_ADMINISTRATION_XML_BYTES:
        raise NiceProtocolError(
            f"{response_type} XML exceeds the administration response limit"
        )
    try:
        return ET.fromstring(xml)
    except ET.ParseError as err:
        raise NiceProtocolError(f"Invalid {response_type} XML: {err}") from err


def _bounded_text(value: str | None) -> str | None:
    if value is None:
        return None
    cleaned = "".join(character for character in value.strip() if character.isprintable())
    return cleaned[:MAX_PROTOCOL_TEXT_LENGTH] or None


def _event_from_element(element: ET.Element, scope: str) -> NiceLogEvent:
    return NiceLogEvent(
        scope=scope,
        event_type=(
            _bounded_text(element.get("type"))
            or _bounded_text(element.tag)
            or "unknown"
        ),
        time=_bounded_text(element.get("time")),
        door_action=_bounded_text(
            element.findtext("DoorAction") or element.get("DoorActionCode")
        ),
        t4_action=_bounded_text(
            element.findtext("T4Action") or element.get("T4ActionCode")
        ),
        door_status=_bounded_text(element.findtext("DoorStatus")),
        obstruct=_bounded_text(element.findtext("Obstruct")),
    )


def parse_logs_xml(logs_xml: str) -> NiceLogSnapshot:
    """Parse a bounded, allowlisted LOGS response."""
    root = _parse_xml(logs_xml, "LOGS")
    events: list[NiceLogEvent] = []

    interface_events = root.findall("./Interface/Events/*")
    for element in interface_events[:LOG_EVENTS_PER_SCOPE]:
        events.append(_event_from_element(element, "interface"))

    for device in root.findall("./Devices/Device")[:MAX_GROUP_DEVICES]:
        for element in device.findall("./Events/*")[:LOG_EVENTS_PER_SCOPE]:
            if len(events) >= MAX_LOG_EVENTS:
                break
            events.append(_event_from_element(element, "device"))
        if len(events) >= MAX_LOG_EVENTS:
            break

    return NiceLogSnapshot(events=tuple(events))


def parse_groups_xml(groups_xml: str) -> NiceGroupSnapshot:
    """Parse only redacted counts from a bounded GROUPS response."""
    root = _parse_xml(groups_xml, "GROUPS")
    groups: list[NiceGroupSummary] = []
    for ordinal, group in enumerate(root.findall("./Groups/Group")[:MAX_GROUPS], 1):
        devices = group.findall("./Devices/Device")[:MAX_GROUP_DEVICES]
        groups.append(
            NiceGroupSummary(
                ordinal=ordinal,
                interface_rule_count=min(
                    len(group.findall("./Interface/Allow")),
                    MAX_GROUP_RULES,
                ),
                device_count=len(devices),
                device_rule_count=sum(
                    min(
                        len(device.findall("./Allow")),
                        MAX_GROUP_RULES,
                    )
                    for device in devices
                ),
            )
        )
    return NiceGroupSnapshot(groups=tuple(groups))


def build_logs_body(device_id: int, event_count: int = LOG_EVENTS_PER_SCOPE) -> str:
    """Build the bounded LOGS selector used by shared Wi-Fi interfaces."""
    if device_id < 1:
        raise ValueError("device_id must be at least 1")
    if not 1 <= event_count <= LOG_EVENTS_PER_SCOPE:
        raise ValueError(
            f"event_count must be between 1 and {LOG_EVENTS_PER_SCOPE}"
        )
    return (
        f'<Interface events="{event_count}" />\r\n'
        f'<Device id="{device_id}" events="{event_count}" />\r\n'
    )


def validate_interface_name(name: str) -> str:
    """Validate a user-provided shared Wi-Fi interface name."""
    normalized = name.strip()
    if not normalized:
        raise ValueError("interface name must not be empty")
    if len(normalized) > MAX_INTERFACE_NAME_LENGTH:
        raise ValueError(
            f"interface name must be at most {MAX_INTERFACE_NAME_LENGTH} characters"
        )
    if any(not character.isprintable() for character in normalized):
        raise ValueError("interface name must not contain control characters")
    return normalized


def build_update_name_body(name: str) -> str:
    """Build a CHANGE body for an interface name."""
    return (
        "<Interface>\r\n"
        "<Settings>\r\n"
        f"<Name>{xml_escape(validate_interface_name(name))}</Name>\r\n"
        "</Settings>\r\n"
        "</Interface>\r\n"
    )


def _format_offset(offset: timedelta) -> str:
    total_minutes = int(offset.total_seconds() // 60)
    sign = "+" if total_minutes >= 0 else "-"
    total_minutes = abs(total_minutes)
    hours, minutes = divmod(total_minutes, 60)
    value = f"{sign}{hours:02d}:{minutes:02d}"
    if not _OFFSET_PATTERN.fullmatch(value):
        raise ValueError(f"unsupported UTC offset: {value}")
    return value


def interface_clock_for(
    now: datetime,
    timezone_name: str,
) -> NiceInterfaceClock:
    """Build NHK date, standard-zone, and DST values for one instant."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    try:
        timezone = ZoneInfo(timezone_name)
    except ZoneInfoNotFoundError as err:
        raise ValueError(f"unknown timezone: {timezone_name}") from err
    local = now.astimezone(timezone)
    dst = local.dst() or timedelta(0)
    offset = local.utcoffset()
    if offset is None:
        raise ValueError(f"timezone has no UTC offset: {timezone_name}")
    return NiceInterfaceClock(
        date=now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        zone=_format_offset(offset - dst),
        dst=_format_offset(dst),
    )


def build_update_clock_body(clock: NiceInterfaceClock) -> str:
    """Build a CHANGE body for the interface clock."""
    if not _OFFSET_PATTERN.fullmatch(clock.zone):
        raise ValueError("zone must use signed HH:MM format")
    if not _OFFSET_PATTERN.fullmatch(clock.dst):
        raise ValueError("DST must use signed HH:MM format")
    try:
        datetime.strptime(clock.date, "%Y-%m-%dT%H:%M:%SZ")
    except ValueError as err:
        raise ValueError("date must use UTC YYYY-MM-DDTHH:MM:SSZ format") from err
    return (
        "<Interface>\r\n"
        "<Settings>\r\n"
        f"<Date>{clock.date}</Date>\r\n"
        f"<Zone>{clock.zone}</Zone>\r\n"
        f"<DST>{clock.dst}</DST>\r\n"
        "</Settings>\r\n"
        "</Interface>\r\n"
    )


def build_reboot_body() -> str:
    """Build the shared Wi-Fi reboot CHANGE body."""
    return (
        "<Interface>\r\n"
        "<Commands>\r\n"
        "<Reboot>true</Reboot>\r\n"
        "</Commands>\r\n"
        "</Interface>\r\n"
    )

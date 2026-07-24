"""Parsing for unsolicited NHK change and diagnostic events."""

from __future__ import annotations

from datetime import UTC, datetime
import xml.etree.ElementTree as ET

from ...errors import NiceProtocolError
from ...models.events import NiceEvent, NiceEventCategory, NiceEventKind
from ..t4.codec import decrypt_t4_payloads_from_frame
from ..t4.live import parse_cuwifi_live_status_payload
from .codec import xml_payload
from .status import bool_value, status_value

_MAX_TEXT_LENGTH = 96


def _bounded(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip()
    return normalized[:_MAX_TEXT_LENGTH] if normalized else None


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _descendant(node: ET.Element, name: str) -> ET.Element | None:
    return next(
        (child for child in node.iter() if _local_name(child.tag) == name),
        None,
    )


def _text(node: ET.Element, name: str) -> str | None:
    child = _descendant(node, name)
    return _bounded(child.text if child is not None else None)


def _integer(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(value, 10)
    except ValueError:
        return None


def _number(value: str | None) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except ValueError:
        return None


def _event_kind(root: ET.Element) -> NiceEventKind:
    event_type = (root.get("type") or _local_name(root.tag)).strip().casefold()
    if event_type == "change":
        return NiceEventKind.CHANGE
    if event_type == "diagnostic":
        return NiceEventKind.DIAGNOSTIC
    if "t4" in event_type:
        return NiceEventKind.LIVE_STATUS
    return NiceEventKind.UNKNOWN


def _category(
    *,
    kind: NiceEventKind,
    state: str | None,
    position: float | None,
    obstruction: bool | None,
    basic_diagnostic: str | None,
    cause: str | None,
    advanced_diagnostic: str | None,
    bluebus_error: str | None,
    battery_level: str | None,
    manoeuvre_count: int | None,
    manoeuvre_threshold: int | None,
    manoeuvre_current: float | None,
    reset_cause: str | None,
) -> NiceEventCategory:
    if bluebus_error is not None:
        return NiceEventCategory.BLUEBUS_ERROR
    if obstruction is True:
        return NiceEventCategory.OBSTRUCTION
    if reset_cause is not None:
        return NiceEventCategory.RESET
    if battery_level is not None:
        return NiceEventCategory.BATTERY
    if manoeuvre_count is not None or manoeuvre_threshold is not None:
        return NiceEventCategory.MAINTENANCE
    if manoeuvre_current is not None:
        return NiceEventCategory.MOTOR_CURRENT
    if (
        basic_diagnostic is not None
        or cause is not None
        or advanced_diagnostic is not None
        or kind is NiceEventKind.DIAGNOSTIC
    ):
        return NiceEventCategory.DIAGNOSTIC
    if state is not None or position is not None or obstruction is not None:
        return NiceEventCategory.STATE_CHANGE
    return NiceEventCategory.UNKNOWN


def _parse_device_event(
    root: ET.Element,
    device: ET.Element,
    *,
    kind: NiceEventKind,
    received_at: datetime,
    live_state: str | None,
    live_position: float | None,
) -> NiceEvent:
    raw_state = _text(device, "DoorStatus")
    obstruction = bool_value(_text(device, "Obstruct"))
    basic_diagnostic = _text(device, "BasicDiagnostic")
    cause = _text(device, "CauseCode")
    advanced_diagnostic = _text(device, "AdvDiagnostic")
    relative_timestamp = _text(device, "RelativeTimeStamp")
    bluebus_error = _text(device, "BlueBusErrStatus")
    battery = _descendant(device, "BatteryLevel")
    battery_level = _bounded(battery.text if battery is not None else None)
    battery_device_type = (
        _bounded(battery.get("devType")) if battery is not None else None
    )
    reset = _descendant(device, "CUResetCause")
    reset_cause = _bounded(reset.text if reset is not None else None)
    reset_device_class = _bounded(reset.get("devClass")) if reset is not None else None
    manoeuvre_count = _integer(_text(device, "ManoeuvreCount"))
    manoeuvre_threshold = _integer(_text(device, "ManoeuvreThLimit"))
    manoeuvre_current = _number(_text(device, "ManoeuvreAvgCurrent"))
    state = status_value(raw_state) or live_state
    event_kind = (
        NiceEventKind.LIVE_STATUS if live_state or live_position is not None else kind
    )

    return NiceEvent(
        kind=event_kind,
        category=_category(
            kind=event_kind,
            state=state,
            position=live_position,
            obstruction=obstruction,
            basic_diagnostic=basic_diagnostic,
            cause=cause,
            advanced_diagnostic=advanced_diagnostic,
            bluebus_error=bluebus_error,
            battery_level=battery_level,
            manoeuvre_count=manoeuvre_count,
            manoeuvre_threshold=manoeuvre_threshold,
            manoeuvre_current=manoeuvre_current,
            reset_cause=reset_cause,
        ),
        received_at=received_at,
        event_id=_bounded(root.get("id")),
        device_id=_bounded(device.get("id")),
        state=state,
        raw_state=raw_state,
        position=live_position,
        obstruction=obstruction,
        protocol_timestamp=_text(root, "Timestamp"),
        basic_diagnostic_code=basic_diagnostic,
        cause_code=cause,
        advanced_diagnostic_code=advanced_diagnostic,
        relative_timestamp=relative_timestamp,
        bluebus_error_status=bluebus_error,
        battery_device_type=battery_device_type,
        battery_level_code=battery_level,
        manoeuvre_count=manoeuvre_count,
        manoeuvre_threshold=manoeuvre_threshold,
        manoeuvre_average_current=manoeuvre_current,
        reset_device_class=reset_device_class,
        reset_cause=reset_cause,
    )


def parse_nhk_event_frame(
    frame: bytes,
    device_id: int = 1,
    *,
    received_at: datetime | None = None,
) -> tuple[NiceEvent, ...]:
    """Parse a framed unsolicited response into normalized events."""
    try:
        root = ET.fromstring(xml_payload(frame))
    except (ET.ParseError, UnicodeError, ValueError) as err:
        raise NiceProtocolError(f"Invalid NHK event XML: {err}") from err

    live_state: str | None = None
    live_position: float | None = None
    try:
        for payload in decrypt_t4_payloads_from_frame(frame):
            live = parse_cuwifi_live_status_payload(payload)
            if live is not None:
                live_state = live.state or live_state
                live_position = (
                    live.position if live.position is not None else live_position
                )
    except (ValueError, TypeError):
        pass

    kind = _event_kind(root)
    observed_at = received_at or datetime.now(UTC)
    all_devices = [
        node for node in root.iter() if _local_name(node.tag) == "Device"
    ]
    devices = [
        node
        for node in all_devices
        if node.get("id") in {None, str(device_id)}
    ]
    if all_devices and not devices:
        return ()
    if not all_devices:
        devices = [root]
    return tuple(
        _parse_device_event(
            root,
            device,
            kind=kind,
            received_at=observed_at,
            live_state=live_state,
            live_position=live_position,
        )
        for device in devices
    )

"""NHK STATUS and CHANGE event parsing."""

from __future__ import annotations

from dataclasses import replace
import xml.etree.ElementTree as ET

from ...errors import NiceProtocolError
from ...models.status import (
    STATE_CLOSING,
    STATE_OPENING,
    NiceStatus,
)
from ..t4.codec import decrypt_t4_payloads_from_frame
from ..t4.live import CuwifiLiveStatus, parse_cuwifi_live_status_payload
from .codec import xml_payload

NHK_DOOR_STATUS = {
    "closed": "closed",
    "close": "closed",
    "open": "open",
    "opened": "open",
    "opening": "opening",
    "closing": "closing",
    "stopped": "stopped",
    "stop": "stopped",
}
NHK_UNKNOWN_DOOR_STATUS = {"unknown", "unknow"}


def status_value(value: str | None) -> str | None:
    """Normalize an NHK DoorStatus value."""
    if not value:
        return None
    return NHK_DOOR_STATUS.get(value.strip().casefold())


def is_unknown_status(value: str | None) -> bool:
    """Return whether the device explicitly reported an unknown state."""
    return bool(value and value.strip().casefold() in NHK_UNKNOWN_DOOR_STATUS)


def bool_value(value: str | None) -> bool | None:
    """Normalize an NHK boolean value."""
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _status_device(root: ET.Element, device_id: int) -> ET.Element | None:
    if root.tag == "Device" and root.get("id") in {None, str(device_id)}:
        return root
    device = root.find(f".//Device[@id='{device_id}']")
    return device if device is not None else root.find(".//Device")


def _child_text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    value = node.findtext(path)
    return value.strip() if value and value.strip() else None


def parse_nhk_status_xml(status_xml: str, device_id: int = 1) -> NiceStatus | None:
    """Parse one STATUS or CHANGE XML document."""
    try:
        root = ET.fromstring(status_xml)
    except ET.ParseError as err:
        raise NiceProtocolError(f"Invalid NHK status XML: {err}") from err

    device = _status_device(root, device_id)
    raw_state = _child_text(device, "./Properties/DoorStatus")
    raw_obstruct = _child_text(device, "./Properties/Obstruct")
    if raw_state is None:
        raw_state = _child_text(root, ".//DoorStatus")
    if raw_obstruct is None:
        raw_obstruct = _child_text(root, ".//Obstruct")

    state = status_value(raw_state)
    if raw_state is not None and state is None:
        if not is_unknown_status(raw_state):
            raise NiceProtocolError(f"Unsupported NHK DoorStatus value: {raw_state}")
        obstacle = bool_value(raw_obstruct)
        registers = {"NHK/DoorStatus": raw_state}
        if raw_obstruct is not None:
            registers["NHK/Obstruct"] = raw_obstruct
        return NiceStatus(
            state=None,
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
            registers=registers,
            obstacle=obstacle,
        )
    if state is None:
        return None

    obstacle = bool_value(raw_obstruct)
    registers = {"NHK/DoorStatus": raw_state or state}
    if raw_obstruct is not None:
        registers["NHK/Obstruct"] = raw_obstruct
    return NiceStatus(
        state=state,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
        registers=registers,
        obstacle=obstacle,
    )


def merge_cuwifi_live_status(
    status: NiceStatus | None,
    live_status: CuwifiLiveStatus,
) -> NiceStatus:
    """Merge one CU_WIFI live T4 status into an NHK status snapshot."""
    if status is None:
        status = NiceStatus(
            state=None,
            position=None,
            current_position=None,
            closed_position=None,
            open_position=None,
            registers={},
        )
    registers = dict(status.registers)
    if live_status.state is not None:
        registers["NHK/T4Status"] = live_status.state
        registers["NHK/T4StatusPayload"] = live_status.payload_hex
    if live_status.position is not None:
        registers["NHK/T4InstantPosition"] = str(round(live_status.position))
        registers["NHK/T4InstantPositionPayload"] = live_status.payload_hex
    if live_status.raw_position is not None:
        registers["NHK/T4InstantPositionRaw"] = str(live_status.raw_position)
    if live_status.position_scale is not None:
        registers["NHK/T4InstantPositionScale"] = live_status.position_scale
    registers["NHK/T4PayloadKind"] = live_status.payload_kind

    position = live_status.position if live_status.position is not None else status.position
    if live_status.payload_kind == "04/40":
        if live_status.state in {STATE_OPENING, STATE_CLOSING}:
            state = live_status.state
        else:
            state = status.state
            if live_status.state is not None and live_status.state != status.state:
                registers["NHK/T4StatusIgnored"] = "04/40_position_only"
    else:
        state = live_status.state or status.state
    return replace(status, state=state, position=position, registers=registers)


def parse_nhk_status_frames(frames: list[bytes], device_id: int = 1) -> NiceStatus:
    """Parse the matched STATUS response and adjacent change frames."""
    status: NiceStatus | None = None
    for frame in frames:
        parsed = parse_nhk_status_xml(xml_payload(frame), device_id)
        if parsed is not None:
            status = parsed
        for plain in decrypt_t4_payloads_from_frame(frame):
            live_status = parse_cuwifi_live_status_payload(plain)
            if live_status is not None:
                status = merge_cuwifi_live_status(status, live_status)
    if status is None:
        raise NiceProtocolError(
            "NHK STATUS response did not include DoorStatus or CU_WIFI T4 status"
        )
    return status

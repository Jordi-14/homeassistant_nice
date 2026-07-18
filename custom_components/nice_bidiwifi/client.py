"""Local NHK/T4 client for Nice."""

from __future__ import annotations

import base64
from collections.abc import Callable
from dataclasses import dataclass, replace
import hashlib
import logging
import re
import secrets
import socket
import ssl
import string
import threading
import time
from typing import Any
import xml.etree.ElementTree as ET

STX = b"\x02"
ETX = b"\x03"

_LOGGER = logging.getLogger(__name__)

STATE_STOPPED = "stopped"
STATE_OPENING = "opening"
STATE_CLOSING = "closing"
STATE_OPEN = "open"
STATE_CLOSED = "closed"
STATE_PARTIALLY_OPEN = "partially_open"
CUWIFI_INTERMEDIATE_POSITION_TOLERANCE = 1.0
LIVE_RAW_POSITION_OPEN = 7000

STATUS_BY_BYTE = {
    0x01: STATE_STOPPED,
    0x02: STATE_OPENING,
    0x03: STATE_CLOSING,
    0x04: STATE_OPEN,
    0x05: STATE_CLOSED,
    0x10: STATE_PARTIALLY_OPEN,
    0x11: STATE_PARTIALLY_OPEN,
    0x12: STATE_PARTIALLY_OPEN,
    0x83: STATE_OPENING,
    0x84: STATE_CLOSING,
}

NHK_DOOR_STATUS = {
    "closed": STATE_CLOSED,
    "close": STATE_CLOSED,
    "open": STATE_OPEN,
    "opened": STATE_OPEN,
    "opening": STATE_OPENING,
    "closing": STATE_CLOSING,
    "stopped": STATE_STOPPED,
    "stop": STATE_STOPPED,
}
NHK_UNKNOWN_DOOR_STATUS = {"unknown", "unknow"}

STOP_REASON_BY_BYTE = {
    0x00: "normal",
    0x01: "obstacle_by_encoder",
    0x02: "obstacle_by_force",
    0x03: "photo_intervention",
    0x04: "halt",
    0x05: "emergency",
    0x06: "electrical_anomaly",
    0x07: "blocked",
    0x08: "timeout",
}

DMP_TARGET_CONTROLLER = (0x00, 0x03)
DMP_TARGET_OXI = (0x00, 0x0A)
NHK_STATUS_POST_RESPONSE_LISTEN_SECONDS = 0.75

CORE_STATUS_REGISTERS = (
    (0x04, 0x01),
    (0x04, 0x11),
    (0x04, 0x18),
    (0x04, 0x19),
)

EXTENDED_CONTROLLER_REGISTERS = (
    (0x04, 0x12),
    (0x04, 0x21),
    (0x04, 0x22),
    (0x04, 0x23),
    (0x04, 0x42),
    (0x04, 0x43),
    (0x04, 0x4A),
    (0x04, 0x4B),
    (0x04, 0x71),
    (0x04, 0x72),
    (0x04, 0x73),
    (0x04, 0x74),
    (0x04, 0x80),
    (0x04, 0x81),
    (0x04, 0x84),
    (0x04, 0x85),
    (0x04, 0x86),
    (0x04, 0x88),
    (0x04, 0x89),
    (0x04, 0x8A),
    (0x04, 0x8C),
    (0x04, 0x94),
    (0x04, 0x9C),
    (0x04, 0xB1),
    (0x04, 0xB2),
    (0x04, 0xB3),
    (0x04, 0xD0),
    (0x04, 0xD1),
    (0x04, 0xD2),
    (0x04, 0xD4),
)

OXI_INFO_REGISTERS = (
    (0x0A, 0x04),
    (0x0A, 0x09),
    (0x0A, 0x0A),
    (0x0A, 0x0B),
    (0x0A, 0x0C),
)

DEP_ACTION_PARTIAL_OPEN_1 = "partial_open_1"
DEP_ACTION_PARTIAL_OPEN_2 = "partial_open_2"
DEP_ACTION_PARTIAL_OPEN_3 = "partial_open_3"
DEP_ACTION_STEP_STEP = "step_step"
DEP_ACTION_COURTESY_LIGHT = "courtesy_light"
DEP_ACTION_COURTESY_LIGHT_TIMER = "courtesy_light_timer"
DEP_ACTION_LOCK = "lock"
DEP_ACTION_UNLOCK = "unlock"

DEP_ACTION_COMMANDS = {
    DEP_ACTION_STEP_STEP: 0x01,
    DEP_ACTION_PARTIAL_OPEN_1: 0x05,
    DEP_ACTION_PARTIAL_OPEN_2: 0x06,
    DEP_ACTION_PARTIAL_OPEN_3: 0x07,
    DEP_ACTION_LOCK: 0x0F,
    DEP_ACTION_UNLOCK: 0x10,
    DEP_ACTION_COURTESY_LIGHT_TIMER: 0x11,
    DEP_ACTION_COURTESY_LIGHT: 0x12,
}


class NiceBidiError(Exception):
    """Base error for Nice local communication."""


class NiceBidiAuthError(NiceBidiError):
    """Authentication failed."""


class NiceBidiConnectionError(NiceBidiError):
    """Connection failed or device did not respond."""


def nice_bidi_error_code(err: Exception | str) -> str | None:
    """Return a Nice XML error code from an exception or response string."""
    match = re.search(r"<Code>\s*([^<\s]+)\s*</Code>", str(err))
    return match.group(1) if match else None


@dataclass(frozen=True)
class NiceBidiCredentials:
    """Credentials needed for NHK local authentication."""

    username: str
    password_hex: str
    target_mac: str
    source_id: str | None = None

    @property
    def source(self) -> str:
        """Return the NHK source identifier."""
        return self.source_id or self.username

    @property
    def password(self) -> bytes:
        """Return the pairing password as raw bytes."""
        if not re.fullmatch(r"[0-9a-fA-F]{64}", self.password_hex):
            raise NiceBidiAuthError("Password must be a 64-character hexadecimal value")
        return bytes.fromhex(self.password_hex)


@dataclass(frozen=True)
class NiceBidiStatus:
    """Current gate state read from DMP registers."""

    state: str | None
    position: float | None
    current_position: int | None
    closed_position: int | None
    open_position: int | None
    registers: dict[str, str]
    max_open_position: int | None = None
    partial_open_1_position: int | None = None
    partial_open_2_position: int | None = None
    partial_open_3_position: int | None = None
    opening_speed: int | None = None
    closing_speed: int | None = None
    opening_force: int | None = None
    closing_force: int | None = None
    pause_time: int | None = None
    photo_close_time: int | None = None
    photo_close_mode: int | None = None
    always_close_time: int | None = None
    always_close_mode: int | None = None
    maintenance_threshold: int | None = None
    maintenance_count: int | None = None
    total_maneuver_count: int | None = None
    alternate_movement_count: int | None = None
    input_1: bool | None = None
    input_2: bool | None = None
    input_3: bool | None = None
    input_4: bool | None = None
    auto_close: bool | None = None
    photo_close: bool | None = None
    always_close: bool | None = None
    standby: bool | None = None
    pre_flash: bool | None = None
    key_lock: bool | None = None
    limit_closed: bool | None = None
    limit_open: bool | None = None
    photocell: bool | None = None
    obstacle: bool | None = None
    diagnostics_io_byte: int | None = None
    last_stop_reason: str | None = None
    last_stop_reason_code: int | None = None
    diagnostics_parameters: str | None = None
    oxi_detected: bool | None = None
    oxi_product: str | None = None
    oxi_hardware_version: str | None = None
    oxi_firmware_version: str | None = None
    oxi_description: str | None = None

    @property
    def is_moving(self) -> bool:
        """Return true when the gate is moving."""
        return self.state in {STATE_OPENING, STATE_CLOSING}


@dataclass(frozen=True)
class NiceBidiDeviceInfo:
    """Device metadata returned by the BiDi-WiFi INFO request."""

    interface_hw_version: str | None
    interface_fw_version: str | None
    interface_manufacturer: str | None
    interface_product: str | None
    interface_serial: str | None
    device_type: str | None
    device_manufacturer: str | None
    device_product: str | None
    device_description: str | None
    device_hw_version: str | None
    device_fw_version: str | None
    device_serial: str | None
    device_product_detail: str | None
    services: tuple[NiceBidiServiceCapability, ...] = ()
    properties: tuple[NiceBidiServiceCapability, ...] = ()


@dataclass(frozen=True)
class NiceBidiServiceCapability:
    """A service or property advertised by the BiDi-WiFi INFO request."""

    owner: str
    owner_id: str | None
    name: str
    path: str
    value_type: str | None
    permission: str | None
    values_raw: str | None
    values: tuple[str, ...]


@dataclass(frozen=True)
class _CuwifiLiveStatus:
    """State or position decoded from a CU_WIFI live T4 event."""

    state: str | None
    position: float | None
    payload_hex: str
    payload_kind: str
    raw_position: int | None = None
    position_scale: str | None = None


@dataclass(frozen=True)
class _LiveT4Message:
    """Inner controller-originated live T4 message and its target address."""

    message: bytes
    target: tuple[int, int]


def _sha256(*values: bytes) -> bytes:
    digest = hashlib.sha256()
    for value in values:
        digest.update(value)
    return digest.digest()


def _reverse_hex(value: str) -> bytes:
    return bytes.fromhex(value)[::-1]


def _printable(data: bytes) -> str:
    return data.decode("utf-8", errors="replace").replace("\x02", "<STX>").replace("\x03", "<ETX>")


def _attr(text: str, name: str) -> str | None:
    match = re.search(rf'\b{name}="([^"]*)"', text)
    return match.group(1) if match else None


def _xml_escape(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace('"', "&quot;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


def _xor_sha256(data: bytes, key: bytes) -> bytes:
    digest = hashlib.sha256(key).digest()
    return bytes(byte ^ digest[index % len(digest)] for index, byte in enumerate(data))


def _random_t4_key(length: int = 31) -> bytes:
    alphabet = string.ascii_letters + string.digits
    return "".join(secrets.choice(alphabet) for _ in range(length)).encode("ascii")


def _dmp_checksum(*values: int) -> int:
    checksum = 0
    for value in values:
        checksum ^= value
    return checksum & 0xFF


def build_dmp_read_frame(
    daddr: int,
    dendpoint: int,
    group: int,
    parameter: int,
    index: int | None = None,
) -> bytes:
    """Build a DMP register read frame."""
    args = [group, parameter, 0x99, 0x00]
    if index is None:
        args.append(0x00)
    else:
        args.extend([0x01, index])
    sublen = len(args) + 1
    marker = 0xC9 ^ sublen ^ daddr ^ dendpoint
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x08,
        sublen,
        marker,
        *args,
        _dmp_checksum(*args),
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])


def build_dmp_write_frame(
    daddr: int,
    dendpoint: int,
    group: int,
    parameter: int,
    value: bytes,
) -> bytes:
    """Build a DMP register write frame."""
    if not value:
        raise ValueError("value must contain at least one byte")
    if len(value) > 0xFF:
        raise ValueError("value must fit in a DMP byte-length payload")
    args = [group, parameter, 0xA9, 0x00, len(value), *value]
    sublen = len(args) + 1
    marker = 0xC9 ^ sublen ^ daddr ^ dendpoint
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x08,
        sublen,
        marker,
        *args,
        _dmp_checksum(*args),
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])


def build_dep_action_frame(
    command: int,
    *,
    daddr: int = 0x00,
    dendpoint: int = 0x03,
) -> bytes:
    """Build a DEP action frame."""
    if not 0 <= command <= 0xFF:
        raise ValueError("command must be a byte")
    args = [0x01, 0x82, command, 0x64]
    checksum = _dmp_checksum(*args)
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x01,
        0x05,
        0xC6,
        *args,
        checksum,
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])


def parse_dmp_response(plain: bytes) -> dict[str, Any]:
    """Parse a decrypted DMP response."""
    result: dict[str, Any] = {"plain_hex": plain.hex(" ")}
    if len(plain) < 15 or plain[0] != 0x55:
        return result
    result.update(
        {
            "group": f"{plain[9]:02X}",
            "parameter": f"{plain[10]:02X}",
            "operation": f"{plain[11]:02X}",
        }
    )
    if plain[11] == 0x19 and len(plain) >= 15:
        value_len = plain[12]
        value_type = plain[13]
        value = plain[14 : 14 + value_len]
        if len(value) == value_len:
            result.update(
                {
                    "value_type": f"{value_type:02X}",
                    "value_hex": value.hex(" "),
                    "value_uint_be": int.from_bytes(value, "big") if value else 0,
                }
            )
    return result


def _dmp_uint(register: dict[str, Any] | None) -> int | None:
    if not register:
        return None
    value_hex = register.get("value_hex")
    if not value_hex:
        return None
    try:
        value = bytes.fromhex(value_hex)
    except ValueError:
        return None
    if not value or all(byte == 0xFF for byte in value):
        return None
    return int.from_bytes(value, "big")


def _dmp_bytes(register: dict[str, Any] | None) -> bytes | None:
    if not register:
        return None
    value_hex = register.get("value_hex")
    if not value_hex:
        return None
    try:
        return bytes.fromhex(value_hex)
    except ValueError:
        return None


def _dmp_bool(register: dict[str, Any] | None) -> bool | None:
    value = _dmp_bytes(register)
    if not value or all(byte == 0xFF for byte in value):
        return None
    return value[0] != 0


def _dmp_ascii(register: dict[str, Any] | None) -> str | None:
    value = _dmp_bytes(register)
    if not value:
        return None
    text = bytes(byte for byte in value if byte).decode("ascii", errors="ignore").strip()
    return text or None


def _status_from_register(register: dict[str, Any] | None) -> str | None:
    if not register:
        return None
    value_hex = register.get("value_hex")
    if not value_hex:
        return None
    try:
        first_byte = int(value_hex.split()[0], 16)
    except (IndexError, ValueError):
        return None
    return STATUS_BY_BYTE.get(first_byte)


def _endpoint_position_from_state(state: str | None) -> float | None:
    """Return a known endpoint position from a terminal state."""
    if state == STATE_CLOSED:
        return 0.0
    if state == STATE_OPEN:
        return 100.0
    return None


def _make_context() -> ssl.SSLContext:
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    # BiDi-WiFi exposes a local TLS endpoint with a device certificate that
    # cannot be validated against Home Assistant's trust store.
    context.check_hostname = False
    context.verify_mode = ssl.CERT_NONE
    context.minimum_version = ssl.TLSVersion.TLSv1_2
    context.maximum_version = ssl.TLSVersion.TLSv1_2
    return context


def _frame(xml: str) -> bytes:
    return STX + xml.encode("utf-8") + ETX


def _xml_payload(frame: bytes) -> str:
    payload = frame
    if payload.startswith(STX):
        payload = payload[1:]
    if payload.endswith(ETX):
        payload = payload[:-1]
    return payload.decode("utf-8", errors="replace")


def _element_label(element: ET.Element) -> str:
    element_id = element.get("id")
    return element.tag if element_id is None else f'{element.tag}[@id="{element_id}"]'


def _split_values(value: str | None) -> tuple[str, ...]:
    if not value:
        return ()
    return tuple(part.strip() for part in value.split(",") if part.strip())


def _parse_info_capabilities(root: ET.Element, container_tag: str) -> tuple[NiceBidiServiceCapability, ...]:
    capabilities: list[NiceBidiServiceCapability] = []

    def walk(node: ET.Element, path: str) -> None:
        for child in list(node):
            if child.tag == container_tag:
                for capability in list(child):
                    values_raw = capability.get("values")
                    capabilities.append(
                        NiceBidiServiceCapability(
                            owner=node.tag,
                            owner_id=node.get("id"),
                            name=capability.tag,
                            path=f"{path}/{container_tag}/{capability.tag}",
                            value_type=capability.get("type"),
                            permission=capability.get("perm"),
                            values_raw=values_raw,
                            values=_split_values(values_raw),
                        )
                    )
            walk(child, f"{path}/{_element_label(child)}")

    walk(root, _element_label(root))

    return tuple(capabilities)


def _parse_info_services(root: ET.Element) -> tuple[NiceBidiServiceCapability, ...]:
    return _parse_info_capabilities(root, "Services")


def _parse_info_properties(root: ET.Element) -> tuple[NiceBidiServiceCapability, ...]:
    return _parse_info_capabilities(root, "Properties")


def parse_info_xml(info_xml: str, device_id: int = 1) -> NiceBidiDeviceInfo:
    """Parse INFO XML into static metadata and advertised capabilities."""
    try:
        root = ET.fromstring(info_xml)
    except ET.ParseError as err:
        raise NiceBidiConnectionError(f"Invalid INFO XML: {err}") from err

    interface = root.find("Interface")
    device = root.find(f"./Devices/Device[@id='{device_id}']")
    if device is None:
        device = root.find("./Devices/Device")

    def find_text(node: ET.Element | None, name: str) -> str | None:
        if node is None:
            return None
        value = node.findtext(name)
        return value.strip() if value and value.strip() else None

    return NiceBidiDeviceInfo(
        interface_hw_version=find_text(interface, "VersionHW"),
        interface_fw_version=find_text(interface, "VersionFW"),
        interface_manufacturer=find_text(interface, "Manuf"),
        interface_product=find_text(interface, "Prod"),
        interface_serial=find_text(interface, "SerialNr"),
        device_type=find_text(device, "Type"),
        device_manufacturer=find_text(device, "Manuf"),
        device_product=find_text(device, "Prod"),
        device_description=find_text(device, "Desc"),
        device_hw_version=find_text(device, "VersionHW"),
        device_fw_version=find_text(device, "VersionFW"),
        device_serial=find_text(device, "SerialNr"),
        device_product_detail=find_text(device, "ProdDTL"),
        services=_parse_info_services(root),
        properties=_parse_info_properties(root),
    )


def device_info_supports_nhk_status(info: NiceBidiDeviceInfo, device_id: int = 1) -> bool:
    """Return true when INFO advertises readable NHK DoorStatus."""
    target_device_id = str(device_id)
    for prop in info.properties:
        if prop.name != "DoorStatus":
            continue
        if prop.owner != "Device" or prop.owner_id not in {None, target_device_id}:
            continue
        if "r" in (prop.permission or ""):
            return True
    return False


def _nhk_status_value(value: str | None) -> str | None:
    if not value:
        return None
    return NHK_DOOR_STATUS.get(value.strip().casefold())


def _is_nhk_unknown_status(value: str | None) -> bool:
    if not value:
        return False
    return value.strip().casefold() in NHK_UNKNOWN_DOOR_STATUS


def _nhk_bool_value(value: str | None) -> bool | None:
    if value is None:
        return None
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    return None


def _find_nhk_status_device(root: ET.Element, device_id: int) -> ET.Element | None:
    if root.tag == "Device" and root.get("id") in {None, str(device_id)}:
        return root
    device = root.find(f".//Device[@id='{device_id}']")
    if device is not None:
        return device
    return root.find(".//Device")


def _find_child_text(node: ET.Element | None, path: str) -> str | None:
    if node is None:
        return None
    value = node.findtext(path)
    return value.strip() if value and value.strip() else None


def _parse_nhk_status_xml(status_xml: str, device_id: int = 1) -> NiceBidiStatus | None:
    try:
        root = ET.fromstring(status_xml)
    except ET.ParseError as err:
        raise NiceBidiConnectionError(f"Invalid NHK status XML: {err}") from err

    device = _find_nhk_status_device(root, device_id)
    raw_state = _find_child_text(device, "./Properties/DoorStatus")
    raw_obstruct = _find_child_text(device, "./Properties/Obstruct")
    if raw_state is None:
        raw_state = _find_child_text(root, ".//DoorStatus")
    if raw_obstruct is None:
        raw_obstruct = _find_child_text(root, ".//Obstruct")

    state = _nhk_status_value(raw_state)
    if raw_state is not None and state is None:
        if not _is_nhk_unknown_status(raw_state):
            raise NiceBidiConnectionError(f"Unsupported NHK DoorStatus value: {raw_state}")
        obstacle = _nhk_bool_value(raw_obstruct)
        registers = {"NHK/DoorStatus": raw_state}
        if raw_obstruct is not None:
            registers["NHK/Obstruct"] = raw_obstruct
        return NiceBidiStatus(
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

    obstacle = _nhk_bool_value(raw_obstruct)
    registers = {"NHK/DoorStatus": raw_state or state}
    if raw_obstruct is not None:
        registers["NHK/Obstruct"] = raw_obstruct

    return NiceBidiStatus(
        state=state,
        position=_endpoint_position_from_state(state),
        current_position=None,
        closed_position=None,
        open_position=None,
        registers=registers,
        obstacle=obstacle,
    )


def _merge_cuwifi_live_status(
    status: NiceBidiStatus | None,
    live_status: _CuwifiLiveStatus,
) -> NiceBidiStatus:
    """Merge one CU_WIFI live T4 status into an NHK status snapshot."""
    if status is None:
        status = NiceBidiStatus(
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

    position = live_status.position
    if position is None:
        position = _endpoint_position_from_state(live_status.state)
    if position is None:
        position = status.position

    state = live_status.state or status.state
    if (
        live_status.payload_kind == "04/40"
        and live_status.state == STATE_STOPPED
        and live_status.position is not None
        and CUWIFI_INTERMEDIATE_POSITION_TOLERANCE
        < live_status.position
        < 100.0 - CUWIFI_INTERMEDIATE_POSITION_TOLERANCE
        and status.state in {STATE_OPENING, STATE_CLOSING}
    ):
        registers["NHK/T4StatusIgnored"] = "stopped_with_intermediate_position"
        state = status.state

    return replace(
        status,
        state=state,
        position=position,
        registers=registers,
    )


def _parse_nhk_status_frames(frames: list[bytes], device_id: int = 1) -> NiceBidiStatus:
    status: NiceBidiStatus | None = None
    for frame in frames:
        parsed = _parse_nhk_status_xml(_xml_payload(frame), device_id)
        if parsed is not None:
            status = parsed
        for plain in _decrypt_t4_payloads_from_frame(frame):
            live_status = _parse_cuwifi_live_status_payload(plain)
            if live_status is not None:
                status = _merge_cuwifi_live_status(status, live_status)
    if status is None:
        raise NiceBidiConnectionError("NHK STATUS response did not include DoorStatus or CU_WIFI T4 status")
    return status


def _cuwifi_t4_message(plain: bytes) -> _LiveT4Message | None:
    """Return the inner controller-originated CU_WIFI T4 message."""
    if len(plain) < 12:
        return None

    if plain[0] == 0x55:
        if len(plain) < 4:
            return None
        body_size = plain[1]
        if body_size != plain[-1] or body_size != len(plain) - 3:
            return None
        body = plain[2:-1]
    else:
        body = plain

    if len(body) < 10:
        return None

    to_row, to_address, from_row, from_address, message_type, message_size = body[:6]
    message = body[7:-1]
    if message_size != len(message) + 1:
        return None
    target = (to_row, to_address)
    if target not in {(0x00, 0xFF), (0xFF, 0x01)}:
        return None
    if (from_row, from_address) != DMP_TARGET_CONTROLLER:
        return None
    if message_type != 0x01:
        return None

    return _LiveT4Message(message=message, target=target)


def _live_04_40_position(position_value: int, target: tuple[int, int]) -> tuple[float | None, str | None]:
    """Return percent position and scale name from a live 04/40 raw value."""
    if target == (0xFF, 0x01):
        if 0 <= position_value <= LIVE_RAW_POSITION_OPEN:
            return round((position_value / LIVE_RAW_POSITION_OPEN) * 100.0, 1), "raw_0_7000"
        return None, "raw_0_7000"
    if 0 <= position_value <= 100:
        return float(position_value), "percent"
    return None, None


def _parse_cuwifi_live_status_payload(plain: bytes) -> _CuwifiLiveStatus | None:
    """Parse CU_WIFI live T4 status and coarse instant-position payloads."""
    live_message = _cuwifi_t4_message(plain)
    if live_message is None:
        return None
    message = live_message.message
    if not message or len(message) < 3 or message[0] != 0x04:
        return None

    payload_hex = message.hex(" ")
    if message[1] == 0x40 and len(message) >= 5:
        state = STATUS_BY_BYTE.get(message[2])
        position_value = int.from_bytes(message[3:5], "big")
        position, position_scale = _live_04_40_position(position_value, live_message.target)
        if state is None and position is None:
            return None
        return _CuwifiLiveStatus(
            state=state,
            position=position,
            payload_hex=payload_hex,
            payload_kind="04/40",
            raw_position=position_value if position is not None else None,
            position_scale=position_scale if position is not None else None,
        )

    if message[1] == 0x02:
        state = STATUS_BY_BYTE.get(message[2])
        if state is None:
            return None
        return _CuwifiLiveStatus(
            state=state,
            position=_endpoint_position_from_state(state),
            payload_hex=payload_hex,
            payload_kind="04/02",
        )

    return None


T4_RE = re.compile(r"<T4\b(?P<attrs>[^>]*)>(?P<body>.*?)</T4>", re.DOTALL)


def _decrypt_t4_payloads_from_frame(frame: bytes) -> list[bytes]:
    """Return decrypted T4 payloads from one NHK XML frame."""
    text = _printable(frame)
    payloads: list[bytes] = []
    for match in T4_RE.finditer(text):
        key_value = _attr(match.group("attrs"), "key")
        if not key_value:
            continue
        try:
            key = base64.b64decode(key_value)
            encrypted = base64.b64decode(match.group("body").strip())
        except ValueError:
            continue
        payloads.append(_xor_sha256(encrypted, key))
    return payloads


def _response_summary(response: bytes) -> str:
    """Return a credential-safe summary of an NHK response."""
    text = _printable(response)
    response_type = _attr(text, "type") or "unknown"
    response_id = _attr(text, "id") or "unknown"
    error = " error=yes" if re.search(r"<Error\b", text) else ""
    return (
        f"type={response_type} id={response_id} bytes={len(response)} "
        f"t4_payloads={len(T4_RE.findall(text))}{error}"
    )


class NiceBidiClient:
    """Persistent local NHK client for a Nice interface."""

    def __init__(
        self,
        host: str,
        port: int,
        credentials: NiceBidiCredentials,
        *,
        device_id: int = 1,
        timeout: float = 10.0,
        t4_timeout_ms: int = 200,
    ) -> None:
        self.host = host
        self.port = port
        self.credentials = credentials
        self.device_id = device_id
        self.timeout = timeout
        self.t4_timeout_ms = t4_timeout_ms
        self._socket: ssl.SSLSocket | None = None
        self._session_id = 0
        self._session_key: bytes | None = None
        self._sequence = 1
        self._lock = threading.RLock()
        self._reconnect_count = 0

    @property
    def reconnect_count(self) -> int:
        """Return the number of internal reconnect attempts."""
        with self._lock:
            return self._reconnect_count

    def close(self) -> None:
        """Close the current session."""
        with self._lock:
            self._close_locked()

    def read_status(self, *, include_extended: bool = False) -> NiceBidiStatus:
        """Read status and position DMP registers."""
        return self._run_with_reconnect(lambda: self._read_status_locked(include_extended=include_extended))

    def read_nhk_status(self) -> NiceBidiStatus:
        """Read state from NHK STATUS/CHANGE properties."""
        return self._run_with_reconnect(self._read_nhk_status_locked)

    def read_info(self) -> NiceBidiDeviceInfo:
        """Read static BiDi-WiFi and control-unit metadata."""
        return self._run_with_reconnect(self._read_info_locked)

    def read_info_xml(self) -> str:
        """Read raw INFO XML from the BiDi-WiFi."""
        return self._run_with_reconnect(self._read_info_xml_locked)

    def send_action(self, action: str) -> None:
        """Send a high-level DoorAction command."""
        if action not in {"open", "stop", "close"}:
            raise ValueError("action must be open, stop, or close")
        self._run_with_reconnect(lambda: self._send_action_locked(action))

    def send_dep_action(self, action: str) -> None:
        """Send a low-level DEP action command."""
        if action not in DEP_ACTION_COMMANDS:
            valid = ", ".join(sorted(DEP_ACTION_COMMANDS))
            raise ValueError(f"action must be one of: {valid}")
        self._run_with_reconnect(lambda: self._send_dep_action_locked(action))

    def write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
    ) -> None:
        """Write a BusT4/DMP register."""
        if size < 1:
            raise ValueError("size must be at least 1")
        if value < 0:
            raise ValueError("value must be non-negative")
        if value > (1 << (size * 8)) - 1:
            raise ValueError(f"value must fit in {size} byte(s)")
        payload = value.to_bytes(size, "big")
        self._run_with_reconnect(lambda: self._write_dmp_register_locked(group, parameter, payload))

    def test_connection(self) -> NiceBidiStatus:
        """Authenticate and read status once."""
        try:
            return self.read_status()
        except NiceBidiConnectionError as err:
            if nice_bidi_error_code(err) != "14":
                raise
            info = self.read_info()
            if device_info_supports_nhk_status(info, self.device_id):
                try:
                    return self.read_nhk_status()
                except NiceBidiError:
                    _LOGGER.debug("Nice NHK status validation failed after DMP Code 14", exc_info=True)
            return NiceBidiStatus(
                state=None,
                position=None,
                current_position=None,
                closed_position=None,
                open_position=None,
                registers={},
            )

    def _run_with_reconnect(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_connected_locked()
                    return operation()
                except (OSError, ssl.SSLError, NiceBidiConnectionError) as exc:
                    last_error = exc
                    self._close_locked()
                    if attempt == 0:
                        _LOGGER.debug(
                            "Nice local operation failed; reconnecting once: %s",
                            exc.__class__.__name__,
                        )
                        self._reconnect_count += 1
                        time.sleep(1.0)
            assert last_error is not None
            raise NiceBidiConnectionError(str(last_error)) from last_error

    def _open_locked(self) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            raw: socket.socket | None = None
            tls_sock: ssl.SSLSocket | None = None
            try:
                _LOGGER.debug(
                    "Opening Nice local TLS connection to %s:%s (attempt %s/3)",
                    self.host,
                    self.port,
                    attempt + 1,
                )
                raw = socket.create_connection((self.host, self.port), timeout=self.timeout)
                raw.settimeout(self.timeout)
                tls_sock = _make_context().wrap_socket(
                    raw,
                    server_hostname=None,
                    do_handshake_on_connect=False,
                )
                tls_sock.do_handshake()
                self._socket = tls_sock
                _LOGGER.debug("Nice local TLS connection established to %s:%s", self.host, self.port)
                return
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                if tls_sock:
                    tls_sock.close()
                elif raw:
                    raw.close()
                if attempt < 2:
                    _LOGGER.debug(
                        "Nice local TLS connection attempt %s failed: %s: %s",
                        attempt + 1,
                        exc.__class__.__name__,
                        exc,
                    )
                    time.sleep(0.75 * (attempt + 1))
        assert last_error is not None
        raise NiceBidiConnectionError(str(last_error)) from last_error

    def _connect_locked(self) -> None:
        client_challenge = f"{secrets.randbits(32):08X}"
        xml = (
            self._start_request(0, "CONNECT")
            + f'<Authentication cc="{client_challenge}" username="{_xml_escape(self.credentials.username)}"/>\r\n'
            + "</Request>\r\n"
        )
        response = self._send_locked(xml, expected_type="CONNECT", expected_id=0)
        text = _printable(response)
        server_challenge = _attr(text, "sc")
        auth_match = re.search(r"<Authentication\b([^>]*)>", text)
        session_id = _attr(auth_match.group(1), "id") if auth_match else None
        if "<Error>" in text:
            raise NiceBidiAuthError(text)
        if not server_challenge or session_id is None:
            raise NiceBidiConnectionError("CONNECT response was empty or missing session data")

        self._session_id = int(session_id)
        self._sequence = 1
        self._session_key = _sha256(
            self.credentials.password,
            _reverse_hex(server_challenge),
            _reverse_hex(client_challenge),
        )
        _LOGGER.debug("Nice local NHK authentication succeeded with session id %s", self._session_id)

    def _ensure_connected_locked(self) -> None:
        if self._socket and self._session_key is not None:
            return
        self._close_locked()
        self._open_locked()
        self._connect_locked()

    def _close_locked(self) -> None:
        if self._socket:
            try:
                self._socket.close()
            finally:
                self._socket = None
        self._session_key = None
        self._session_id = 0
        self._sequence = 1

    def _start_request(self, request_id: int, request_type: str) -> str:
        return (
            f'<Request gw="gwID" id="{request_id}" protocolType="NHK" '
            f'protocolVersion="1.0" source="{_xml_escape(self.credentials.source)}" '
            f'target="{_xml_escape(self.credentials.target_mac)}" type="{request_type}">\r\n'
        )

    def _next_request_id(self) -> int:
        request_id = (self._sequence << 8) | (self._session_id & 0xFF)
        self._sequence += 1
        return request_id

    def _sign(self, xml_command: str) -> str:
        if self._session_key is None:
            raise NiceBidiConnectionError("not authenticated")
        return base64.b64encode(_sha256(_sha256(xml_command.encode("utf-8")), self._session_key)).decode("ascii")

    def _signed_request_with_id(self, request_type: str, body: str = "") -> tuple[str, int]:
        request_id = self._next_request_id()
        xml_command = self._start_request(request_id, request_type) + body
        return xml_command + f"<Sign>{self._sign(xml_command)}</Sign>\r\n</Request>\r\n", request_id

    def _signed_exchange_locked(self, request_type: str, body: str = "") -> bytes:
        xml, request_id = self._signed_request_with_id(request_type, body)
        return self._send_locked(xml, expected_type=request_type, expected_id=request_id)

    def _signed_exchange_frames_locked(
        self,
        request_type: str,
        body: str = "",
        *,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        xml, request_id = self._signed_request_with_id(request_type, body)
        return self._send_frames_locked(
            xml,
            expected_type=request_type,
            expected_id=request_id,
            post_response_listen_seconds=post_response_listen_seconds,
        )

    def _send_locked(self, xml: str, expected_type: str | None, expected_id: int | None) -> bytes:
        frames = self._send_frames_locked(xml, expected_type=expected_type, expected_id=expected_id)
        for response in frames:
            if self._matches_response(response, expected_type, expected_id):
                return response
        if frames:
            return frames[-1]
        raise NiceBidiConnectionError("device did not respond")

    def _send_frames_locked(
        self,
        xml: str,
        expected_type: str | None,
        expected_id: int | None,
        *,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        if not self._socket:
            raise NiceBidiConnectionError("socket is not open")
        self._socket.settimeout(self.timeout)
        _LOGGER.debug(
            "Sending Nice local request type=%s id=%s to %s:%s",
            expected_type or "unknown",
            expected_id if expected_id is not None else "unknown",
            self.host,
            self.port,
        )
        self._socket.sendall(_frame(xml))
        frames: list[bytes] = []
        deadline = time.time() + self.timeout
        post_response_deadline: float | None = None
        while time.time() < deadline:
            active_deadline = post_response_deadline or deadline
            response = self._recv_frame_locked(max(0.1, active_deadline - time.time()))
            if not response:
                break
            frames.append(response)
            if self._matches_response(response, expected_type, expected_id):
                _LOGGER.debug("Received matching Nice local response: %s", _response_summary(response))
                if post_response_listen_seconds <= 0:
                    return frames
                post_response_deadline = time.time() + post_response_listen_seconds
                deadline = post_response_deadline
                continue
            _LOGGER.debug("Received non-matching Nice local response: %s", _response_summary(response))
        if frames:
            _LOGGER.debug("Returning last Nice local response after timeout: %s", _response_summary(frames[-1]))
            return frames
        return []

    def _recv_frame_locked(self, timeout: float) -> bytes:
        if not self._socket:
            return b""
        self._socket.settimeout(timeout)
        chunks: list[bytes] = []
        while True:
            try:
                chunk = self._socket.recv(65535)
            except TimeoutError:
                break
            if not chunk:
                break
            chunks.append(chunk)
            if ETX in chunk:
                break
        return b"".join(chunks)

    def _matches_response(self, response: bytes, expected_type: str | None, expected_id: int | None) -> bool:
        text = _printable(response)
        if "<Response " not in text:
            return False
        if expected_type and _attr(text, "type") != expected_type:
            return False
        if expected_id is not None and _attr(text, "id") != str(expected_id):
            return False
        return True

    def _send_action_locked(self, action: str) -> None:
        body = (
            "<Devices>\r\n"
            f'<Device id="{self.device_id}">\r\n'
            "<Services>\r\n"
            f"<DoorAction>{action}</DoorAction>\r\n"
            "</Services>\r\n"
            "</Device>\r\n"
            "</Devices>\r\n"
        )
        response = self._signed_exchange_locked("CHANGE", body)
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_printable(response))

    def _send_dep_action_locked(self, action: str) -> None:
        response, _ = self._t4_request_locked(
            "DEP",
            build_dep_action_frame(DEP_ACTION_COMMANDS[action]),
            0x00,
            0x03,
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_printable(response))

    def _write_dmp_register_locked(
        self,
        group: int,
        parameter: int,
        value: bytes,
    ) -> None:
        response, _ = self._t4_request_locked(
            "DMP",
            build_dmp_write_frame(*DMP_TARGET_CONTROLLER, group, parameter, value),
            *DMP_TARGET_CONTROLLER,
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_printable(response))

    def _read_dmp_register_locked(
        self,
        registers: dict[str, dict[str, Any]],
        daddr: int,
        dendpoint: int,
        group: int,
        parameter: int,
        *,
        required: bool,
    ) -> None:
        response, plains = self._t4_request_locked(
            "DMP",
            build_dmp_read_frame(daddr, dendpoint, group, parameter),
            daddr,
            dendpoint,
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            if required:
                raise NiceBidiConnectionError(_printable(response))
            _LOGGER.debug(
                "Optional Nice DMP register read failed daddr=%02X dendpoint=%02X register=%02X/%02X: %s",
                daddr,
                dendpoint,
                group,
                parameter,
                _response_summary(response),
            )
            return
        for plain in plains:
            parsed = parse_dmp_response(plain)
            parsed_group = parsed.get("group")
            parsed_parameter = parsed.get("parameter")
            if parsed_group is None or parsed_parameter is None:
                continue
            key = f"{parsed_group}/{parsed_parameter}"
            if (daddr, dendpoint) != DMP_TARGET_CONTROLLER:
                key = f"{key}@{daddr:02X}.{dendpoint:02X}"
            registers[key] = parsed

    def _read_status_locked(self, *, include_extended: bool = False) -> NiceBidiStatus:
        registers: dict[str, dict[str, Any]] = {}
        for group, parameter in CORE_STATUS_REGISTERS:
            self._read_dmp_register_locked(
                registers,
                *DMP_TARGET_CONTROLLER,
                group,
                parameter,
                required=True,
            )
        if include_extended:
            for group, parameter in EXTENDED_CONTROLLER_REGISTERS:
                self._read_dmp_register_locked(
                    registers,
                    *DMP_TARGET_CONTROLLER,
                    group,
                    parameter,
                    required=False,
                )
            for group, parameter in OXI_INFO_REGISTERS:
                self._read_dmp_register_locked(
                    registers,
                    *DMP_TARGET_OXI,
                    group,
                    parameter,
                    required=False,
                )

        state_register = registers.get("04/01")
        state = _status_from_register(state_register)
        current = _dmp_uint(registers.get("04/11"))
        opened = _dmp_uint(registers.get("04/18"))
        closed = _dmp_uint(registers.get("04/19"))
        position: float | None = None
        if current is not None and opened is not None and closed is not None and opened != closed:
            position = max(0.0, min(100.0, ((current - closed) / (opened - closed)) * 100))
        elif state == STATE_CLOSED:
            position = 0.0
        elif state == STATE_OPEN:
            position = 100.0

        diagnostics_io = _dmp_bytes(registers.get("04/D1"))
        io_byte = diagnostics_io[2] if diagnostics_io and len(diagnostics_io) >= 3 else diagnostics_io[0] if diagnostics_io else None
        stop_reason_code = None
        stop_reason_value = _dmp_bytes(registers.get("04/D0"))
        if stop_reason_value:
            stop_reason_code = stop_reason_value[0]
        total_maneuver_count = _dmp_uint(registers.get("04/B3"))
        alternate_movement_count = _dmp_uint(registers.get("04/D4"))

        return NiceBidiStatus(
            state=state,
            position=round(position, 1) if position is not None else None,
            current_position=current,
            closed_position=closed,
            open_position=opened,
            registers={key: str(value.get("value_hex", "")) for key, value in registers.items()},
            max_open_position=_dmp_uint(registers.get("04/12")),
            partial_open_1_position=_dmp_uint(registers.get("04/21")),
            partial_open_2_position=_dmp_uint(registers.get("04/22")),
            partial_open_3_position=_dmp_uint(registers.get("04/23")),
            opening_speed=_dmp_uint(registers.get("04/42")),
            closing_speed=_dmp_uint(registers.get("04/43")),
            opening_force=_dmp_uint(registers.get("04/4A")),
            closing_force=_dmp_uint(registers.get("04/4B")),
            pause_time=_dmp_uint(registers.get("04/81")),
            photo_close_time=_dmp_uint(registers.get("04/85")),
            photo_close_mode=_dmp_uint(registers.get("04/86")),
            always_close_time=_dmp_uint(registers.get("04/89")),
            always_close_mode=_dmp_uint(registers.get("04/8A")),
            maintenance_threshold=_dmp_uint(registers.get("04/B1")),
            maintenance_count=_dmp_uint(registers.get("04/B2")),
            total_maneuver_count=total_maneuver_count if total_maneuver_count is not None else alternate_movement_count,
            alternate_movement_count=alternate_movement_count,
            input_1=_dmp_bool(registers.get("04/71")),
            input_2=_dmp_bool(registers.get("04/72")),
            input_3=_dmp_bool(registers.get("04/73")),
            input_4=_dmp_bool(registers.get("04/74")),
            auto_close=_dmp_bool(registers.get("04/80")),
            photo_close=_dmp_bool(registers.get("04/84")),
            always_close=_dmp_bool(registers.get("04/88")),
            standby=_dmp_bool(registers.get("04/8C")),
            pre_flash=_dmp_bool(registers.get("04/94")),
            key_lock=_dmp_bool(registers.get("04/9C")),
            limit_closed=bool(io_byte & 0x01) if io_byte is not None else None,
            limit_open=bool(io_byte & 0x02) if io_byte is not None else None,
            photocell=bool(io_byte & 0x04) if io_byte is not None else None,
            obstacle=stop_reason_code in {0x01, 0x02} if stop_reason_code is not None else None,
            diagnostics_io_byte=io_byte,
            last_stop_reason=STOP_REASON_BY_BYTE.get(stop_reason_code) if stop_reason_code is not None else None,
            last_stop_reason_code=stop_reason_code,
            diagnostics_parameters=str(registers.get("04/D2", {}).get("value_hex") or "") or None,
            oxi_detected=any(key.startswith("0A/") for key in registers),
            oxi_product=_dmp_ascii(registers.get("0A/09@00.0A")),
            oxi_hardware_version=_dmp_ascii(registers.get("0A/0A@00.0A")),
            oxi_firmware_version=_dmp_ascii(registers.get("0A/0B@00.0A")),
            oxi_description=_dmp_ascii(registers.get("0A/0C@00.0A")),
        )

    def _read_nhk_status_locked(self) -> NiceBidiStatus:
        frames = self._signed_exchange_frames_locked(
            "STATUS",
            post_response_listen_seconds=NHK_STATUS_POST_RESPONSE_LISTEN_SECONDS,
        )
        for frame in frames:
            if "<Error>" in _printable(frame):
                raise NiceBidiConnectionError(_printable(frame))
        return _parse_nhk_status_frames(frames, self.device_id)

    def _read_info_xml_locked(self) -> str:
        response = self._signed_exchange_locked("INFO")
        text = _printable(response)
        if "<Error>" in text:
            raise NiceBidiConnectionError(text)
        return _xml_payload(response)

    def _read_info_locked(self) -> NiceBidiDeviceInfo:
        return parse_info_xml(self._read_info_xml_locked(), self.device_id)

    def _t4_request_locked(
        self,
        protocol: str,
        plain_payload: bytes,
        daddr: int,
        dendpoint: int,
        tout_ms: int,
    ) -> tuple[bytes, list[bytes]]:
        key = _random_t4_key()
        encrypted = _xor_sha256(plain_payload, key)
        encrypted_b64 = base64.b64encode(encrypted).decode("ascii")
        key_b64 = base64.b64encode(key).decode("ascii")
        body = (
            '<Interface id="1">\r\n'
            f'<Protocol tout="{tout_ms}">{protocol}</Protocol>\r\n'
            f"<DAddress>{daddr:02X}</DAddress>\r\n"
            f"<DEndpoint>{dendpoint:02X}</DEndpoint>\r\n"
            f'<T4 id="1" len="{len(encrypted_b64)}" key="{key_b64}" '
            f'DAddress="{daddr}" DEndpoint="{dendpoint}">{encrypted_b64}</T4>\r\n'
            "</Interface>\r\n"
        )
        response = self._signed_exchange_locked("T4_REQUEST", body)
        plains = self._decrypt_t4_payloads(response)
        _LOGGER.debug(
            "Nice T4 %s request daddr=%02X dendpoint=%02X returned %s payload(s)",
            protocol,
            daddr,
            dendpoint,
            len(plains),
        )
        return response, plains

    def _decrypt_t4_payloads(self, response: bytes) -> list[bytes]:
        return _decrypt_t4_payloads_from_frame(response)

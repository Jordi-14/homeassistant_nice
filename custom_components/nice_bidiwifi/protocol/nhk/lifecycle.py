"""Bounded NHK device identity and read-only BOOT_1 inspection codecs."""

from __future__ import annotations

from dataclasses import asdict, dataclass
import re

from ...errors import NiceParameterError, NiceProtocolError
from .access import parse_response

READ_OPERATIONS = frozenset({"FILE_INFO", "DEV_ON_BOOT", "STATUS_PROGRAMMING"})
MAX_DEVICES = 64
BOOT_STATUSES = frozenset(
    {
        "UNKNOWN",
        "IDLE",
        "RUN",
        "SUCCESS",
        "STOP",
        "ERR_PKT",
        "ERR_EOF",
        "ERR_REQ_CHK",
        "ERR_CHK",
        "ERR_READ_FILE",
        "ERR_MEM",
        "ERR_INI",
        "ERR_WRONG_FILE",
        "ERR_NO_FILE",
        "ERR_NO_DEV",
        "GENERIC_ERROR",
        "ERASE_FAILED",
        "WRITE_FAILED",
        "SKIPPED_PACKET",
        "SEQUENCE_ERR",
        "TIMEOUT_ERR",
        "ALREADY_UPDATED",
        "ERR_INVALID_BUNDLE",
        "ERR_CFG_CMD_NOT_SENDABLE",
        "ERR_CFG_CMD_MAX_RETRY",
        "ERR_CANNOT_READ_CU_HW_VER",
        "ERR_LCU_NOT_FOUND",
        "ERR_T4_DEVICE_NOT_FOUND",
        "ERR_T4_FAST_ALREADY_PERFORMED",
        "ERR_T4_TASK_ERASE",
        "ERR_T4_TASK_BLOCK_SEND",
        "ERR_T4_FAST_CRC_VERIFY",
        "ERR_T4_FAST_TOO_MUCH_ATTEMPTS",
        "ERR_T4_FAST_ERROR_JUMP_TO_APP",
    }
)


def safe_label(value, maximum=96):
    if value is None:
        return None
    value = "".join(c for c in value.strip() if ord(c) >= 32)
    return value[:maximum] or None


def integer_id(value):
    if (
        not isinstance(value, str)
        or not re.fullmatch(r"[0-9]{1,5}", value)
        or not 0 <= int(value) <= 65535
    ):
        raise NiceProtocolError(
            "Missing or invalid advertised interface/device identity"
        )
    return int(value)


@dataclass(frozen=True, slots=True)
class Peripheral:
    device_id: int
    product: str | None
    manufacturer: str | None
    hardware: str | None
    firmware: str | None
    device_type: str | None


@dataclass(frozen=True, slots=True)
class DeviceInventory:
    interface_id: int | None
    interface_product: str | None
    interface_hardware: str | None
    interface_firmware: str | None
    devices: tuple[Peripheral, ...]
    complete: bool

    def export(self):
        # No serial numbers, descriptions, remote IDs, passwords or raw XML.
        return asdict(self)


def parse_inventory(frame):
    root = parse_response(frame, "INFO")
    if root.get("protocolType") not in {None, "NHK"}:
        raise NiceProtocolError("Unexpected inventory protocol namespace")
    interface = root.find("./Interface")
    if interface is None:
        raise NiceProtocolError("INFO has no interface identity")
    interface_id = (
        integer_id(interface.get("id")) if interface.get("id") is not None else None
    )
    nodes = root.findall("./Devices/Device")
    if len(nodes) > MAX_DEVICES:
        raise NiceProtocolError("Device inventory exceeds retention limit")
    devices, seen = [], set()
    for node in nodes:
        device_id = integer_id(node.get("id"))
        if device_id in seen:
            raise NiceProtocolError("Duplicate inventory device identity")
        seen.add(device_id)
        devices.append(
            Peripheral(
                device_id,
                *(
                    safe_label(node.findtext(key))
                    for key in ["Prod", "Manuf", "VersionHW", "VersionFW", "Type"]
                ),
            )
        )
    return DeviceInventory(
        interface_id,
        *(
            safe_label(interface.findtext(key))
            for key in ["Prod", "VersionHW", "VersionFW"]
        ),
        tuple(devices),
        root.find("./Devices") is not None
        and root.get("more", "false").lower() not in {"true", "1"},
    )


def boot_read_body(operation, interface_id):
    if operation not in READ_OPERATIONS:
        raise NiceParameterError("Only read-only BOOT_1 inspection is supported")
    if (
        isinstance(interface_id, bool)
        or not isinstance(interface_id, int)
        or not 0 <= interface_id <= 65535
    ):
        raise NiceParameterError("A verified interface ID is required")
    return f'<Interface id="{interface_id}"><Protocol>BOOT_1</Protocol><Type>{operation}</Type></Interface>\r\n'


def parse_boot_read(frame, operation, interface_id):
    boot_read_body(operation, interface_id)
    root = parse_response(frame, "T4_UPDATE")
    if root.get("protocolType") not in {None, "NHK"}:
        raise NiceProtocolError("Unexpected boot protocol namespace")
    interface = root.find("./Interface")
    if interface is None or integer_id(interface.get("id")) != interface_id:
        raise NiceProtocolError("BOOT_1 response interface does not match")
    if interface.findtext("Protocol") not in {None, "BOOT_1"} or interface.findtext(
        "Type"
    ) not in {None, operation}:
        raise NiceProtocolError("BOOT_1 response operation does not match")
    if operation == "FILE_INFO":
        checked = interface.findtext("CrcChecked")
        return {
            "firmware": safe_label(interface.findtext("FwVersion")),
            "hardware_versions": tuple(
                safe_label(x, 32)
                for x in (interface.findtext("HwVerList") or "").split(",")[:32]
                if x.strip()
            ),
            "crc_checked_reported": {"true": True, "false": False}.get(checked),
            "authenticity_verified": False,
        }
    if operation == "STATUS_PROGRAMMING":
        progress = interface.findtext("Progress")
        if progress is not None and (
            not re.fullmatch(r"-?[0-9]{1,10}", progress)
            or not -(2**31) <= int(progress) < 2**31
        ):
            raise NiceProtocolError("Invalid reported programming progress")
        return {
            "status_reported": interface.findtext("Status")
            if interface.findtext("Status") in BOOT_STATUSES
            else "UNKNOWN",
            "status_recognized": interface.findtext("Status") in BOOT_STATUSES,
            "progress_reported": int(progress) if progress is not None else None,
            "progress_unit": "unverified",
            "started_by_integration": False,
        }
    nodes = interface.findall("./Devices/Device")
    if len(nodes) > MAX_DEVICES:
        raise NiceProtocolError("Boot inventory exceeds retention limit")
    rows, seen = [], set()
    for node in nodes:
        device_id = integer_id(node.get("id"))
        if device_id in seen:
            raise NiceProtocolError("Duplicate boot device")
        seen.add(device_id)
        # DAddress/DEndpoint are retained privately only when a future adapter
        # verifies targeting; this observation cannot authorize programming.
        rows.append(
            {
                "device_id": device_id,
                "hardware": safe_label(node.findtext("HwVer")),
                "modular_reported": {"true": True, "false": False}.get(
                    node.findtext("IsModular")
                ),
            }
        )
    return {
        "devices": tuple(rows),
        "complete": interface.find("./Devices") is not None,
        "entered_bootloader": False,
    }

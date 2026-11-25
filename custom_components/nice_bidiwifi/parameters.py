"""Typed controller metadata, observations and reviewed scalar write policies.

Wire formats remain protocol-specific. This repository contains no Home Assistant
or network code and never assumes that an array index identifies a particular motor.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from dataclasses import replace
from datetime import UTC, datetime
from enum import StrEnum
from fractions import Fraction
from threading import RLock
import time

from .errors import NiceParameterError
from .models.device import NiceDeviceInfo
from .models.profiles import normalized_device_identity
from .protocol.t4.menu import MenuPermission
from .protocol.t4.info import DmpInfo, scaled_value
from .protocol.t4.settings import SETTINGS_BY_REGISTER, DmpSetting
from .write_policy import dmp_write_block_reason


class SupportState(StrEnum):
    UNKNOWN = "unknown"
    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    INVALID = "invalid"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True, slots=True)
class ParameterAddress:
    device_id: int
    group: int
    parameter: int
    address: int = 0
    endpoint: int = 3
    index: int | None = None

    def __post_init__(self) -> None:
        if self.device_id < 1 or any(
            not 0 <= x <= 255
            for x in (self.address, self.endpoint, self.group, self.parameter)
        ):
            raise ValueError("Invalid parameter target")
        if self.index is not None and self.index < 0:
            raise ValueError("Invalid parameter index")

    @property
    def key(self) -> str:
        suffix = f"[{self.index}]" if self.index is not None else ""
        return f"{self.device_id}:{self.address:02X}.{self.endpoint:02X}/{self.group:02X}/{self.parameter:02X}{suffix}"


@dataclass(frozen=True, slots=True)
class ParameterObservation:
    raw: bytes | None
    raw_value: int | None
    value: int | float | None
    source: str
    observed_at: datetime
    monotonic: float


@dataclass(slots=True)
class ParameterRecord:
    metadata: DmpInfo | None = None
    raw_info: bytes | None = None
    support: dict[str, SupportState] = field(
        default_factory=lambda: {
            "info": SupportState.UNKNOWN,
            "get": SupportState.UNKNOWN,
            "set": SupportState.UNKNOWN,
        }
    )
    observation: ParameterObservation | None = None
    last_error: str | None = None
    indexed_observed: bool = False


@dataclass(frozen=True, slots=True)
class PreparedParameterWrite:
    setting: DmpSetting
    value: int
    payload: bytes
    byte_order: str = "big"
    address: ParameterAddress | None = None


class ParameterRepository:
    """Per-entry parameter identity, metadata and independently tracked operations."""

    def __init__(self, device_id: int = 1) -> None:
        self.device_id = device_id
        self._records: dict[ParameterAddress, ParameterRecord] = {}
        self._menu_permissions: dict[ParameterAddress, MenuPermission] = {}
        self._menu_generation = 0
        self._lock = RLock()
        # Stable compatibility views while the HA adapters are migrated.
        self.setting_info: dict[str, DmpInfo] = {}
        self.register_info: dict[str, DmpInfo] = {}

    def address(
        self, group: int, parameter: int, *, target=(0, 3), index=None
    ) -> ParameterAddress:
        return ParameterAddress(self.device_id, group, parameter, *target, index)

    def metadata(self, setting: DmpSetting) -> DmpInfo | None:
        return self.setting_info.get(setting.key)

    def support_for(self, address: ParameterAddress, operation: str) -> SupportState:
        """Expose operation support without leaking mutable repository records."""
        self._check_scope(address)
        if operation not in {"info", "get", "set"}:
            raise ValueError("Unknown parameter operation")
        with self._lock:
            record = self._records.get(address)
            return record.support[operation] if record else SupportState.UNKNOWN

    def observation_for(self, address: ParameterAddress) -> ParameterObservation | None:
        self._check_scope(address)
        with self._lock:
            record = self._records.get(address)
            return record.observation if record else None

    def _check_scope(self, address: ParameterAddress) -> None:
        if address.device_id != self.device_id:
            raise ValueError("Parameter belongs to a different device")

    def info_for(self, address: ParameterAddress) -> DmpInfo | None:
        with self._lock:
            record = self._records.get(replace(address, index=None))
            return record.metadata if record else None

    def record_info(
        self,
        address: ParameterAddress,
        info: DmpInfo | None,
        *,
        raw: bytes | None = None,
        state: SupportState | None = None,
    ) -> None:
        self._check_scope(address)
        with self._lock:
            record = self._records.setdefault(address, ParameterRecord())
            record.metadata, record.raw_info = (
                info,
                raw if raw is not None else (info.raw if info else None),
            )
            record.indexed_observed |= bool(info and info.indexed) or bool(
                raw and len(raw) >= 4 and raw[3] & 0x80
            )
            record.support["info"] = state or (
                SupportState.SUPPORTED if info else SupportState.UNAVAILABLE
            )
            if address.address == 0 and address.endpoint == 3 and address.index is None:
                setting = SETTINGS_BY_REGISTER.get((address.group, address.parameter))
                store = self.setting_info if setting else self.register_info
                key = (
                    setting.key
                    if setting
                    else f"{address.group:02X}/{address.parameter:02X}"
                )
                if info is None:
                    store.pop(key, None)
                else:
                    store[key] = info
            if record.observation is not None:
                old = record.observation
                raw_value, value = self._decode(address, old.raw, info)
                record.observation = ParameterObservation(
                    old.raw,
                    raw_value,
                    value,
                    old.source,
                    old.observed_at,
                    old.monotonic,
                )

    def record_support(
        self,
        address: ParameterAddress,
        operation: str,
        state: SupportState,
        *,
        error: str | None = None,
    ) -> None:
        self._check_scope(address)
        if operation not in {"info", "get", "set"}:
            raise ValueError("Unknown parameter operation")
        with self._lock:
            record = self._records.setdefault(address, ParameterRecord())
            record.support[operation] = state
            record.last_error = error

    def support_state(self, address: ParameterAddress, operation: str) -> SupportState:
        with self._lock:
            record = self._records.get(address)
            return record.support[operation] if record else SupportState.UNKNOWN

    def observe(
        self, address: ParameterAddress, raw: bytes | None, *, source="dmp_get"
    ) -> ParameterObservation:
        self._check_scope(address)
        with self._lock:
            record = self._records.setdefault(address, ParameterRecord())
            info = record.metadata
            if info is None and address.index is not None:
                base = self._records.get(replace(address, index=None))
                info = base.metadata if base else None
            setting = SETTINGS_BY_REGISTER.get((address.group, address.parameter))
            if setting and address.address == 0 and address.endpoint == 3:
                info = self.setting_info.get(setting.key, info)
            raw_value, value = self._decode(address, raw, info)
            if (
                setting
                and (address.address, address.endpoint) == (0, 3)
                and setting.kind == "boolean"
                and value is not None
            ):
                value = bool(value)
            observation = ParameterObservation(
                raw, raw_value, value, source, datetime.now(UTC), time.monotonic()
            )
            record.observation = observation
            if raw is not None:
                record.support["get"] = SupportState.SUPPORTED
            return observation

    @staticmethod
    def _decode(address: ParameterAddress, raw: bytes | None, info: DmpInfo | None):
        if not raw or all(b == 0xFF for b in raw):
            return None, None
        data = raw
        if info is None:
            setting = SETTINGS_BY_REGISTER.get((address.group, address.parameter))
            if (
                setting is None
                or (address.address, address.endpoint) != (0, 3)
                or address.index is not None
                or len(data) != setting.size
            ):
                return None, None
        if info is not None:
            if info.indexed:
                if (
                    address.index is None
                    or info.array_size is None
                    or address.index >= info.array_size
                    or len(data) != info.size + info.index_size
                ):
                    return None, None
                if int.from_bytes(data[: info.index_size], "big") != address.index:
                    return None, None
                data = data[info.index_size :]
            if (
                info.data_format not in {0x00, 0x25}
                or info.values is not None and info.scalar_options is None
                or len(data) != info.size
            ):
                return None, None
        value = int.from_bytes(data, info.byte_order if info else "big")
        return value, scaled_value(value, info) if info else value

    def record_menu_permission(self, address: ParameterAddress, permission: MenuPermission) -> None:
        """Record a verified session/menu adapter observation for the exact target."""
        self._check_scope(address)
        if ((address.address, address.endpoint, address.index) != (0, 3, None)
                or permission.device_id != address.device_id
                or permission.binding != (address.group, address.parameter)
                or permission.entry.binding != permission.binding
                or permission.session_generation != self._menu_generation):
            raise NiceParameterError("Menu permission has a different scope or session")
        from .controller_catalog import PARAMETERS_BY_REGISTER
        definition = PARAMETERS_BY_REGISTER.get((address.group, address.parameter))
        if definition is None or definition.app_id != permission.entry.app_id:
            raise NiceParameterError("Menu command identity does not match the reviewed binding")
        with self._lock:
            self._menu_permissions[address] = permission

    @property
    def menu_generation(self) -> int:
        return self._menu_generation

    def invalidate_menu_permissions(self) -> None:
        with self._lock:
            self._menu_generation += 1
            self._menu_permissions.clear()

    def block_reason(
        self, setting: DmpSetting, device: NiceDeviceInfo | None = None
    ) -> str | None:
        if reason := dmp_write_block_reason(device, setting.group, setting.parameter):
            return reason
        info = self.metadata(setting)
        if setting.requires_menu_permission:
            permission = self._menu_permissions.get(self.address(setting.group, setting.parameter))
            if permission is None or permission.session_generation != self._menu_generation or not 0 <= time.monotonic() - permission.verified_monotonic <= 30 or permission.can_set() is None:
                return "controller menu permissions are not verified"
            if not permission.can_set():
                return "controller menu denies this write for the current role"
            if info is None:
                return "controller INFO is required for this setting"
        if info is not None:
            if info.indexed:
                return "indexed setting: scalar writes are not supported"
            if (
                not 1 <= info.size <= 8
                or info.data_format not in {0x00, 0x25}
                or info.byte_order not in {"big", "little"}
                or not info.flags & 0x40 and info.flags & 0x30 == 0x30
                or info.flags & 0x40
                and not info.flags & 0x20 and info.scalar_options is None
                or info.flags & 0x60 == 0x60
                and not info.scalar_options
            ):
                return "unverified scalar value format"
            if not info.flags & 0x40 and any(
                x is None for x in (info.minimum, info.maximum, info.step)
            ):
                return "scalar INFO constraints are incomplete"
            try:
                self.limits(setting)
            except NiceParameterError as err:
                return str(err)
        else:
            identity = normalized_device_identity(device)
            if (setting.group, setting.parameter) in {(4, 0x4A), (4, 0x4B)} and any(
                x in identity for x in ("mc824h", "hyke")
            ):
                return "MC824H / HYKE force metadata is missing or invalid"
        with self._lock:
            record = self._records.get(self.address(setting.group, setting.parameter))
            if record and record.indexed_observed:
                return "indexed setting observed; scalar writes remain disabled"
            if record and record.support["set"] is SupportState.UNSUPPORTED:
                return "controller does not support SET for this setting"
            if (
                record
                and record.support["info"] is SupportState.INVALID
                and info is None
            ):
                return "controller INFO is malformed"
        return None

    def limits(
        self, setting: DmpSetting, *, dynamic_max: int | None = None
    ) -> tuple[int, int]:
        low, high = setting.minimum, setting.maximum
        if dynamic_max is not None:
            high = min(high, dynamic_max)
        info = self.metadata(setting)
        if info:
            values = info.scalar_options or ()
            if values:
                low, high = max(low, min(values)), min(high, max(values))
            else:
                if info.minimum is not None:
                    low = max(low, info.minimum)
                if info.maximum is not None:
                    high = min(high, info.maximum)
        if low > high:
            raise NiceParameterError(
                "controller INFO contradicts the reviewed setting range"
            )
        return low, high

    def prepare_write(
        self,
        setting: DmpSetting,
        value: int,
        *,
        device=None,
        dynamic_max: int | None = None,
    ) -> PreparedParameterWrite:
        if SETTINGS_BY_REGISTER.get((setting.group, setting.parameter)) != setting:
            raise NiceParameterError("setting is not in the reviewed catalog")
        if reason := self.block_reason(setting, device):
            raise NiceParameterError(reason)
        if not isinstance(value, int) or isinstance(value, bool):
            raise NiceParameterError("setting value must be an integer")
        low, high = self.limits(setting, dynamic_max=dynamic_max)
        if not low <= value <= high:
            raise NiceParameterError(f"{setting.key} must be between {low} and {high}")
        info = self.metadata(setting)
        if info and info.scalar_options:
            if value not in info.scalar_options:
                raise NiceParameterError(
                    f"{setting.key} must be one of {list(info.scalar_options)}"
                )
        elif info and info.step:
            anchor = info.minimum if info.minimum is not None else setting.minimum
            if (value - anchor) % info.step:
                raise NiceParameterError(
                    f"{setting.key} must follow step {info.step} from {anchor}"
                )
        size, order = (info.size, info.byte_order) if info else (setting.size, "big")
        try:
            payload = value.to_bytes(size, order)
        except OverflowError as err:
            raise NiceParameterError(
                f"value does not fit the controller's {size}-byte format"
            ) from err
        return PreparedParameterWrite(
            setting,
            value,
            payload,
            order,
            self.address(setting.group, setting.parameter),
        )

    def prepare_native_write(
        self, setting: DmpSetting, value: int | float, **kwargs
    ) -> PreparedParameterWrite:
        """Convert engineering units exactly; existing HA numbers retain their raw API."""
        try:
            raw = Fraction(str(value))
        except (ValueError, ZeroDivisionError) as err:
            raise NiceParameterError("value must be finite") from err
        info = self.metadata(setting)
        if info and not info.flags & 0x40 and info.flags & 0x30:
            if not info.scale:
                raise NiceParameterError("scaling metadata is incomplete")
            raw = raw * info.scale if info.flags & 0x10 else raw / info.scale
        if raw.denominator != 1:
            raise NiceParameterError(
                "value cannot be represented exactly by the controller"
            )
        return self.prepare_write(setting, int(raw), **kwargs)

    def diagnostics(self) -> dict:
        with self._lock:
            result = {}
            for address, record in self._records.items():
                if (address.group, address.parameter) in {
                    (4, 0xFB),
                    (4, 0xB8),
                    (4, 0xBA),
                    (4, 0xBB),
                }:
                    continue
                observation = record.observation
                info = asdict(record.metadata) if record.metadata else None
                if info:
                    info.pop("raw", None)
                    info["signed"] = record.metadata.signed
                result[address.key] = {
                    "metadata": info,
                    "raw_info": record.raw_info.hex(" ") if record.raw_info else None,
                    "support": {k: v.value for k, v in record.support.items()},
                    "raw": observation.raw.hex(" ")
                    if observation and observation.raw
                    else None,
                    "value": observation.value if observation else None,
                    "raw_value": observation.raw_value if observation else None,
                    "source": observation.source if observation else None,
                    "observed_at": observation.observed_at.isoformat()
                    if observation
                    else None,
                    "last_error": record.last_error,
                    "indexed_observed": record.indexed_observed,
                    "definition": asdict(
                        SETTINGS_BY_REGISTER[(address.group, address.parameter)]
                    )
                    if (address.address, address.endpoint) == (0, 3)
                    and (address.group, address.parameter) in SETTINGS_BY_REGISTER
                    else None,
                }
            return result

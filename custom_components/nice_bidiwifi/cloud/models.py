"""Typed models for one-time MyNice credential import."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import re
from typing import Any

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)

from ..const import (
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DEFAULT_DEVICE_ID,
    DEFAULT_PORT,
    DEFAULT_T4_TIMEOUT_MS,
)
from ..errors import NiceCloudSchemaError
from ..models.config import ConnectionMode
from ..models.discovery import normalize_device_id

_PASSWORD_PATTERN = re.compile(r"[0-9a-fA-F]{64}")
_MAX_TEXT_LENGTH = 256


@dataclass(slots=True, repr=False)
class NiceCloudLogin:
    """Secrets used for a single cloud bootstrap request."""

    account_username: str
    account_password: str
    oauth_client_id: str
    oauth_client_secret: str

    def __post_init__(self) -> None:
        """Reject incomplete or unreasonably large secret values."""
        for value in (
            self.account_username,
            self.account_password,
            self.oauth_client_id,
            self.oauth_client_secret,
        ):
            if not value or len(value) > _MAX_TEXT_LENGTH:
                raise ValueError("Cloud authentication fields must be 1-256 characters")
        if ":" in self.oauth_client_id:
            raise ValueError("The OAuth client ID cannot contain a colon")

    def clear(self) -> None:
        """Release references to account and OAuth secrets."""
        self.account_username = ""
        self.account_password = ""
        self.oauth_client_id = ""
        self.oauth_client_secret = ""


@dataclass(slots=True, repr=False)
class TransientAccessToken:
    """A short-lived token that can be explicitly discarded."""

    value: str | None
    token_type: str
    expires_in: int

    @property
    def authorization(self) -> str:
        """Return the HTTP authorization value while the token is valid."""
        if self.value is None or self.expires_in <= 0:
            raise NiceCloudSchemaError("The cloud access token is missing or expired")
        return f"{self.token_type} {self.value}"

    def clear(self) -> None:
        """Release the token reference."""
        self.value = None


@dataclass(frozen=True, slots=True, repr=False)
class NiceCloudAccessory:
    """One accessory credential tuple returned by the bootstrap service."""

    name: str
    target_mac: str
    username: str
    password: str
    source_id: str
    device_id: int = DEFAULT_DEVICE_ID

    def __post_init__(self) -> None:
        """Validate the persisted local credential boundary."""
        if not self.name.strip() or len(self.name) > _MAX_TEXT_LENGTH:
            raise ValueError("Accessory name must be 1-256 characters")
        if normalize_device_id(self.target_mac) != self.target_mac:
            raise ValueError("Accessory identity must be a normalized MAC address")
        if (
            not self.username
            or len(self.username) > _MAX_TEXT_LENGTH
            or not self.source_id
            or len(self.source_id) > _MAX_TEXT_LENGTH
        ):
            raise ValueError("Accessory NHK identifiers must be 1-256 characters")
        if _PASSWORD_PATTERN.fullmatch(self.password) is None:
            raise ValueError("Accessory NHK password must be 64 hexadecimal characters")
        if self.device_id < 1:
            raise ValueError("Accessory device ID must be positive")

    @property
    def identity(self) -> str:
        """Return the stable config-entry identity."""
        return self.target_mac

    def entry_data(
        self,
        *,
        mode: ConnectionMode,
        host: str | None = None,
        port: int = DEFAULT_PORT,
        relay_host: str | None = None,
        relay_port: int = DEFAULT_PORT,
        name: str | None = None,
        device_id: int | None = None,
        t4_timeout_ms: int = DEFAULT_T4_TIMEOUT_MS,
    ) -> dict[str, Any]:
        """Build mode-aware entry data without any cloud account secret."""
        if mode in {
            ConnectionMode.LOCAL_ONLY,
            ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK,
        } and not (host and host.strip()):
            raise ValueError("The selected connection mode requires a local endpoint")
        if mode in {
            ConnectionMode.CLOUD_ONLY,
            ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK,
        } and not (relay_host and relay_host.strip()):
            raise ValueError("The selected connection mode requires a relay endpoint")
        data: dict[str, Any] = {
            CONF_NAME: (name or self.name).strip(),
            CONF_USERNAME: self.username,
            CONF_PASSWORD: self.password,
            CONF_SOURCE_ID: self.source_id,
            CONF_TARGET_MAC: self.target_mac,
            CONF_DEVICE_ID: device_id or self.device_id,
            CONF_T4_TIMEOUT_MS: t4_timeout_ms,
            CONF_CONNECTION_MODE: mode.value,
        }
        if host:
            data[CONF_HOST] = host.strip()
            data[CONF_PORT] = port
        if relay_host:
            data[CONF_RELAY_HOST] = relay_host.strip()
            data[CONF_RELAY_PORT] = relay_port
        return data


@dataclass(frozen=True, slots=True)
class NiceCloudBootstrapResult:
    """Parsed accessories plus the number of unusable records."""

    accessories: tuple[NiceCloudAccessory, ...]
    skipped_records: int = 0


def parse_macro_user(payload: object) -> NiceCloudBootstrapResult:
    """Parse the current macro-user response into bounded credential models."""
    root = _mapping(payload, "response")
    if root.get("result") is not True:
        raise NiceCloudSchemaError("The cloud service did not return a successful result")
    data = _mapping(root.get("data"), "data")
    smart_devices = _sequence(data.get("smartDevices"), "data.smartDevices")

    accessories: dict[str, NiceCloudAccessory] = {}
    skipped = 0
    for smart_device_value in smart_devices:
        if not isinstance(smart_device_value, Mapping):
            skipped += 1
            continue
        records = smart_device_value.get("accessoryCredentials")
        if not isinstance(records, Sequence) or isinstance(
            records,
            str | bytes | bytearray,
        ):
            skipped += 1
            continue
        for record_value in records:
            try:
                accessory = _parse_accessory(record_value)
            except (NiceCloudSchemaError, ValueError):
                skipped += 1
                continue
            if accessory.identity in accessories:
                skipped += 1
                continue
            accessories[accessory.identity] = accessory

    if not accessories:
        raise NiceCloudSchemaError(
            "The cloud response contained no complete accessory credentials"
        )
    return NiceCloudBootstrapResult(
        accessories=tuple(accessories.values()),
        skipped_records=skipped,
    )


def _parse_accessory(value: object) -> NiceCloudAccessory:
    """Parse one credential record."""
    record = _mapping(value, "accessory credential")
    raw_mac = _required_text(record, "accessoryMacAddress")
    target_mac = normalize_device_id(raw_mac)
    if target_mac is None:
        raise NiceCloudSchemaError("An accessory has an invalid hardware identity")

    password = _required_text(record, "accessoryPassword").upper()
    if _PASSWORD_PATTERN.fullmatch(password) is None:
        raise NiceCloudSchemaError("An accessory has an invalid NHK password")

    name = _optional_text(record.get("description")) or "Nice Gate"
    return NiceCloudAccessory(
        name=name,
        target_mac=target_mac,
        username=_required_text(record, "accessoryUser"),
        password=password,
        source_id=_required_text(record, "controllerID"),
    )


def _mapping(value: object, label: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise NiceCloudSchemaError(f"The cloud {label} is not an object")
    return value


def _sequence(value: object, label: str) -> Sequence[object]:
    if not isinstance(value, Sequence) or isinstance(
        value,
        str | bytes | bytearray,
    ):
        raise NiceCloudSchemaError(f"The cloud {label} field is not a list")
    return value


def _required_text(data: Mapping[str, Any], key: str) -> str:
    value = _optional_text(data.get(key))
    if value is None:
        raise NiceCloudSchemaError(f"An accessory is missing {key}")
    return value


def _optional_text(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    if not normalized or len(normalized) > _MAX_TEXT_LENGTH:
        return None
    return normalized

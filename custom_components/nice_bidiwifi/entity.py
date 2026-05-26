"""Shared entity helpers for Nice BiDi-WiFi."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST, CONF_NAME
from homeassistant.helpers.entity import DeviceInfo

from .client import NiceBidiDeviceInfo
from .const import CONF_DEVICE_ID, CONF_TARGET_MAC, DEFAULT_DEVICE_ID, DOMAIN


def bidi_unique_id(entry: ConfigEntry, suffix: str) -> str:
    """Build a stable unique ID for one BiDi entity."""
    target_mac = entry.data[CONF_TARGET_MAC]
    device_id = entry.data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)
    normalized_mac = target_mac.lower().replace(":", "")
    return f"{normalized_mac}_{device_id}_{suffix}"


def bidi_device_info(entry: ConfigEntry, info: NiceBidiDeviceInfo | None = None) -> DeviceInfo:
    """Build Home Assistant device info from config and optional INFO metadata."""
    target_mac = entry.data[CONF_TARGET_MAC]
    return DeviceInfo(
        identifiers={(DOMAIN, target_mac)},
        name=entry.data.get(CONF_NAME),
        manufacturer=info.device_manufacturer if info and info.device_manufacturer else "Nice",
        model=(
            info.device_description
            if info and info.device_description
            else info.interface_product
            if info and info.interface_product
            else "BiDi-WiFi"
        ),
        serial_number=info.device_serial if info and info.device_serial else None,
        sw_version=info.device_fw_version if info and info.device_fw_version else None,
        hw_version=info.device_hw_version if info and info.device_hw_version else None,
        configuration_url=f"https://{entry.data[CONF_HOST]}",
    )

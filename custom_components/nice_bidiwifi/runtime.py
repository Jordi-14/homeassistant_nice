"""Runtime helpers for Nice."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntry

if TYPE_CHECKING:
    from .coordinator import NiceBidiDataUpdateCoordinator


NiceBidiConfigEntry = ConfigEntry


def get_coordinator(entry: ConfigEntry) -> NiceBidiDataUpdateCoordinator:
    """Return the coordinator stored on the config entry runtime data."""
    return cast("NiceBidiDataUpdateCoordinator", entry.runtime_data)

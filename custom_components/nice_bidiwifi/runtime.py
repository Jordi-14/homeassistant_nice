"""Runtime helpers for Nice."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from homeassistant.config_entries import ConfigEntry

from .models.config import NiceEntryConfig

if TYPE_CHECKING:
    from .coordinator import NiceBidiDataUpdateCoordinator


@dataclass(slots=True)
class NiceRuntimeData:
    """Typed objects owned by one loaded config entry."""

    coordinator: NiceBidiDataUpdateCoordinator
    config: NiceEntryConfig


type NiceBidiConfigEntry = ConfigEntry[NiceRuntimeData]


def get_coordinator(entry: ConfigEntry) -> NiceBidiDataUpdateCoordinator:
    """Return the coordinator stored on the config entry runtime data."""
    runtime_data = entry.runtime_data
    if isinstance(runtime_data, NiceRuntimeData):
        return runtime_data.coordinator
    return cast("NiceBidiDataUpdateCoordinator", runtime_data)

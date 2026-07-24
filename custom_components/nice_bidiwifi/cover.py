"""Cover platform for Nice."""

from __future__ import annotations

from typing import Any

from homeassistant.components.cover import DOMAIN as COVER_DOMAIN, ATTR_POSITION, CoverDeviceClass, CoverEntity, CoverEntityFeature
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import STATE_CLOSED, STATE_CLOSING, STATE_OPEN, STATE_OPENING, STATE_PARTIALLY_OPEN, STATE_STOPPED, NiceBidiStatus
from .coordinator import NiceBidiDataUpdateCoordinator
from .entities.factory import (
    NiceCapabilityKey,
    NiceCoreEntityDescription,
    build_described_entities,
    entity_support,
    EntitySupport,
)
from .entity import NiceCoordinatorEntity
from .runtime import get_coordinator

PARALLEL_UPDATES = 1

COVERS = (
    NiceCoreEntityDescription(
        key="cover",
        required_capability=NiceCapabilityKey.OPEN_CLOSE,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up cover from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(
        build_described_entities(
            coordinator,
            entry,
            COVERS,
            lambda entity_coordinator, entity_entry, _description: NiceBidiCover(
                entity_coordinator,
                entity_entry,
            ),
        )
    )


class NiceBidiCover(NiceCoordinatorEntity, CoverEntity):
    """Nice gate cover."""

    _attr_device_class = CoverDeviceClass.GATE
    _attr_has_entity_name = True

    def __init__(self, coordinator: NiceBidiDataUpdateCoordinator, entry: ConfigEntry) -> None:
        """Initialize the cover."""
        super().__init__(
            coordinator,
            entry,
            platform_domain=COVER_DOMAIN,
            unique_id_suffix="cover",
            name=None,
            suggested_id_suffix=None,
        )

    @property
    def supported_features(self) -> CoverEntityFeature:
        """Return supported cover features."""
        features = CoverEntityFeature.OPEN | CoverEntityFeature.CLOSE | CoverEntityFeature.STOP
        if self.status is not None and self.coordinator.display_position is not None:
            features |= CoverEntityFeature.SET_POSITION
        return features

    @property
    def available(self) -> bool:
        """Return true if the latest coordinator update succeeded."""
        return (
            self.coordinator.last_update_success
            and self.status is not None
            and entity_support(self.coordinator, COVERS[0])
            is not EntitySupport.UNSUPPORTED
        )

    @property
    def status(self) -> NiceBidiStatus | None:
        """Return current BiDi status."""
        return self.coordinator.data

    @property
    def current_cover_position(self) -> int | None:
        """Return current or estimated display position."""
        position = self.coordinator.display_position
        if position is None:
            return None
        return round(position)

    @property
    def is_closed(self) -> bool | None:
        """Return if the cover is closed."""
        status = self.status
        if status is None:
            return None
        if self.coordinator.position_simulation_action == "open":
            return False
        if status.state == STATE_CLOSED:
            return True
        if status.state in {STATE_OPEN, STATE_OPENING, STATE_CLOSING, STATE_STOPPED, STATE_PARTIALLY_OPEN}:
            return False
        return None

    @property
    def is_opening(self) -> bool | None:
        """Return if the cover is opening."""
        status = self.status
        if status is None:
            return None
        return status.state == STATE_OPENING or self.coordinator.position_simulation_action == "open"

    @property
    def is_closing(self) -> bool | None:
        """Return if the cover is closing."""
        status = self.status
        if status is None:
            return None
        return status.state == STATE_CLOSING or self.coordinator.position_simulation_action == "close"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return diagnostic attributes."""
        status = self.status
        if status is None:
            return {}
        return {
            "bidi_state": status.state,
            "current_position_raw": status.current_position,
            "real_position": status.position,
            "display_position": self.coordinator.display_position,
            "display_position_estimated": self.coordinator.display_position_estimated,
            "position_reporting_observed": self.coordinator.position_reporting_observed,
            "state_source": self.coordinator.state_source,
            "position_source": self.coordinator.position_source,
            "position_confidence": self.coordinator.position_confidence,
            "position_simulation_action": self.coordinator.position_simulation_action,
            "position_simulation_speed_percent_per_second": (
                self.coordinator.position_simulation_speed_percent_per_second
            ),
            "closed_position_raw": status.closed_position,
            "open_position_raw": status.open_position,
            "position_calibration_state": self.coordinator.calibration_state,
            "position_calibration_quality": self.coordinator.calibration_quality,
            "position_calibration_updated_at": self.coordinator.calibration_updated_at,
        }

    async def async_open_cover(self, **kwargs: Any) -> None:
        """Open the gate."""
        await self.coordinator.async_send_action("open")

    async def async_close_cover(self, **kwargs: Any) -> None:
        """Close the gate."""
        await self.coordinator.async_send_action("close")

    async def async_stop_cover(self, **kwargs: Any) -> None:
        """Stop the gate."""
        await self.coordinator.async_send_action("stop")

    async def async_set_cover_position(self, **kwargs: Any) -> None:
        """Move toward a target position and stop after reaching it."""
        await self.coordinator.async_set_position(kwargs[ATTR_POSITION])

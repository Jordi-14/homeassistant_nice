"""Event platform for unsolicited Nice protocol events."""

from __future__ import annotations

from homeassistant.components.event import DOMAIN as EVENT_DOMAIN, EventEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NiceBidiDataUpdateCoordinator
from .entity import NiceCoordinatorEntity
from .models.events import NiceEventCategory
from .runtime import get_coordinator


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the normalized protocol event entity."""
    coordinator = get_coordinator(entry)
    if (
        coordinator.capabilities is not None
        and coordinator.capabilities.local_events is False
    ):
        return
    async_add_entities([NiceProtocolEventEntity(coordinator, entry)])


class NiceProtocolEventEntity(NiceCoordinatorEntity, EventEntity):
    """Publish bounded normalized change and diagnostic events."""

    _attr_has_entity_name = True
    _attr_event_types = [category.value for category in NiceEventCategory]
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
    ) -> None:
        """Initialize the event stream entity."""
        super().__init__(
            coordinator,
            entry,
            platform_domain=EVENT_DOMAIN,
            unique_id_suffix="protocol_event",
            name="Protocol event",
            suggested_id_suffix="Protocol event",
        )
        self._seen_sequence = coordinator.event_sequence

    @property
    def available(self) -> bool:
        """Return true while unsolicited event delivery is available."""
        return self.coordinator.event_stream_state in {"idle", "active"}

    def _handle_coordinator_update(self) -> None:
        event = self.coordinator.latest_event
        sequence = self.coordinator.event_sequence
        if event is not None and sequence != self._seen_sequence:
            self._seen_sequence = sequence
            self._trigger_event(
                event.category.value,
                event.as_event_attributes(),
            )
        super()._handle_coordinator_update()

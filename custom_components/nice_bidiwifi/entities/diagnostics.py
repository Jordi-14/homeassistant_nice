"""Optional read-only diagnostic screen cards with provenance and uncertainty."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..diagnostic_runtime import DIAGNOSTICS
from ..models.capabilities import ProductFamily
from ..entity import NiceCoordinatorEntity


class DiagnosticScreenSensor(NiceCoordinatorEntity, SensorEntity):
    _unrecorded_attributes = frozenset({"age_seconds", "last_attempt_age_seconds", "received_at"})
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, owner, entry, definition):
        super().__init__(
            owner,
            entry,
            platform_domain="sensor",
            unique_id_suffix="diagnostic_screen_" + definition.key,
            name=definition.title,
            suggested_id_suffix=definition.title + " evidence",
        )
        self.definition = definition
        self._attr_translation_key = "diagnostic_" + definition.key
        del self._attr_name

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(
            self.coordinator.diagnostic_runtime.watch(self.definition.key)
        )

    @property
    def native_value(self):
        state = self.coordinator.diagnostic_runtime.view(self.definition.key)["state"]
        return "not_read" if state == "unavailable" else state

    @property
    def extra_state_attributes(self):
        return self.coordinator.diagnostic_runtime.view(self.definition.key)


def diagnostic_entities(owner, entry):
    if not hasattr(owner, "diagnostic_runtime"):
        return []
    if owner.capabilities is not None and owner.capabilities.family in {
        ProductFamily.CORE,
        ProductFamily.PROVIEW,
    }:
        return []
    return [
        DiagnosticScreenSensor(owner, entry, definition)
        for definition in DIAGNOSTICS
        if definition.decoder is not None
    ]

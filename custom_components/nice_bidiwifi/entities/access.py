"""Permission inspection without exposing module user identities in entity state."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..entity import NiceCoordinatorEntity


class ModuleRoleSensor(NiceCoordinatorEntity, SensorEntity):
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = ["admin", "user", "disable", "wait", "unverified"]
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, owner, entry):
        super().__init__(
            owner,
            entry,
            platform_domain="sensor",
            unique_id_suffix="module_access_role",
            name="Module permission",
            suggested_id_suffix="Module permission",
        )
        self._attr_translation_key = "module_access_role"
        del self._attr_name

    @property
    def native_value(self):
        role = str(self.coordinator.access.role)
        return "unverified" if role == "unknown" else role

    @property
    def extra_state_attributes(self):
        return self.coordinator.access.diagnostics()


def access_entities(owner, entry):
    return [ModuleRoleSensor(owner, entry)] if hasattr(owner, "access") else []

"""Optional installer-state sensors, without new automatic procedure activity."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..entity import NiceCoordinatorEntity


class InstallerStateSensor(NiceCoordinatorEntity, SensorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "idle",
        "preparing",
        "running",
        "completed",
        "acknowledged",
        "cancelled",
        "failed",
        "interrupted",
        "needs_recovery",
        "recovery_acknowledged",
    ]

    def __init__(self, owner, entry):
        super().__init__(
            owner,
            entry,
            platform_domain="sensor",
            unique_id_suffix="installer_procedure_state",
            name="Installer procedure",
            suggested_id_suffix="Installer procedure",
        )
        self._attr_translation_key = "installer_procedure_state"
        del self._attr_name

    @property
    def native_value(self):
        record = self.coordinator.lifecycle.runner.record
        return record.state if record else "idle"

    @property
    def extra_state_attributes(self):
        runtime = self.coordinator.lifecycle
        return {
            "procedure": runtime.runner.record.export()
            if runtime.runner.record
            else None,
            "busy": runtime.runner.busy,
            "controller_programming_is_animation_calibration": False,
        }


class AdvertisedDevicesSensor(NiceCoordinatorEntity, SensorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, owner, entry):
        super().__init__(
            owner,
            entry,
            platform_domain="sensor",
            unique_id_suffix="advertised_devices",
            name="Advertised devices",
            suggested_id_suffix="Advertised devices",
        )
        self._attr_translation_key = "advertised_devices"
        del self._attr_name

    @property
    def native_value(self):
        inventory = self.coordinator.lifecycle.inventory
        return len(inventory.devices) if inventory is not None else None

    @property
    def extra_state_attributes(self):
        runtime = self.coordinator.lifecycle
        return {
            "observed_at": runtime.inventory_at,
            "scope": "NHK INFO; not a full BusT4/BlueBus discovery",
            "complete_reported_list": runtime.inventory.complete
            if runtime.inventory
            else None,
        }


def lifecycle_entities(owner, entry):
    return (
        [InstallerStateSensor(owner, entry), AdvertisedDevicesSensor(owner, entry)]
        if hasattr(owner, "lifecycle")
        else []
    )

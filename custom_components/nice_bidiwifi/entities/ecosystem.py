"""Optional read-only inspection status, with no private automation details."""

from homeassistant.components.sensor import SensorDeviceClass, SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..entity import NiceCoordinatorEntity


class NiceAutomationInspectionSensor(NiceCoordinatorEntity, SensorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.DIAGNOSTIC
    _attr_device_class = SensorDeviceClass.ENUM
    _attr_options = [
        "not_inspected",
        "records_found",
        "no_records_reported",
        "partial",
        "failed",
    ]

    def __init__(self, owner, entry):
        super().__init__(
            owner,
            entry,
            platform_domain="sensor",
            unique_id_suffix="nice_automation_inspection",
            name="Nice automation inspection",
            suggested_id_suffix="Nice automation inspection",
        )
        self._attr_translation_key = "nice_automation_inspection"
        del self._attr_name

    @property
    def native_value(self):
        runtime = self.coordinator.ecosystem
        if runtime.snapshot is not None:
            return runtime.snapshot.state
        return "failed" if runtime.last_error_type else "not_inspected"

    @property
    def extra_state_attributes(self):
        runtime = self.coordinator.ecosystem
        return {
            "inspection": runtime.snapshot.summary() if runtime.snapshot else None,
            "last_attempt_at": runtime.last_attempt_at,
            "last_error_type": runtime.last_error_type,
            "automatic_cloud_polling": False,
            "ha_resources_created": False,
            "vendor_resources_modified": False,
        }


def ecosystem_entities(owner, entry):
    return (
        [NiceAutomationInspectionSensor(owner, entry)]
        if hasattr(owner, "ecosystem")
        else []
    )

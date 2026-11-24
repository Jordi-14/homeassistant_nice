"""Thin HA adapters sharing the controller configuration model."""

from __future__ import annotations

from homeassistant.components.number import NumberEntity, NumberMode
from homeassistant.components.select import SelectEntity
from homeassistant.components.sensor import SensorEntity
from homeassistant.helpers.entity import EntityCategory

from ..configuration import MAX_INDEXED_ELEMENTS
from ..controller_catalog import PARAMETERS, parameter_for_setting
from ..entity import NiceCoordinatorEntity
from ..protocol.t4.settings import SETTINGS


class ConfigurationEntity(NiceCoordinatorEntity):
    _attr_entity_registry_enabled_default = False
    _attr_entity_registry_visible_default = False
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, entry, definition, domain, *, index=None):
        group, parameter = definition.binding
        suffix = f"parameter_{group:02x}_{parameter:02x}"
        title = definition.title
        if index is not None:
            suffix += f"_element_{index}"
            title += f" element {index}"
        super().__init__(
            coordinator,
            entry,
            platform_domain=domain,
            unique_id_suffix=suffix,
            name=title,
            suggested_id_suffix=f"{title} parameter {group:02x} {parameter:02x}",
        )
        self.definition = definition
        self.index = index

    async def async_added_to_hass(self):
        await super().async_added_to_hass()
        self.async_on_remove(self.coordinator.configuration.watch(self.definition.key))

    @property
    def view(self):
        return self.coordinator.configuration.view(
            self.definition.key, index=self.index
        )

    def _observation_attributes(self):
        observation = self.coordinator.parameters.observation_for(
            self.coordinator.configuration.address(self.definition, self.index)
        )
        if observation is None:
            return {"observed_at": None, "source": None}
        return {
            "observed_at": observation.observed_at.isoformat(),
            "source": observation.source,
        }

    @property
    def available(self):
        return self.coordinator.last_update_success and self.view.state == "available"

    @property
    def extra_state_attributes(self):
        view = self.view
        return {
            "parameter_state": view.state,
            "unavailable_reason": view.reason,
            "gap": view.gap,
            "register": "/".join(f"{part:02X}" for part in self.definition.binding),
            "raw_value": view.raw_value,
            "writable": view.writable,
            "element_index": self.index,
            "identity_basis": "unassigned_index"
            if self.index is not None
            else "controller_parameter",
            **self._observation_attributes(),
        }


class ConfigurationSensor(ConfigurationEntity, SensorEntity):
    _attr_entity_category = EntityCategory.DIAGNOSTIC

    def __init__(self, coordinator, entry, definition, *, index=None):
        super().__init__(coordinator, entry, definition, "sensor", index=index)

    @property
    def native_value(self):
        return self.view.value

    @property
    def native_unit_of_measurement(self):
        return None if self.view.options else self.view.unit


class ConfigurationSelect(ConfigurationEntity, SelectEntity):
    def __init__(self, coordinator, entry, definition):
        super().__init__(coordinator, entry, definition, "select")
        self._attr_translation_key = definition.setting.key
        self._attr_name = None

    @property
    def available(self):
        return super().available and self.view.writable and bool(self.options)

    @property
    def options(self):
        return [label for label, _ in self.view.options]

    @property
    def current_option(self):
        value = self.view.value
        return value if value in self.options else None

    async def async_select_option(self, option):
        await self.coordinator.configuration.async_write(
            self.definition.key, option, option=True
        )


class ConfigurationNumber(ConfigurationEntity, NumberEntity):
    _attr_mode = NumberMode.BOX

    def __init__(self, coordinator, entry, definition):
        super().__init__(coordinator, entry, definition, "number")
        self._attr_translation_key = "controller_parameter_value"
        self._attr_translation_placeholders = {"parameter": definition.title}
        self._attr_name = None
        # Existing bus_t4_* entities retain their identities and raw integer API.

    @property
    def available(self):
        return (
            super().available
            and self.view.writable
            and not self.view.options
            and self.native_min_value is not None
            and self.native_max_value is not None
        )

    @property
    def native_value(self):
        return self.view.value if not self.view.options else None

    @property
    def native_unit_of_measurement(self):
        return self.view.unit

    @property
    def native_min_value(self):
        return self.view.minimum

    @property
    def native_max_value(self):
        return self.view.maximum

    @property
    def native_step(self):
        return self.view.step or 1

    async def async_set_native_value(self, value):
        await self.coordinator.configuration.async_write(self.definition.key, value)


def setup_configuration_sensors(coordinator, entry, async_add_entities):
    if not hasattr(coordinator, "configuration"):
        return
    added = set()

    def add_new():
        entities = []
        for definition in PARAMETERS:
            if not definition.safe_read:
                continue
            group, parameter = definition.binding
            identity = (group, parameter, None)
            if identity not in added:
                entities.append(ConfigurationSensor(coordinator, entry, definition))
                added.add(identity)
            info = coordinator.parameters.info_for(
                coordinator.configuration.address(definition)
            )
            if (
                info
                and info.indexed
                and info.array_size is not None
                and 0 < info.array_size <= MAX_INDEXED_ELEMENTS
            ):
                for index in range(info.array_size):
                    identity = (group, parameter, index)
                    if identity not in added:
                        entities.append(
                            ConfigurationSensor(
                                coordinator, entry, definition, index=index
                            )
                        )
                        added.add(identity)
        if entities:
            async_add_entities(entities)

    add_new()
    entry.async_on_unload(coordinator.async_add_listener(add_new))


def configuration_controls(coordinator, entry, kind):
    if not hasattr(coordinator, "configuration"):
        return []
    # Additional model-specific writes stay read-only until menu permissions,
    # identities and encodings are verified. These aliases use reviewed setters.
    if kind == "select":
        keys = ("photo_close_mode", "always_close_mode")
        return [
            ConfigurationSelect(coordinator, entry, parameter_for_setting(key))
            for key in keys
        ]
    return [
        ConfigurationNumber(coordinator, entry, parameter_for_setting(setting.key))
        for setting in SETTINGS.values()
        if not setting.requires_menu_permission
        and setting.kind != "boolean"
        and setting.key not in {"photo_close_mode", "always_close_mode"}
    ]

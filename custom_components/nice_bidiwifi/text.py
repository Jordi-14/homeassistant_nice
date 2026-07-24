"""Text platform for guarded shared Wi-Fi settings."""

from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.text import (
    DOMAIN as TEXT_DOMAIN,
    TextEntity,
    TextEntityDescription,
    TextMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .coordinator import NiceBidiDataUpdateCoordinator
from .entities.factory import (
    NiceEntityDescriptionMixin,
    build_described_entities,
)
from .entity import NiceCoordinatorEntity
from .protocol.nhk.administration import MAX_INTERFACE_NAME_LENGTH
from .runtime import get_coordinator


@dataclass(frozen=True, kw_only=True)
class NiceBidiTextEntityDescription(
    NiceEntityDescriptionMixin,
    TextEntityDescription,
):
    """Description for a writable shared Wi-Fi text setting."""


def _interface_name_supported(
    coordinator: NiceBidiDataUpdateCoordinator,
) -> bool:
    checker = getattr(coordinator, "administration_capability", None)
    if checker is not None:
        return checker("interface_name_write")
    capabilities = getattr(coordinator, "capabilities", None)
    return bool(
        capabilities
        and capabilities.interface_name_write is True
    )


TEXTS: tuple[NiceBidiTextEntityDescription, ...] = (
    NiceBidiTextEntityDescription(
        key="interface_name",
        name="Interface name",
        protected=False,
        supported_fn=_interface_name_supported,
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:rename-outline",
        mode=TextMode.TEXT,
        native_min=1,
        native_max=MAX_INTERFACE_NAME_LENGTH,
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up shared Wi-Fi text settings."""
    coordinator = get_coordinator(entry)
    async_add_entities(
        build_described_entities(
            coordinator,
            entry,
            TEXTS,
            NiceBidiText,
        )
    )


class NiceBidiText(NiceCoordinatorEntity, TextEntity):
    """Guarded shared Wi-Fi text setting."""

    _attr_has_entity_name = True

    entity_description: NiceBidiTextEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiTextEntityDescription,
    ) -> None:
        """Initialize the setting."""
        super().__init__(
            coordinator,
            entry,
            platform_domain=TEXT_DOMAIN,
            unique_id_suffix=description.key,
            name=description.name,
            suggested_id_suffix=description.name,
            description=description,
        )
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return whether INFO is current and the gate is stationary."""
        status = self.coordinator.data
        return (
            super().available
            and self.coordinator.administration_capability(
                "interface_name_write"
            )
            and not (status is not None and status.is_moving)
        )

    @property
    def native_value(self) -> str:
        """Return the current INFO interface name."""
        info = self.coordinator.device_info
        return info.interface_name if info and info.interface_name else ""

    async def async_set_value(self, value: str) -> None:
        """Set and verify the interface name."""
        await self.coordinator.async_update_interface_name(value)

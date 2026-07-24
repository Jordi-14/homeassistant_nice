"""Button platform for Nice."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from functools import partial

from homeassistant.components.button import DOMAIN as BUTTON_DOMAIN, ButtonEntity, ButtonEntityDescription
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .client import (
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_COURTESY_LIGHT_TIMER,
    DEP_ACTION_LOCK,
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    DEP_ACTION_UNLOCK,
)
from .coordinator import NiceBidiDataUpdateCoordinator
from .entities.factory import (
    NiceEntityDescriptionMixin,
    build_described_entities,
)
from .entity import NiceCoordinatorEntity
from .protocol.t4.actions import T4_ACTIONS
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


@dataclass(frozen=True, kw_only=True)
class NiceBidiButtonEntityDescription(
    NiceEntityDescriptionMixin,
    ButtonEntityDescription,
):
    """Description for a Nice button."""

    press_fn: Callable[[NiceBidiDataUpdateCoordinator], Awaitable[None]]
    available_when_offline: bool = False
    t4_action_key: str | None = None


def _partial_open_position_known(
    coordinator: NiceBidiDataUpdateCoordinator,
    index: int,
) -> bool:
    """Return whether an optional partial-open slot is configured."""
    status = coordinator.data
    return status is not None and getattr(status, f"partial_open_{index}_position") is not None


def _t4_action_supported(
    coordinator: NiceBidiDataUpdateCoordinator,
    action_key: str,
) -> bool:
    """Return whether a reviewed T4 action can be used on this device."""
    return coordinator.t4_action_supported(action_key)


def _partial_open_supported(
    coordinator: NiceBidiDataUpdateCoordinator,
    action_key: str,
    index: int,
) -> bool:
    """Require both an allowed T4 action and a configured partial-open slot."""
    return (
        coordinator.t4_action_supported(action_key)
        and _partial_open_position_known(coordinator, index)
    )


async def _async_press_t4_action(
    coordinator: NiceBidiDataUpdateCoordinator,
    action_key: str,
) -> None:
    """Send one reviewed T4 action through the coordinator."""
    await coordinator.async_send_dep_action(action_key)


COMPATIBILITY_BUTTONS: tuple[NiceBidiButtonEntityDescription, ...] = (
    NiceBidiButtonEntityDescription(
        key="partial_open_1",
        name="Partial open 1",
        icon="mdi:gate-open",
        t4_action_key=DEP_ACTION_PARTIAL_OPEN_1,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_PARTIAL_OPEN_1,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_PARTIAL_OPEN_1,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="partial_open_2",
        name="Partial open 2",
        icon="mdi:gate-open",
        t4_action_key=DEP_ACTION_PARTIAL_OPEN_2,
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_PARTIAL_OPEN_2,
        ),
        supported_fn=partial(
            _partial_open_supported,
            action_key=DEP_ACTION_PARTIAL_OPEN_2,
            index=2,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="partial_open_3",
        name="Partial open 3",
        icon="mdi:gate-open",
        t4_action_key=DEP_ACTION_PARTIAL_OPEN_3,
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_PARTIAL_OPEN_3,
        ),
        supported_fn=partial(
            _partial_open_supported,
            action_key=DEP_ACTION_PARTIAL_OPEN_3,
            index=3,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="step_step",
        name="Step-step",
        icon="mdi:gesture-tap-button",
        t4_action_key=DEP_ACTION_STEP_STEP,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_STEP_STEP,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_STEP_STEP,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="courtesy_light",
        name="Courtesy light",
        entity_registry_visible_default=False,
        icon="mdi:lightbulb",
        t4_action_key=DEP_ACTION_COURTESY_LIGHT,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_COURTESY_LIGHT,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_COURTESY_LIGHT,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="courtesy_light_timer",
        name="Courtesy light timer",
        entity_registry_visible_default=False,
        icon="mdi:timer-outline",
        t4_action_key=DEP_ACTION_COURTESY_LIGHT_TIMER,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_COURTESY_LIGHT_TIMER,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_COURTESY_LIGHT_TIMER,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="lock",
        name="Lock",
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:lock",
        t4_action_key=DEP_ACTION_LOCK,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_LOCK,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_LOCK,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="unlock",
        name="Unlock",
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:lock-open-variant",
        t4_action_key=DEP_ACTION_UNLOCK,
        supported_fn=partial(
            _t4_action_supported,
            action_key=DEP_ACTION_UNLOCK,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=DEP_ACTION_UNLOCK,
        ),
    ),
    NiceBidiButtonEntityDescription(
        key="refresh_status",
        name="Refresh status",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:refresh",
        press_fn=lambda coordinator: coordinator.async_request_refresh(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="reconnect",
        name="Reconnect",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_visible_default=False,
        icon="mdi:connection",
        press_fn=lambda coordinator: coordinator.async_reconnect(),
        available_when_offline=True,
    ),
    NiceBidiButtonEntityDescription(
        key="calibrate_positions",
        name="Calibrate positions",
        entity_category=EntityCategory.DIAGNOSTIC,
        entity_registry_enabled_default=False,
        entity_registry_visible_default=False,
        icon="mdi:map-marker-path",
        press_fn=lambda coordinator: coordinator.async_start_position_calibration(),
    ),
)

ADVANCED_T4_BUTTONS = tuple(
    NiceBidiButtonEntityDescription(
        key=action.key,
        name=action.name,
        entity_registry_enabled_default=action.enabled_by_default,
        entity_registry_visible_default=False,
        icon="mdi:gesture-tap-button",
        protected=False,
        t4_action_key=action.key,
        supported_fn=partial(
            _t4_action_supported,
            action_key=action.key,
        ),
        press_fn=partial(
            _async_press_t4_action,
            action_key=action.key,
        ),
    )
    for action in T4_ACTIONS
    if not action.compatibility_entity
)

BUTTONS = (*COMPATIBILITY_BUTTONS, *ADVANCED_T4_BUTTONS)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up buttons from a config entry."""
    coordinator = get_coordinator(entry)
    async_add_entities(
        build_described_entities(
            coordinator,
            entry,
            BUTTONS,
            NiceBidiButton,
        )
    )


class NiceBidiButton(NiceCoordinatorEntity, ButtonEntity):
    """Nice diagnostic button."""

    _attr_has_entity_name = True

    entity_description: NiceBidiButtonEntityDescription

    def __init__(
        self,
        coordinator: NiceBidiDataUpdateCoordinator,
        entry: ConfigEntry,
        description: NiceBidiButtonEntityDescription,
    ) -> None:
        """Initialize the button."""
        super().__init__(
            coordinator,
            entry,
            platform_domain=BUTTON_DOMAIN,
            unique_id_suffix=description.key,
            name=description.name,
            suggested_id_suffix=description.name,
            description=description,
        )
        self.entity_description = description

    @property
    def available(self) -> bool:
        """Return if the diagnostic button can currently be pressed."""
        if self.entity_description.available_when_offline:
            return True
        supported_fn = self.entity_description.supported_fn
        return super().available and (supported_fn is None or supported_fn(self.coordinator))

    async def async_press(self) -> None:
        """Handle the button press."""
        supported_fn = self.entity_description.supported_fn
        if supported_fn is not None and not supported_fn(self.coordinator):
            raise HomeAssistantError(
                f"Nice action {self.entity_description.key!r} is not "
                "advertised by this device"
            )
        await self.entity_description.press_fn(self.coordinator)

"""Capability-aware entity description and construction helpers."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol

from homeassistant.config_entries import ConfigEntry

from ..coordinator import NiceBidiDataUpdateCoordinator


class NiceCapabilityKey(StrEnum):
    """Normalized capability requirements used by entities."""

    NONE = "none"
    OPEN_CLOSE = "open_close"
    STATUS = "status"
    POSITION = "position"
    DMP = "dmp"
    OXI = "oxi"


class EntitySupport(StrEnum):
    """Tri-state entity support result."""

    SUPPORTED = "supported"
    UNSUPPORTED = "unsupported"
    UNKNOWN = "unknown"


SupportPredicate = Callable[[NiceBidiDataUpdateCoordinator], bool | None]


@dataclass(frozen=True, kw_only=True)
class NiceEntityDescriptionMixin:
    """Capability metadata shared by all Nice entity descriptions."""

    required_capability: NiceCapabilityKey = NiceCapabilityKey.NONE
    supported_fn: SupportPredicate | None = None
    protected: bool = True


@dataclass(frozen=True, kw_only=True)
class NiceCoreEntityDescription(NiceEntityDescriptionMixin):
    """Description for a non-parameterized core entity."""

    key: str


class NiceDescription(Protocol):
    """Description fields required by the central factory."""

    key: str
    required_capability: NiceCapabilityKey
    supported_fn: SupportPredicate | None
    protected: bool


def entity_support(
    coordinator: NiceBidiDataUpdateCoordinator,
    description: NiceDescription,
) -> EntitySupport:
    """Return normalized support for one entity description."""
    if description.supported_fn is not None:
        supported = description.supported_fn(coordinator)
        if supported is not None:
            return (
                EntitySupport.SUPPORTED
                if supported
                else EntitySupport.UNSUPPORTED
            )

    capability = description.required_capability
    capabilities = getattr(coordinator, "capabilities", None)
    status = coordinator.data
    if capability is NiceCapabilityKey.NONE:
        return EntitySupport.UNKNOWN
    if capability is NiceCapabilityKey.OPEN_CLOSE:
        if capabilities is None:
            return EntitySupport.UNKNOWN
        if capabilities.high_level_actions is None:
            return EntitySupport.UNKNOWN
        return (
            EntitySupport.SUPPORTED
            if capabilities.high_level_actions
            else EntitySupport.UNSUPPORTED
        )
    if capability is NiceCapabilityKey.STATUS:
        if capabilities is None:
            return EntitySupport.UNKNOWN
        if capabilities.readable_status is None:
            return EntitySupport.UNKNOWN
        return (
            EntitySupport.SUPPORTED
            if capabilities.readable_status
            else EntitySupport.UNKNOWN
        )
    if capability is NiceCapabilityKey.POSITION:
        if status is None:
            return EntitySupport.UNKNOWN
        if coordinator.display_position is not None:
            return EntitySupport.SUPPORTED
        return EntitySupport.UNKNOWN
    if capability is NiceCapabilityKey.DMP:
        if capabilities is None or not capabilities.observed_dmp_registers:
            return EntitySupport.UNKNOWN
        return EntitySupport.SUPPORTED
    if capability is NiceCapabilityKey.OXI:
        if status is None or status.oxi_detected is None:
            return EntitySupport.UNKNOWN
        return (
            EntitySupport.SUPPORTED
            if status.oxi_detected
            else EntitySupport.UNSUPPORTED
        )
    return EntitySupport.UNKNOWN


def build_described_entities[DescriptionT: NiceDescription, EntityT](
    coordinator: NiceBidiDataUpdateCoordinator,
    entry: ConfigEntry,
    descriptions: Iterable[DescriptionT],
    builder: Callable[
        [NiceBidiDataUpdateCoordinator, ConfigEntry, DescriptionT],
        EntityT,
    ],
) -> list[EntityT]:
    """Build entities while preserving the protected compatibility catalog."""
    entities: list[EntityT] = []
    for description in descriptions:
        support = entity_support(coordinator, description)
        if (
            support is EntitySupport.UNSUPPORTED
            and not description.protected
        ):
            continue
        entities.append(builder(coordinator, entry, description))
    return entities


def description_defaults(description: Any) -> tuple[bool, bool]:
    """Return registry enabled and visible defaults from a HA description."""
    return (
        bool(description.entity_registry_enabled_default),
        bool(description.entity_registry_visible_default),
    )

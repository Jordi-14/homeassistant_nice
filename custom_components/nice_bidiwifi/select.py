"""Human-readable controller choices without replacing legacy number entities."""

from __future__ import annotations

from .entities.configuration import setup_configuration_controls
from .runtime import get_coordinator

PARALLEL_UPDATES = 1


async def async_setup_entry(hass, entry, async_add_entities):
    setup_configuration_controls(get_coordinator(entry), entry, "select", async_add_entities)

"""Integration setup tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.nice_bidiwifi as nice_init
from custom_components.nice_bidiwifi import (
    PLATFORMS,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_METHOD,
    CONNECTION_METHOD_CLOUD,
    DOMAIN,
)
from tests.conftest import config_entry_data


class FakeCoordinator:
    """Coordinator fake for setup tests."""

    instances: list[FakeCoordinator] = []

    def __init__(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.loaded = False
        self.refreshed = False
        self.shutdown = False
        FakeCoordinator.instances.append(self)

    async def async_load_calibration(self) -> None:
        """Record calibration loading."""
        self.loaded = True

    async def async_config_entry_first_refresh(self) -> None:
        """Record first refresh."""
        self.refreshed = True

    async def async_shutdown(self) -> None:
        """Record shutdown."""
        self.shutdown = True


async def test_setup_entry_loads_coordinator_and_forwards_platforms(
    hass: HomeAssistant,
) -> None:
    """Test setting up an entry."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="entry-1")
    entry.add_to_hass(hass)
    FakeCoordinator.instances = []

    with (
        patch.object(nice_init, "NiceBidiDataUpdateCoordinator", FakeCoordinator),
        patch.object(hass.config_entries, "async_forward_entry_setups", new_callable=AsyncMock) as mock_forward,
    ):
        result = await async_setup_entry(hass, entry)

    assert result is True
    assert entry.runtime_data is FakeCoordinator.instances[0]
    assert FakeCoordinator.instances[0].loaded is True
    assert FakeCoordinator.instances[0].refreshed is True
    mock_forward.assert_called_once_with(entry, PLATFORMS)


async def test_setup_entry_dispatches_cloud_entries(hass: HomeAssistant) -> None:
    """Test cloud entries use the cloud setup path."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD},
        entry_id="entry-1",
    )
    entry.add_to_hass(hass)

    with patch.object(nice_init, "async_setup_cloud_entry", new_callable=AsyncMock) as mock_setup:
        mock_setup.return_value = True
        result = await async_setup_entry(hass, entry)

    assert result is True
    mock_setup.assert_awaited_once_with(hass, entry)


async def test_unload_entry_unloads_platforms_and_shuts_down_coordinator(
    hass: HomeAssistant,
) -> None:
    """Test unloading an entry."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="entry-1")
    coordinator = FakeCoordinator(hass, entry)
    entry.runtime_data = coordinator

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=True,
    ) as mock_unload:
        result = await async_unload_entry(hass, entry)

    assert result is True
    assert coordinator.shutdown is True
    assert entry.runtime_data is None
    mock_unload.assert_called_once_with(entry, PLATFORMS)


async def test_unload_entry_dispatches_cloud_entries(hass: HomeAssistant) -> None:
    """Test cloud entries use the cloud unload path."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD},
        entry_id="entry-1",
    )

    with patch.object(nice_init, "async_unload_cloud_entry", new_callable=AsyncMock) as mock_unload:
        mock_unload.return_value = True
        result = await async_unload_entry(hass, entry)

    assert result is True
    mock_unload.assert_awaited_once_with(hass, entry)


async def test_unload_entry_preserves_runtime_data_when_platform_unload_fails(
    hass: HomeAssistant,
) -> None:
    """Test failed platform unload leaves runtime data intact."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data(), entry_id="entry-1")
    coordinator = FakeCoordinator(hass, entry)
    entry.runtime_data = coordinator

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        new_callable=AsyncMock,
        return_value=False,
    ) as mock_unload:
        result = await async_unload_entry(hass, entry)

    assert result is False
    assert coordinator.shutdown is False
    assert entry.runtime_data is coordinator
    mock_unload.assert_called_once_with(entry, PLATFORMS)

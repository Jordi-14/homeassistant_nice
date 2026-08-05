"""Integration setup tests."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

import custom_components.nice_bidiwifi as nice_init
from custom_components.nice_bidiwifi import (
    PLATFORMS,
    async_migrate_entry,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_MODE,
    CONF_TARGET_MAC,
    CONFIG_ENTRY_VERSION,
    DOMAIN,
)
from custom_components.nice_bidiwifi.models.config import (
    ConnectionMode,
    NiceEntryConfig,
)
from custom_components.nice_bidiwifi.runtime import NiceRuntimeData
from tests.conftest import config_entry_data


class FakeCoordinator:
    """Coordinator fake for setup tests."""

    instances: list[FakeCoordinator] = []

    def __init__(self, hass: HomeAssistant, entry: MockConfigEntry) -> None:
        self.hass = hass
        self.entry = entry
        self.entry_config = NiceEntryConfig.from_mapping(entry.data, title=entry.title)
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
    assert isinstance(entry.runtime_data, NiceRuntimeData)
    assert entry.runtime_data.coordinator is FakeCoordinator.instances[0]
    assert entry.runtime_data.config is FakeCoordinator.instances[0].entry_config
    assert FakeCoordinator.instances[0].loaded is True
    assert FakeCoordinator.instances[0].refreshed is True
    mock_forward.assert_called_once_with(entry, PLATFORMS)


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


async def test_migrate_legacy_entry_to_explicit_local_only_without_entity_churn(
    hass: HomeAssistant,
) -> None:
    """Test legacy entries gain policy and identity without changing entities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(
            **{CONF_TARGET_MAC: "aa-bb-cc-dd-ee-ff"}
        ),
        entry_id="entry-1",
        unique_id=None,
        version=1,
    )
    entry.add_to_hass(hass)
    entity_registry = er.async_get(hass)
    entity = entity_registry.async_get_or_create(
        "cover",
        DOMAIN,
        "aabbccddeeff_1_cover",
        config_entry=entry,
        suggested_object_id="parking_gate",
    )

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.version == CONFIG_ENTRY_VERSION
    assert (
        entry.data[CONF_CONNECTION_MODE]
        == ConnectionMode.LOCAL_ONLY.value
    )
    assert entry.data[CONF_TARGET_MAC] == "aa-bb-cc-dd-ee-ff"
    assert entry.unique_id == "AA:BB:CC:DD:EE:FF"
    migrated_entity = entity_registry.async_get(entity.entity_id)
    assert migrated_entity is not None
    assert migrated_entity.entity_id == entity.entity_id
    assert migrated_entity.unique_id == entity.unique_id
    config = NiceEntryConfig.from_mapping(entry.data)
    assert config.connection.mode is ConnectionMode.LOCAL_ONLY
    assert config.connection.relay is None


async def test_migrate_current_entry_is_noop(
    hass: HomeAssistant,
) -> None:
    """Test current config entries are accepted without rewriting data."""
    original_data = config_entry_data(
        **{CONF_CONNECTION_MODE: ConnectionMode.LOCAL_ONLY.value}
    )
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=original_data,
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
        version=CONFIG_ENTRY_VERSION,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is True
    assert entry.data == original_data
    assert entry.unique_id == "AA:BB:CC:DD:EE:FF"


async def test_migrate_newer_entry_is_rejected(
    hass: HomeAssistant,
) -> None:
    """Test an entry from a newer integration is not downgraded."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        version=CONFIG_ENTRY_VERSION + 1,
    )
    entry.add_to_hass(hass)

    result = await async_migrate_entry(hass, entry)

    assert result is False
    assert entry.version == CONFIG_ENTRY_VERSION + 1
    assert CONF_CONNECTION_MODE not in entry.data

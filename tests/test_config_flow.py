"""Config flow tests."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, patch

from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import selector
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import config_flow
from custom_components.nice_bidiwifi.client import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
)
from custom_components.nice_bidiwifi.const import (
    CONF_DEVICE_ID,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DOMAIN,
)
from tests.conftest import config_entry_data


class FakeClient:
    """Client fake for config flow validation."""

    connect_error: Exception | None = None
    instances: list[FakeClient] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeClient.instances.append(self)

    def test_connection(self) -> None:
        """Validate connection or raise a configured error."""
        if FakeClient.connect_error is not None:
            raise FakeClient.connect_error

    def close(self) -> None:
        """Record that the client was closed."""
        self.closed = True


def _input(**overrides: Any) -> dict[str, Any]:
    data: dict[str, Any] = {
        CONF_NAME: " Gate ",
        CONF_HOST: " 192.0.2.10 ",
        CONF_PORT: 443,
        CONF_TARGET_MAC: " aa:bb:cc:dd:ee:ff ",
        CONF_USERNAME: " user ",
        CONF_PASSWORD: "aa" * 32,
        CONF_SOURCE_ID: " source ",
        CONF_DEVICE_ID: 1,
        CONF_T4_TIMEOUT_MS: 200,
    }
    data.update(overrides)
    return data


def setup_function() -> None:
    """Reset fake client state."""
    FakeClient.connect_error = None
    FakeClient.instances = []


async def test_user_step_success_creates_entry(hass: HomeAssistant) -> None:
    """Test a successful config flow."""
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gate"
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"
    assert result["data"][CONF_PASSWORD] == "AA" * 32
    assert FakeClient.instances[0].kwargs["host"] == "192.0.2.10"
    assert FakeClient.instances[0].kwargs["port"] == 443
    assert FakeClient.instances[0].closed is True


async def test_user_step_auth_error_returns_form(hass: HomeAssistant) -> None:
    """Test auth failure handling."""
    FakeClient.connect_error = NiceBidiAuthError("bad credentials")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"
    assert FakeClient.instances[0].closed is True


async def test_user_step_connection_error_returns_form(hass: HomeAssistant) -> None:
    """Test connection failure handling."""
    FakeClient.connect_error = NiceBidiConnectionError("offline")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"
    assert FakeClient.instances[0].closed is True


async def test_reauth_success_updates_entry_and_reloads(hass: HomeAssistant) -> None:
    """Test a successful reauthentication flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as mock_reload,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _input(**{CONF_HOST: " 192.0.2.11 "}),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_HOST] == "192.0.2.11"
    mock_reload.assert_called_once_with(entry.entry_id)


async def test_reauth_wrong_device_returns_form(hass: HomeAssistant) -> None:
    """Test reauth rejects a different MAC address."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reauth", "entry_id": entry.entry_id},
            data=entry.data,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _input(**{CONF_TARGET_MAC: "11:22:33:44:55:66"}),
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "wrong_device"


async def test_reconfigure_success_updates_entry_and_reloads(hass: HomeAssistant) -> None:
    """Test a successful reconfiguration flow."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as mock_reload,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _input(**{CONF_PORT: 8443}),
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_PORT] == 8443
    mock_reload.assert_called_once_with(entry.entry_id)


async def test_reconfigure_connection_error_returns_form(hass: HomeAssistant) -> None:
    """Test reconfiguration connection failure handling."""
    FakeClient.connect_error = NiceBidiConnectionError("offline")
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


def test_schema_uses_home_assistant_selectors() -> None:
    """Test config flow schema uses selectors."""
    schema = {
        key.schema: value
        for key, value in config_flow._schema().schema.items()
    }

    assert isinstance(schema[CONF_HOST], selector.TextSelector)
    assert isinstance(schema[CONF_PASSWORD], selector.TextSelector)
    assert isinstance(schema[CONF_PORT], selector.NumberSelector)
    assert isinstance(schema[CONF_T4_TIMEOUT_MS], selector.NumberSelector)


def test_normalize_input_strips_text_and_uppercases_binary_fields() -> None:
    """Test input normalization."""
    normalized = config_flow._normalize_input(_input())

    assert normalized[CONF_NAME] == "Gate"
    assert normalized[CONF_HOST] == "192.0.2.10"
    assert normalized[CONF_PORT] == 443
    assert normalized[CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"
    assert normalized[CONF_PASSWORD] == "AA" * 32
    assert normalized[CONF_SOURCE_ID] == "source"
    assert normalized[CONF_DEVICE_ID] == 1
    assert normalized[CONF_T4_TIMEOUT_MS] == 200

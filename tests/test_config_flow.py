"""Config flow tests."""

from __future__ import annotations

import logging
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
    CONF_CLOUD_TOKEN,
    CONF_CONNECTION_METHOD,
    CONF_DEVICE_ID,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    CONNECTION_METHOD_CLOUD,
    CONNECTION_METHOD_LOCAL,
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


async def _start_local_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONNECTION_METHOD: CONNECTION_METHOD_LOCAL},
    )


async def test_user_step_success_creates_entry(hass: HomeAssistant) -> None:
    """Test a successful config flow."""
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gate"
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"
    assert result["data"][CONF_PASSWORD] == "AA" * 32
    assert FakeClient.instances[0].kwargs["host"] == "192.0.2.10"
    assert FakeClient.instances[0].kwargs["port"] == 443
    assert FakeClient.instances[0].closed is True


async def test_user_step_defaults_to_local_method(hass: HomeAssistant) -> None:
    """Test the first setup step recommends local control."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    schema = {key.schema: value for key, value in result["data_schema"].schema.items()}
    assert isinstance(schema[CONF_CONNECTION_METHOD], selector.SelectSelector)
    assert schema[CONF_CONNECTION_METHOD].config["options"] == [
        CONNECTION_METHOD_LOCAL,
        CONNECTION_METHOD_CLOUD,
    ]
    assert schema[CONF_CONNECTION_METHOD].config["translation_key"] == CONF_CONNECTION_METHOD


async def test_user_step_local_method_routes_to_local_form(hass: HomeAssistant) -> None:
    """Test choosing local setup opens the local credential form."""
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONNECTION_METHOD: CONNECTION_METHOD_LOCAL},
    )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "local"


async def test_user_step_cloud_success_creates_cloud_entry(hass: HomeAssistant) -> None:
    """Test a successful cloud config flow."""
    cloud_data = {
        CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "secret",
        CONF_CLOUD_TOKEN: {"access_token": "token"},
    }

    with (
        patch.object(config_flow, "_async_validate_cloud_input", new_callable=AsyncMock) as validate,
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        validate.return_value = cloud_data
        result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": "user"})
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_USERNAME: " user@example.com ", CONF_PASSWORD: " secret "},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "user@example.com"
    assert result["data"] == cloud_data
    validate.assert_awaited_once()


async def test_user_step_auth_error_returns_form(hass: HomeAssistant) -> None:
    """Test auth failure handling."""
    FakeClient.connect_error = NiceBidiAuthError("bad credentials")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"
    assert FakeClient.instances[0].closed is True


async def test_user_step_connection_error_returns_form(hass: HomeAssistant) -> None:
    """Test connection failure handling."""
    FakeClient.connect_error = NiceBidiConnectionError("offline")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(result["flow_id"], _input())

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"
    assert FakeClient.instances[0].closed is True


async def test_user_step_connection_error_logs_sanitized_details(hass: HomeAssistant, caplog) -> None:
    """Test setup validation failures are logged without extracted credentials."""
    password = "AB" * 32
    FakeClient.connect_error = NiceBidiConnectionError(
        f"offline for nhk_login source-123 AA:BB:CC:DD:EE:FF {password}"
    )
    caplog.set_level(logging.WARNING, logger=config_flow.__name__)

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _input(
                **{
                    CONF_USERNAME: "nhk_login",
                    CONF_SOURCE_ID: "source-123",
                    CONF_PASSWORD: password,
                }
            ),
        )

    assert result["type"] == FlowResultType.FORM
    assert "Nice setup validation failed at user" in caplog.text
    assert "NiceBidiConnectionError" in caplog.text
    assert "192.0.2.10:443" in caplog.text
    assert "nhk_login" not in caplog.text
    assert "source-123" not in caplog.text
    assert "AA:BB:CC:DD:EE:FF" not in caplog.text
    assert password not in caplog.text


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


async def test_cloud_reconfigure_keeps_existing_username(hass: HomeAssistant) -> None:
    """Test cloud reconfiguration cannot change config entry identity."""
    entry_data = {
        CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "old-secret",
        CONF_CLOUD_TOKEN: {"access_token": "old-token"},
    }
    updated_data = {
        CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
        CONF_USERNAME: "user@example.com",
        CONF_PASSWORD: "new-secret",
        CONF_CLOUD_TOKEN: {"access_token": "new-token"},
    }
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=entry_data,
        entry_id="cloud-entry",
        title="user@example.com",
        unique_id="cloud:user@example.com",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(config_flow, "_async_validate_cloud_input", new_callable=AsyncMock) as validate,
        patch.object(hass.config_entries, "async_reload", new_callable=AsyncMock) as mock_reload,
    ):
        validate.return_value = updated_data
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )

        schema = {key.schema: value for key, value in result["data_schema"].schema.items()}
        assert CONF_PASSWORD in schema
        assert CONF_USERNAME not in schema

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_PASSWORD: " new-secret "},
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    validate.assert_awaited_once_with(
        hass,
        {
            CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
            CONF_USERNAME: "user@example.com",
            CONF_PASSWORD: "new-secret",
        },
    )
    assert entry.title == "user@example.com"
    assert entry.unique_id == "cloud:user@example.com"
    assert entry.data == updated_data
    mock_reload.assert_called_once_with(entry.entry_id)


def test_schema_uses_home_assistant_selectors() -> None:
    """Test config flow schema uses selectors."""
    schema = {
        key.schema: value
        for key, value in config_flow._schema().schema.items()
    }

    method_schema = {
        key.schema: value
        for key, value in config_flow._method_schema().schema.items()
    }

    assert isinstance(method_schema[CONF_CONNECTION_METHOD], selector.SelectSelector)
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

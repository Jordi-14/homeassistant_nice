"""Config flow tests."""

from __future__ import annotations

import logging
from ipaddress import ip_address
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
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
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi import config_flow
from custom_components.nice_bidiwifi.client import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
)
from custom_components.nice_bidiwifi.cloud.models import (
    NiceCloudAccessory,
    NiceCloudBootstrapResult,
)
from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_DISCOVERY_ADDRESSES,
    CONF_DISCOVERY_MODEL,
    CONF_DISCOVERY_PROTOCOL,
    CONF_DISCOVERY_STATUS_FLAG,
    CONF_LEGACY_LOCAL_TLS,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DOMAIN,
)
from custom_components.nice_bidiwifi.errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudConnectionError,
    NiceCloudSchemaError,
    NiceProtocolError,
    NiceUnsupportedError,
)
from custom_components.nice_bidiwifi.models.config import ConnectionMode
from tests.conftest import config_entry_data


class FakeClient:
    """Client fake for config flow validation."""

    connect_error: Exception | None = None
    route_errors: dict[str, Exception] = {}
    instances: list[FakeClient] = []

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.closed = False
        FakeClient.instances.append(self)

    def test_connection(self) -> None:
        """Validate connection or raise a configured error."""
        route = str(self.kwargs.get("route_name", "local"))
        if route in FakeClient.route_errors:
            raise FakeClient.route_errors[route]
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


def _local_input(**overrides: Any) -> dict[str, Any]:
    data = _input()
    for key in (
        CONF_PORT,
        CONF_SOURCE_ID,
        CONF_DEVICE_ID,
        CONF_T4_TIMEOUT_MS,
    ):
        data.pop(key)
    data.update(overrides)
    return data


def _zeroconf_info(
    *,
    address: str = "192.0.2.20",
    addresses: tuple[str, ...] | None = None,
    port: int | None = 443,
    service_type: str = "_hap._tcp.local.",
    name: str = "Driveway._hap._tcp.local.",
    hostname: str = "driveway.local.",
    properties: dict[str, Any] | None = None,
) -> ZeroconfServiceInfo:
    parsed_addresses = tuple(
        ip_address(item) for item in (addresses or (address,))
    )
    return ZeroconfServiceInfo(
        ip_address=ip_address(address),
        ip_addresses=list(parsed_addresses),
        port=port,
        hostname=hostname,
        type=service_type,
        name=name,
        properties=(
            properties
            if properties is not None
            else {
                "deviceid": "AA:BB:CC:DD:EE:FF",
                "model": "Nice - BIDIWIFI - HW1",
                "protovers": "1.0",
                "sf": "0",
            }
        ),
    )


async def _start_local_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {CONF_CONNECTION_MODE: ConnectionMode.LOCAL_ONLY.value},
    )


def _cloud_result() -> NiceCloudBootstrapResult:
    return NiceCloudBootstrapResult(
        accessories=(
            NiceCloudAccessory(
                name="Driveway",
                target_mac="AA:BB:CC:DD:EE:FF",
                username="cloud-user-1",
                password="AA" * 32,
                source_id="controller-1",
            ),
            NiceCloudAccessory(
                name="Garage",
                target_mac="11:22:33:44:55:66",
                username="cloud-user-2",
                password="BB" * 32,
                source_id="controller-2",
            ),
        ),
        skipped_records=1,
    )


async def _start_cloud_flow(hass: HomeAssistant) -> dict[str, Any]:
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )
    return await hass.config_entries.flow.async_configure(
        result["flow_id"],
        {
            CONF_CONNECTION_MODE: ConnectionMode.LOCAL_ONLY.value,
            config_flow.CONF_CREDENTIAL_SOURCE: (
                config_flow.CREDENTIAL_SOURCE_MYNICE
            ),
        },
    )


def setup_function() -> None:
    """Reset fake client state."""
    FakeClient.connect_error = None
    FakeClient.route_errors = {}
    FakeClient.instances = []


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (NiceBidiAuthError("denied"), "invalid_auth"),
        (NiceProtocolError("malformed INFO"), "invalid_protocol"),
        (NiceUnsupportedError("unknown protocol"), "unsupported_device"),
        (NiceBidiConnectionError("offline"), "cannot_connect"),
        (OSError("unreachable"), "cannot_connect"),
        (RuntimeError("unexpected"), "unknown"),
    ],
)
def test_validation_errors_are_classified(
    error: Exception,
    error_key: str,
) -> None:
    """Test setup distinguishes auth, transport, and protocol failures."""
    assert config_flow._error_from_exception(error) == error_key


async def test_user_step_success_creates_entry(hass: HomeAssistant) -> None:
    """Test a successful config flow."""
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _local_input(),
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "Gate"
    assert result["data"][CONF_HOST] == "192.0.2.10"
    assert result["data"][CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"
    assert result["data"][CONF_PASSWORD] == "AA" * 32
    assert (
        result["data"][CONF_CONNECTION_MODE]
        == ConnectionMode.LOCAL_ONLY.value
    )
    assert result["data"][CONF_PORT] == 443
    assert result["data"][CONF_DEVICE_ID] == 1
    assert result["data"][CONF_T4_TIMEOUT_MS] == 200
    assert FakeClient.instances[0].kwargs["host"] == "192.0.2.10"
    assert FakeClient.instances[0].kwargs["port"] == 443
    assert FakeClient.instances[0].closed is True


async def test_user_step_auth_error_returns_form(hass: HomeAssistant) -> None:
    """Test auth failure handling."""
    FakeClient.connect_error = NiceBidiAuthError("bad credentials")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _local_input(),
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"
    assert FakeClient.instances[0].closed is True


async def test_user_step_connection_error_returns_form(hass: HomeAssistant) -> None:
    """Test connection failure handling."""
    FakeClient.connect_error = NiceBidiConnectionError("offline")

    with patch.object(config_flow, "NiceBidiClient", FakeClient):
        result = await _start_local_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _local_input(),
        )

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
            _local_input(
                **{
                    CONF_USERNAME: "nhk_login",
                    CONF_PASSWORD: password,
                    config_flow.CONF_ADVANCED: True,
                }
            ),
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {CONF_SOURCE_ID: "source-123"},
        )

    assert result["type"] == FlowResultType.FORM
    assert "Nice setup validation failed at local_advanced" in caplog.text
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


async def test_user_starts_with_available_connection_modes(
    hass: HomeAssistant,
) -> None:
    """Test connection policy is selected before credentials are collected."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "user"},
    )
    schema = {
        key.schema: value for key, value in result["data_schema"].schema.items()
    }
    mode_selector = schema[CONF_CONNECTION_MODE]

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "user"
    assert isinstance(mode_selector, selector.SelectSelector)
    assert mode_selector.config["options"] == [
        ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK.value,
        ConnectionMode.LOCAL_ONLY.value,
        ConnectionMode.CLOUD_ONLY.value,
    ]
    assert (
        config_flow.RECOMMENDED_CONNECTION_MODE
        is ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
    )
    assert (
        config_flow.DEFAULT_NEW_CONNECTION_MODE
        is ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
    )


async def test_cloud_only_manual_setup_uses_verified_relay(
    hass: HomeAssistant,
) -> None:
    """Cloud-only setup requires no LAN address and validates the relay."""
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CONNECTION_MODE: ConnectionMode.CLOUD_ONLY.value,
                config_flow.CONF_CREDENTIAL_SOURCE: (
                    config_flow.CREDENTIAL_SOURCE_MANUAL
                ),
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Cloud Gate",
                CONF_RELAY_HOST: "relay.example",
                CONF_RELAY_PORT: 7890,
                CONF_TARGET_MAC: "AA:BB:CC:DD:EE:FF",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "AA" * 32,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION_MODE] == ConnectionMode.CLOUD_ONLY.value
    assert CONF_HOST not in result["data"]
    assert FakeClient.instances[0].kwargs["route_name"] == "cloud"


async def test_recommended_mode_accepts_temporarily_unavailable_lan(
    hass: HomeAssistant,
) -> None:
    """Fallback setup succeeds through the relay when the LAN is unavailable."""
    FakeClient.route_errors["local"] = NiceBidiConnectionError("LAN down")
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CONNECTION_MODE: (
                    ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK.value
                ),
                config_flow.CONF_CREDENTIAL_SOURCE: (
                    config_flow.CREDENTIAL_SOURCE_MANUAL
                ),
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **_local_input(),
                CONF_RELAY_HOST: "relay.example",
                CONF_RELAY_PORT: 7890,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert [client.kwargs.get("route_name", "local") for client in FakeClient.instances] == [
        "local",
        "cloud",
    ]


async def test_mynice_import_can_create_cloud_only_entry(
    hass: HomeAssistant,
) -> None:
    """One-time import feeds cloud-only setup without retaining account data."""
    with (
        patch.object(
            config_flow,
            "_async_fetch_cloud_accessories",
            AsyncMock(return_value=_cloud_result()),
        ),
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch("custom_components.nice_bidiwifi.async_setup_entry", return_value=True),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "user"},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_CONNECTION_MODE: ConnectionMode.CLOUD_ONLY.value,
                config_flow.CONF_CREDENTIAL_SOURCE: (
                    config_flow.CREDENTIAL_SOURCE_MYNICE
                ),
            },
        )
        assert {
            key.schema for key in result["data_schema"].schema
        } == {
            config_flow.CONF_CLOUD_ACCOUNT,
            config_flow.CONF_CLOUD_ACCOUNT_PASSWORD,
            config_flow.CONF_CLOUD_CONFIRM,
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "person@example.com",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "account-secret",
                config_flow.CONF_CLOUD_CONFIRM: True,
            },
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {config_flow.CONF_CLOUD_ACCESSORIES: ["0"]},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Driveway",
                CONF_RELAY_HOST: "relay.example",
                CONF_RELAY_PORT: 7890,
                CONF_DEVICE_ID: 1,
                CONF_T4_TIMEOUT_MS: 200,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_CONNECTION_MODE] == ConnectionMode.CLOUD_ONLY.value
    assert CONF_HOST not in result["data"]
    assert config_flow.CONF_CLOUD_ACCOUNT not in result["data"]
    assert FakeClient.instances[0].kwargs["route_name"] == "cloud"


async def test_reconfigure_changes_mode_without_changing_identity(
    hass: HomeAssistant,
) -> None:
    """Route policy changes preserve the config entry and entity identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="Gate",
        data=config_entry_data(),
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch.object(
            hass.config_entries,
            "async_reload",
            AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "reconfigure", "entry_id": entry.entry_id},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                **config_entry_data(),
                CONF_CONNECTION_MODE: ConnectionMode.CLOUD_ONLY.value,
                CONF_RELAY_HOST: "relay.example",
                CONF_RELAY_PORT: 7890,
            },
        )

    assert result["type"] == FlowResultType.ABORT
    assert entry.unique_id == "AA:BB:CC:DD:EE:FF"
    assert entry.data[CONF_CONNECTION_MODE] == ConnectionMode.CLOUD_ONLY.value


async def test_cloud_bootstrap_creates_multiple_local_entries_without_cloud_secrets(
    hass: HomeAssistant,
) -> None:
    """Test selected accessories become independent validated local entries."""
    observed_login: dict[str, str] = {}

    async def _fetch(_hass, login):
        observed_login.update(
            {
                "account": login.account_username,
                "account_password": login.account_password,
            }
        )
        return _cloud_result()

    with (
        patch.object(
            config_flow,
            "_async_fetch_cloud_accessories",
            side_effect=_fetch,
        ),
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await _start_cloud_flow(hass)
        assert result["step_id"] == "cloud_auth"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "account@example.test",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "account-secret",
                config_flow.CONF_CLOUD_CONFIRM: True,
            },
        )
        assert result["step_id"] == "cloud_accessories"
        assert result["description_placeholders"] == {
            "count": "2",
            "skipped": "1",
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {config_flow.CONF_CLOUD_ACCESSORIES: ["0", "1"]},
        )
        assert result["step_id"] == "cloud_local"
        assert result["description_placeholders"]["name"] == "Driveway"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Driveway",
                CONF_HOST: "192.0.2.10",
                CONF_PORT: 443,
                CONF_DEVICE_ID: 1,
                CONF_T4_TIMEOUT_MS: 200,
            },
        )
        assert result["step_id"] == "cloud_local"
        assert result["description_placeholders"]["name"] == "Garage"
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Garage",
                CONF_HOST: "192.0.2.11",
                CONF_PORT: 443,
                CONF_DEVICE_ID: 1,
                CONF_T4_TIMEOUT_MS: 200,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert observed_login == {
        "account": "account@example.test",
        "account_password": "account-secret",
    }
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 2
    assert {entry.unique_id for entry in entries} == {
        "AA:BB:CC:DD:EE:FF",
        "11:22:33:44:55:66",
    }
    forbidden = {
        config_flow.CONF_CLOUD_ACCOUNT,
        config_flow.CONF_CLOUD_ACCOUNT_PASSWORD,
        "access_token",
        "refresh_token",
    }
    assert all(forbidden.isdisjoint(entry.data) for entry in entries)
    assert all(
        entry.data[CONF_CONNECTION_MODE]
        == ConnectionMode.LOCAL_ONLY.value
        for entry in entries
    )
    assert {instance.kwargs["host"] for instance in FakeClient.instances} == {
        "192.0.2.10",
        "192.0.2.11",
    }


@pytest.mark.parametrize(
    ("error", "error_key"),
    [
        (NiceCloudAuthError(), "cloud_invalid_auth"),
        (NiceCloudAccessError(), "cloud_access_denied"),
        (NiceCloudConnectionError(), "cloud_cannot_connect"),
        (NiceCloudSchemaError(), "cloud_schema_changed"),
    ],
)
async def test_cloud_bootstrap_errors_are_clear_and_secrets_are_not_logged(
    hass: HomeAssistant,
    caplog,
    error: Exception,
    error_key: str,
) -> None:
    """Test cloud failures stay classified without logging secret values."""
    caplog.set_level(logging.WARNING, logger=config_flow.__name__)
    result = await _start_cloud_flow(hass)
    with patch.object(
        config_flow,
        "_async_fetch_cloud_accessories",
        side_effect=error,
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "private-account",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "private-password",
                config_flow.CONF_CLOUD_CONFIRM: True,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "cloud_auth"
    assert result["errors"]["base"] == error_key
    assert error.__class__.__name__ in caplog.text
    for secret in (
        "private-account",
        "private-password",
    ):
        assert secret not in caplog.text


async def test_cloud_bootstrap_requires_explicit_confirmation(
    hass: HomeAssistant,
) -> None:
    """Test account data is never sent without the privacy confirmation."""
    result = await _start_cloud_flow(hass)
    with patch.object(
        config_flow,
        "_async_fetch_cloud_accessories",
        new_callable=AsyncMock,
    ) as fetch:
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "account",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "password",
                config_flow.CONF_CLOUD_CONFIRM: False,
            },
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cloud_confirmation_required"
    fetch.assert_not_awaited()


async def test_cloud_bootstrap_omits_already_configured_accessories(
    hass: HomeAssistant,
) -> None:
    """Test the selection list contains only new stable identities."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)
    result = await _start_cloud_flow(hass)
    with (
        patch.object(
            config_flow,
            "_async_fetch_cloud_accessories",
            return_value=_cloud_result(),
        ),
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "account",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "password",
                config_flow.CONF_CLOUD_CONFIRM: True,
            },
        )

    schema = {
        key.schema: value
        for key, value in result["data_schema"].schema.items()
    }
    options = schema[config_flow.CONF_CLOUD_ACCESSORIES].config["options"]
    assert result["step_id"] == "cloud_accessories"
    assert options == [{"value": "0", "label": "Garage"}]


async def test_cloud_bootstrap_does_not_count_ignored_discovery_as_configured(
    hass: HomeAssistant,
) -> None:
    """An ignored discovery record can still be selected during manual import."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        source="ignore",
        data={},
        entry_id="ignored-entry",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)
    result = await _start_cloud_flow(hass)
    with (
        patch.object(
            config_flow,
            "_async_fetch_cloud_accessories",
            return_value=_cloud_result(),
        ),
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                config_flow.CONF_CLOUD_ACCOUNT: "account",
                config_flow.CONF_CLOUD_ACCOUNT_PASSWORD: "password",
                config_flow.CONF_CLOUD_CONFIRM: True,
            },
        )

        schema = {
            key.schema: value
            for key, value in result["data_schema"].schema.items()
        }
        options = schema[config_flow.CONF_CLOUD_ACCESSORIES].config["options"]
        assert result["step_id"] == "cloud_accessories"
        assert options == [
            {"value": "0", "label": "Driveway"},
            {"value": "1", "label": "Garage"},
        ]
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {config_flow.CONF_CLOUD_ACCESSORIES: ["0"]},
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Driveway",
                CONF_HOST: "192.0.2.10",
                CONF_PORT: 443,
                CONF_DEVICE_ID: 1,
                CONF_T4_TIMEOUT_MS: 200,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    entries = hass.config_entries.async_entries(DOMAIN, include_ignore=True)
    assert len(entries) == 1
    assert entries[0].source != "ignore"


async def test_manual_setup_separates_normal_and_advanced_fields(
    hass: HomeAssistant,
) -> None:
    """Test protocol tuning and source ID are hidden behind Advanced."""
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await _start_local_flow(hass)
        normal_fields = {
            key.schema for key in result["data_schema"].schema
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            _local_input(**{config_flow.CONF_ADVANCED: True}),
        )
        advanced_fields = {
            key.schema for key in result["data_schema"].schema
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_SOURCE_ID: " controller ",
                CONF_PORT: 8443,
                CONF_DEVICE_ID: 2,
                CONF_T4_TIMEOUT_MS: 350,
                CONF_LEGACY_LOCAL_TLS: True,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_SOURCE_ID not in normal_fields
    assert CONF_PORT not in normal_fields
    assert CONF_DEVICE_ID not in normal_fields
    assert CONF_T4_TIMEOUT_MS not in normal_fields
    assert advanced_fields == {
        CONF_SOURCE_ID,
        CONF_PORT,
        CONF_DEVICE_ID,
        CONF_T4_TIMEOUT_MS,
        CONF_LEGACY_LOCAL_TLS,
    }
    assert result["data"][CONF_SOURCE_ID] == "controller"
    assert result["data"][CONF_PORT] == 8443
    assert result["data"][CONF_DEVICE_ID] == 2
    assert result["data"][CONF_T4_TIMEOUT_MS] == 350
    assert result["data"][CONF_LEGACY_LOCAL_TLS] is True
    assert (
        FakeClient.instances[0].kwargs["transport_factory"]
        == config_flow.LegacyLanTlsTransport.connect
    )


async def test_zeroconf_discovery_creates_local_entry(
    hass: HomeAssistant,
) -> None:
    """Test operational discovery hides network identity from confirmation."""
    discovery = _zeroconf_info(
        addresses=("192.0.2.20", "2001:db8::20")
    )
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "zeroconf"},
            data=discovery,
        )
        form_fields = {
            key.schema for key in result["data_schema"].schema
        }
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Driveway",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "aa" * 32,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert CONF_HOST not in form_fields
    assert CONF_TARGET_MAC not in form_fields
    assert result["data"][CONF_HOST] == "192.0.2.20"
    assert result["data"][CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"
    assert result["data"][CONF_CONNECTION_MODE] == "local_only"
    assert result["data"][CONF_DISCOVERY_ADDRESSES] == [
        "192.0.2.20",
        "2001:db8::20",
    ]
    assert (
        result["data"][CONF_DISCOVERY_MODEL]
        == "Nice - BIDIWIFI - HW1"
    )
    assert result["data"][CONF_DISCOVERY_PROTOCOL] == "1.0"
    assert result["data"][CONF_DISCOVERY_STATUS_FLAG] == "0"


async def test_zeroconf_ipv6_discovery_creates_entry(
    hass: HomeAssistant,
) -> None:
    """Test an IPv6-only advertisement remains usable and persisted."""
    discovery = _zeroconf_info(
        address="2001:db8::20",
        service_type="_nap._tcp.local.",
        name="Driveway._nap._tcp.local.",
    )
    with (
        patch.object(config_flow, "NiceBidiClient", FakeClient),
        patch(
            "custom_components.nice_bidiwifi.async_setup_entry",
            return_value=True,
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": "zeroconf"},
            data=discovery,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {
                CONF_NAME: "Driveway",
                CONF_USERNAME: "user",
                CONF_PASSWORD: "aa" * 32,
            },
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_HOST] == "2001:db8::20"
    assert result["data"][CONF_DISCOVERY_ADDRESSES] == ["2001:db8::20"]
    assert FakeClient.instances[0].kwargs["host"] == "2001:db8::20"
    assert (
        config_flow._configuration_url("2001:db8::20")
        == "https://[2001:db8::20]"
    )
    assert (
        config_flow._configuration_url("fe80::20%eth0")
        == "https://[fe80::20%25eth0]"
    )


async def test_zeroconf_updates_stale_host_without_duplicate(
    hass: HomeAssistant,
) -> None:
    """Test rediscovery updates route metadata on the existing entry."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(
            **{
                CONF_HOST: "192.0.2.10",
                CONF_CONNECTION_MODE: ConnectionMode.LOCAL_ONLY.value,
            }
        ),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(address="192.0.2.99", port=8443),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    assert entry.data[CONF_HOST] == "192.0.2.99"
    assert entry.data[CONF_PORT] == 8443
    assert entry.data[CONF_CONNECTION_MODE] == "local_only"
    assert entry.unique_id == "AA:BB:CC:DD:EE:FF"
    assert entry.data[CONF_TARGET_MAC] == "AA:BB:CC:DD:EE:FF"


async def test_zeroconf_duplicate_does_not_create_second_entry(
    hass: HomeAssistant,
) -> None:
    """Test a configured operational service aborts before confirmation."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data=config_entry_data(),
        entry_id="entry-1",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(address="192.0.2.10"),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_zeroconf_ignored_device_remains_ignored(
    hass: HomeAssistant,
) -> None:
    """Test automatic discovery cannot reopen an ignored device."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        source="ignore",
        data={},
        entry_id="ignored-entry",
        unique_id="AA:BB:CC:DD:EE:FF",
    )
    entry.add_to_hass(hass)

    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert hass.config_entries.async_entries(DOMAIN) == [entry]


async def test_zeroconf_provisioning_service_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """Test setup access-point advertisements never show a credential form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(
            service_type="_mfi-config._tcp.local.",
            name="Nice setup._mfi-config._tcp.local.",
        ),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "not_operational"


async def test_zeroconf_unsupported_family_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """Test app-only families are explicit until their transport exists."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(
            properties={
                "deviceid": "AA:BB:CC:DD:EE:FF",
                "model": "Nice - CORE - HW1",
            }
        ),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unsupported_device"


async def test_zeroconf_missing_identity_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """Test discovery requires a stable identity before setup."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(
            name="Driveway._nap._tcp.local.",
            service_type="_nap._tcp.local.",
            hostname="driveway.local.",
            properties={"model": "Nice - BIDIWIFI - HW1"},
        ),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "missing_identity"


async def test_zeroconf_unknown_service_is_not_offered(
    hass: HomeAssistant,
) -> None:
    """Test only observed operational service types reach confirmation."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN,
        context={"source": "zeroconf"},
        data=_zeroconf_info(
            service_type="_http._tcp.local.",
            name="Driveway._http._tcp.local.",
        ),
    )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "unsupported_service"

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
from custom_components.nice_bidiwifi.const import (
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_DISCOVERY_ADDRESSES,
    CONF_DISCOVERY_MODEL,
    CONF_DISCOVERY_PROTOCOL,
    CONF_DISCOVERY_STATUS_FLAG,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DOMAIN,
)
from custom_components.nice_bidiwifi.errors import (
    NiceProtocolError,
    NiceUnsupportedError,
)
from custom_components.nice_bidiwifi.models.config import ConnectionMode
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


def setup_function() -> None:
    """Reset fake client state."""
    FakeClient.connect_error = None
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
        ConnectionMode.LOCAL_ONLY.value
    ]
    assert (
        config_flow.RECOMMENDED_CONNECTION_MODE
        is ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
    )
    assert config_flow.DEFAULT_NEW_CONNECTION_MODE is ConnectionMode.LOCAL_ONLY


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
    }
    assert result["data"][CONF_SOURCE_ID] == "controller"
    assert result["data"][CONF_PORT] == 8443
    assert result["data"][CONF_DEVICE_ID] == 2
    assert result["data"][CONF_T4_TIMEOUT_MS] == 350


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

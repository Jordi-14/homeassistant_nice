"""Config flow for Nice."""

from __future__ import annotations

from dataclasses import dataclass, field
import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    SOURCE_IGNORE,
    ConfigEntry,
    ConfigEntryState,
    ConfigFlow,
)
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import selector
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError
from .cloud import (
    NiceCloudAccessory,
    NiceCloudBootstrapClient,
    NiceCloudBootstrapResult,
    NiceCloudLogin,
)
from .const import (
    CONFIG_FIELDS,
    CONFIG_ENTRY_VERSION,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_LEGACY_LOCAL_TLS,
    CONF_DISCOVERY_MODEL,
    CONF_DISCOVERY_NAME,
    CONF_RELAY_HOST,
    CONF_RELAY_PORT,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DEFAULT_DEVICE_ID,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_RELAY_HOST,
    DEFAULT_RELAY_PORT,
    DEFAULT_T4_TIMEOUT_MS,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudConnectionError,
    NiceCloudSchemaError,
    NiceProtocolError,
    NiceUnsupportedError,
)
from .models.config import ConnectionMode, NiceEntryConfig
from .models.discovery import NiceDiscoveryInfo, normalize_device_id
from .redaction import configured_secrets, redact_text
from .transport.relay import RelayTlsTransport
from .transport.lan import LegacyLanTlsTransport

_LOGGER = logging.getLogger(__name__)

CONF_ADVANCED = "advanced"
CONF_CREDENTIAL_SOURCE = "credential_source"
CONF_CLOUD_ACCOUNT = "cloud_account"
CONF_CLOUD_ACCOUNT_PASSWORD = "cloud_account_password"
CONF_CLOUD_ACCESSORIES = "cloud_accessories"
CONF_CLOUD_CONFIRM = "cloud_confirm"

CREDENTIAL_SOURCE_MANUAL = "manual"
CREDENTIAL_SOURCE_MYNICE = "mynice"
RECOMMENDED_CONNECTION_MODE = ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
IMPLEMENTED_CONNECTION_MODES = tuple(ConnectionMode)
DEFAULT_NEW_CONNECTION_MODE = RECOMMENDED_CONNECTION_MODE

_TEXT_SELECTOR = selector.TextSelector(selector.TextSelectorConfig())
_PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)
_PORT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1,
        max=65535,
        mode=selector.NumberSelectorMode.BOX,
    )
)
_DEVICE_ID_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=1,
        mode=selector.NumberSelectorMode.BOX,
    )
)
_TIMEOUT_SELECTOR = selector.NumberSelector(
    selector.NumberSelectorConfig(
        min=50,
        max=10000,
        mode=selector.NumberSelectorMode.BOX,
        unit_of_measurement="ms",
    )
)
_ADVANCED_SELECTOR = selector.BooleanSelector(
    selector.BooleanSelectorConfig()
)
_CONNECTION_MODE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[mode.value for mode in IMPLEMENTED_CONNECTION_MODES],
        translation_key=CONF_CONNECTION_MODE,
        mode=selector.SelectSelectorMode.LIST,
    )
)
_CREDENTIAL_SOURCE_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[
            CREDENTIAL_SOURCE_MANUAL,
            CREDENTIAL_SOURCE_MYNICE,
        ],
        translation_key=CONF_CREDENTIAL_SOURCE,
        mode=selector.SelectSelectorMode.LIST,
    )
)


def _mode_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return the first-step connection mode selector."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECTION_MODE,
                default=user_input.get(
                    CONF_CONNECTION_MODE,
                    DEFAULT_NEW_CONNECTION_MODE.value,
                ),
            ): _CONNECTION_MODE_SELECTOR,
            vol.Required(
                CONF_CREDENTIAL_SOURCE,
                default=user_input.get(
                    CONF_CREDENTIAL_SOURCE,
                    CREDENTIAL_SOURCE_MANUAL,
                ),
            ): _CREDENTIAL_SOURCE_SELECTOR,
        }
    )


def _cloud_auth_schema() -> vol.Schema:
    """Return transient MyNice account fields."""
    return vol.Schema(
        {
            vol.Required(CONF_CLOUD_ACCOUNT): _TEXT_SELECTOR,
            vol.Required(CONF_CLOUD_ACCOUNT_PASSWORD): _PASSWORD_SELECTOR,
            vol.Required(CONF_CLOUD_CONFIRM, default=False): _ADVANCED_SELECTOR,
        }
    )


def _cloud_accessory_schema(
    accessories: tuple[NiceCloudAccessory, ...],
) -> vol.Schema:
    """Return a multiple-accessory selector."""
    labels: dict[str, int] = {}
    for accessory in accessories:
        labels[accessory.name] = labels.get(accessory.name, 0) + 1
    options: list[selector.SelectOptionDict] = []
    for index, accessory in enumerate(accessories):
        label = accessory.name
        if labels[label] > 1:
            label = f"{label} ({accessory.target_mac[-5:]})"
        options.append(
            selector.SelectOptionDict(
                value=str(index),
                label=label,
            )
        )
    return vol.Schema(
        {
            vol.Required(CONF_CLOUD_ACCESSORIES): selector.SelectSelector(
                selector.SelectSelectorConfig(
                    options=options,
                    multiple=True,
                    mode=selector.SelectSelectorMode.LIST,
                )
            )
        }
    )


def _cloud_local_schema(
    accessory: NiceCloudAccessory,
    mode: ConnectionMode,
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Return route endpoint fields for one imported accessory."""
    user_input = user_input or {}
    fields: dict[vol.Marker, object] = {
        vol.Required(
            CONF_NAME,
            default=user_input.get(CONF_NAME, accessory.name),
        ): _TEXT_SELECTOR,
        vol.Required(
            CONF_DEVICE_ID,
            default=user_input.get(
                CONF_DEVICE_ID,
                accessory.device_id,
            ),
        ): _DEVICE_ID_SELECTOR,
        vol.Required(
            CONF_T4_TIMEOUT_MS,
            default=user_input.get(
                CONF_T4_TIMEOUT_MS,
                DEFAULT_T4_TIMEOUT_MS,
            ),
        ): _TIMEOUT_SELECTOR,
    }
    if mode is not ConnectionMode.CLOUD_ONLY:
        fields[
            vol.Required(
                CONF_HOST,
                default=user_input.get(CONF_HOST, ""),
            )
        ] = _TEXT_SELECTOR
        fields[
            vol.Required(
                CONF_PORT,
                default=user_input.get(CONF_PORT, DEFAULT_PORT),
            )
        ] = _PORT_SELECTOR
        fields[
            vol.Optional(
                CONF_LEGACY_LOCAL_TLS,
                default=bool(user_input.get(CONF_LEGACY_LOCAL_TLS, False)),
            )
        ] = _ADVANCED_SELECTOR
    if mode is not ConnectionMode.LOCAL_ONLY:
        fields[
            vol.Required(
                CONF_RELAY_HOST,
                default=user_input.get(CONF_RELAY_HOST, DEFAULT_RELAY_HOST),
            )
        ] = _TEXT_SELECTOR
        fields[
            vol.Required(
                CONF_RELAY_PORT,
                default=user_input.get(CONF_RELAY_PORT, DEFAULT_RELAY_PORT),
            )
        ] = _PORT_SELECTOR
    return vol.Schema(fields)


def _local_schema(
    user_input: dict[str, Any] | None = None,
    mode: ConnectionMode = ConnectionMode.LOCAL_ONLY,
) -> vol.Schema:
    """Return normal setup fields for the selected routes."""
    user_input = user_input or {}
    fields: dict[vol.Marker, object] = {
            vol.Required(
                CONF_NAME,
                default=user_input.get(CONF_NAME, DEFAULT_NAME),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_TARGET_MAC,
                default=user_input.get(CONF_TARGET_MAC, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_PASSWORD,
                default=user_input.get(CONF_PASSWORD, ""),
            ): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_ADVANCED,
                default=bool(user_input.get(CONF_ADVANCED, False)),
            ): _ADVANCED_SELECTOR,
    }
    if mode is not ConnectionMode.CLOUD_ONLY:
        fields[vol.Required(
            CONF_HOST,
            default=user_input.get(CONF_HOST, ""),
        )] = _TEXT_SELECTOR
    if mode is not ConnectionMode.LOCAL_ONLY:
        fields[vol.Required(
            CONF_RELAY_HOST,
            default=user_input.get(CONF_RELAY_HOST, DEFAULT_RELAY_HOST),
        )] = _TEXT_SELECTOR
        fields[vol.Required(
            CONF_RELAY_PORT,
            default=user_input.get(CONF_RELAY_PORT, DEFAULT_RELAY_PORT),
        )] = _PORT_SELECTOR
    return vol.Schema(fields)


def _discovery_schema(
    user_input: dict[str, Any] | None = None,
) -> vol.Schema:
    """Return credential fields for a discovered interface."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=user_input.get(CONF_NAME, DEFAULT_NAME),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_PASSWORD,
                default=user_input.get(CONF_PASSWORD, ""),
            ): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_ADVANCED,
                default=bool(user_input.get(CONF_ADVANCED, False)),
            ): _ADVANCED_SELECTOR,
        }
    )


def _advanced_schema(
    user_input: dict[str, Any] | None = None,
    mode: ConnectionMode = ConnectionMode.LOCAL_ONLY,
) -> vol.Schema:
    """Return advanced local protocol fields."""
    user_input = user_input or {}
    fields: dict[vol.Marker, object] = {
            vol.Optional(
                CONF_SOURCE_ID,
                default=user_input.get(CONF_SOURCE_ID, ""),
            ): _TEXT_SELECTOR,
            vol.Optional(
                CONF_DEVICE_ID,
                default=user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            ): _DEVICE_ID_SELECTOR,
            vol.Optional(
                CONF_T4_TIMEOUT_MS,
                default=user_input.get(
                    CONF_T4_TIMEOUT_MS,
                    DEFAULT_T4_TIMEOUT_MS,
                ),
            ): _TIMEOUT_SELECTOR,
    }
    if mode is not ConnectionMode.CLOUD_ONLY:
        fields[vol.Optional(
            CONF_PORT,
            default=user_input.get(CONF_PORT, DEFAULT_PORT),
        )] = _PORT_SELECTOR
        fields[vol.Optional(
            CONF_LEGACY_LOCAL_TLS,
            default=bool(user_input.get(CONF_LEGACY_LOCAL_TLS, False)),
        )] = _ADVANCED_SELECTOR
    return vol.Schema(fields)


def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return complete entry fields for reauthentication and reconfiguration."""
    user_input = user_input or {}
    try:
        mode = ConnectionMode(
            str(user_input.get(CONF_CONNECTION_MODE, ConnectionMode.LOCAL_ONLY))
        )
    except ValueError:
        mode = ConnectionMode.LOCAL_ONLY
    fields: dict[vol.Marker, object] = {
            vol.Required(
                CONF_CONNECTION_MODE,
                default=mode.value,
            ): _CONNECTION_MODE_SELECTOR,
            vol.Required(
                CONF_NAME,
                default=user_input.get(CONF_NAME, DEFAULT_NAME),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_TARGET_MAC,
                default=user_input.get(CONF_TARGET_MAC, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_USERNAME,
                default=user_input.get(CONF_USERNAME, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_PASSWORD,
                default=user_input.get(CONF_PASSWORD, ""),
            ): _PASSWORD_SELECTOR,
            vol.Optional(
                CONF_SOURCE_ID,
                default=user_input.get(CONF_SOURCE_ID, ""),
            ): _TEXT_SELECTOR,
            vol.Optional(
                CONF_DEVICE_ID,
                default=user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
            ): _DEVICE_ID_SELECTOR,
            vol.Optional(
                CONF_T4_TIMEOUT_MS,
                default=user_input.get(
                    CONF_T4_TIMEOUT_MS,
                    DEFAULT_T4_TIMEOUT_MS,
                ),
            ): _TIMEOUT_SELECTOR,
    }
    fields[vol.Optional(
        CONF_HOST,
        default=user_input.get(CONF_HOST, ""),
    )] = _TEXT_SELECTOR
    fields[vol.Optional(
        CONF_PORT,
        default=user_input.get(CONF_PORT, DEFAULT_PORT),
    )] = _PORT_SELECTOR
    fields[vol.Optional(
        CONF_RELAY_HOST,
        default=user_input.get(CONF_RELAY_HOST, DEFAULT_RELAY_HOST),
    )] = _TEXT_SELECTOR
    fields[vol.Optional(
        CONF_RELAY_PORT,
        default=user_input.get(CONF_RELAY_PORT, DEFAULT_RELAY_PORT),
    )] = _PORT_SELECTOR
    if mode is not ConnectionMode.CLOUD_ONLY:
        fields[vol.Optional(
            CONF_LEGACY_LOCAL_TLS,
            default=bool(user_input.get(CONF_LEGACY_LOCAL_TLS, False)),
        )] = _ADVANCED_SELECTOR
    return vol.Schema(fields)


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize manual and discovered config-entry data."""
    data = dict(user_input)
    for key in (
        CONF_NAME,
        CONF_HOST,
        CONF_RELAY_HOST,
        CONF_TARGET_MAC,
        CONF_USERNAME,
        CONF_PASSWORD,
        CONF_SOURCE_ID,
    ):
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip()
    if CONF_TARGET_MAC in data:
        raw_identity = str(data[CONF_TARGET_MAC])
        data[CONF_TARGET_MAC] = (
            normalize_device_id(raw_identity) or raw_identity.upper()
        )
    if CONF_PASSWORD in data:
        data[CONF_PASSWORD] = str(data[CONF_PASSWORD]).upper()
    for key in (
        CONF_PORT,
        CONF_RELAY_PORT,
        CONF_DEVICE_ID,
        CONF_T4_TIMEOUT_MS,
    ):
        if key in data:
            data[key] = int(data[key])
    if CONF_CONNECTION_MODE in data:
        data[CONF_CONNECTION_MODE] = ConnectionMode(
            str(data[CONF_CONNECTION_MODE])
        ).value
    if CONF_LEGACY_LOCAL_TLS in data:
        data[CONF_LEGACY_LOCAL_TLS] = bool(data[CONF_LEGACY_LOCAL_TLS])
    data.pop(CONF_ADVANCED, None)
    return data


def _with_local_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Fill safe protocol defaults after the normal field step."""
    normalized = _normalize_input(data)
    mode = ConnectionMode(
        normalized.get(CONF_CONNECTION_MODE, ConnectionMode.LOCAL_ONLY.value)
    )
    if mode is not ConnectionMode.CLOUD_ONLY:
        normalized.setdefault(CONF_PORT, DEFAULT_PORT)
        normalized.setdefault(CONF_LEGACY_LOCAL_TLS, False)
    else:
        normalized.pop(CONF_LEGACY_LOCAL_TLS, None)
    if mode is not ConnectionMode.LOCAL_ONLY:
        normalized.setdefault(CONF_RELAY_HOST, DEFAULT_RELAY_HOST)
        normalized.setdefault(CONF_RELAY_PORT, DEFAULT_RELAY_PORT)
    normalized.setdefault(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)
    normalized.setdefault(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS)
    normalized.setdefault(
        CONF_CONNECTION_MODE,
        ConnectionMode.LOCAL_ONLY.value,
    )
    return normalized


def _merge_entry_data(
    entry: ConfigEntry,
    user_input: dict[str, Any],
) -> dict[str, Any]:
    data = dict(entry.data)
    data.update(user_input)
    return _with_local_defaults(data)


def _configuration_url(host: str) -> str:
    """Return a valid HTTPS URL for an IPv4, IPv6, or hostname target."""
    if ":" in host and not host.startswith("["):
        return f"https://[{host.replace('%', '%25')}]"
    return f"https://{host}"


async def _async_remove_ignored_discovery_entry(
    hass: HomeAssistant,
    identity: str,
) -> None:
    """Replace an ignored discovery marker during an explicit manual import."""
    for entry in hass.config_entries.async_entries(
        DOMAIN,
        include_ignore=True,
    ):
        if entry.source != SOURCE_IGNORE:
            continue
        entry_identity = normalize_device_id(
            entry.unique_id
            or str(entry.data.get(CONF_TARGET_MAC) or "")
        )
        if entry_identity == identity:
            await hass.config_entries.async_remove(entry.entry_id)


def _test_route(config: NiceEntryConfig, *, cloud: bool) -> None:
    endpoint = config.connection.relay if cloud else config.connection.local
    if endpoint is None:
        raise ValueError("The selected route is not configured")
    kwargs: dict[str, Any] = {}
    if cloud:
        kwargs.update(
            transport_factory=RelayTlsTransport.connect,
            route_name="cloud",
        )
    elif config.legacy_local_tls:
        kwargs["transport_factory"] = LegacyLanTlsTransport.connect
    client = NiceBidiClient(
        host=endpoint.host,
        port=endpoint.port,
        credentials=config.credentials,
        device_id=config.device_id,
        timeout=DEFAULT_TIMEOUT,
        t4_timeout_ms=config.t4_timeout_ms,
        **kwargs,
    )
    try:
        client.test_connection()
    finally:
        client.close()


def _test_connection(data: dict[str, Any]) -> None:
    config = NiceEntryConfig.from_mapping(data)
    if config.connection.mode is ConnectionMode.LOCAL_ONLY:
        _test_route(config, cloud=False)
        return
    if config.connection.mode is ConnectionMode.CLOUD_ONLY:
        _test_route(config, cloud=True)
        return
    try:
        _test_route(config, cloud=False)
    except NiceBidiAuthError:
        raise
    except (NiceBidiConnectionError, OSError):
        _test_route(config, cloud=True)


async def _async_validate_input(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> None:
    await hass.async_add_executor_job(_test_connection, data)


async def _async_fetch_cloud_accessories(
    hass: HomeAssistant,
    login: NiceCloudLogin,
) -> NiceCloudBootstrapResult:
    """Fetch credentials with Home Assistant's verified-TLS HTTP session."""
    client = NiceCloudBootstrapClient(async_get_clientsession(hass))
    return await client.async_fetch_accessories(login)


def _error_from_exception(err: Exception) -> str:
    if isinstance(err, NiceBidiAuthError):
        return "invalid_auth"
    if isinstance(err, NiceUnsupportedError):
        return "unsupported_device"
    if isinstance(err, NiceProtocolError):
        return "invalid_protocol"
    if isinstance(err, NiceBidiConnectionError | OSError):
        return "cannot_connect"
    return "unknown"


def _cloud_error_from_exception(err: Exception) -> str:
    """Map bounded cloud bootstrap exceptions to translated form errors."""
    if isinstance(err, NiceCloudAuthError):
        return "cloud_invalid_auth"
    if isinstance(err, NiceCloudAccessError):
        return "cloud_access_denied"
    if isinstance(err, NiceCloudSchemaError):
        return "cloud_schema_changed"
    if isinstance(err, NiceCloudConnectionError | OSError):
        return "cloud_cannot_connect"
    return "unknown"


@dataclass(slots=True, repr=False)
class _CloudBootstrapFlowState:
    """Accessory-only state retained after the cloud session has ended."""

    accessories: tuple[NiceCloudAccessory, ...]
    skipped_records: int
    selected: tuple[NiceCloudAccessory, ...] = ()
    entry_data: list[dict[str, Any]] = field(default_factory=list)
    local_index: int = 0
    mode: ConnectionMode = ConnectionMode.LOCAL_ONLY


def _log_validation_failure(
    step: str,
    data: dict[str, Any],
    err: Exception,
) -> None:
    """Log a setup validation failure without exposing extracted credentials."""
    _LOGGER.warning(
        "Nice setup validation failed at %s for %s:%s "
        "(device_id=%s, t4_timeout_ms=%s): %s: %s",
        step,
        data.get(CONF_HOST),
        data.get(CONF_PORT),
        data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
        data.get(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS),
        err.__class__.__name__,
        redact_text(str(err), configured_secrets(data)),
    )


class NiceBidiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Nice config flow."""

    VERSION = CONFIG_ENTRY_VERSION
    _reauth_entry: ConfigEntry | None = None
    _reconfigure_entry: ConfigEntry | None = None
    _pending_local_data: dict[str, Any] | None = None
    _discovery_info: NiceDiscoveryInfo | None = None
    _cloud_state: _CloudBootstrapFlowState | None = None
    _connection_mode: ConnectionMode = DEFAULT_NEW_CONNECTION_MODE

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select the connection policy for a new entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            try:
                mode = ConnectionMode(str(user_input[CONF_CONNECTION_MODE]))
            except (KeyError, ValueError):
                errors["base"] = "invalid_connection_mode"
            else:
                if mode not in IMPLEMENTED_CONNECTION_MODES:
                    errors["base"] = "connection_mode_unavailable"
                else:
                    self._connection_mode = mode
                    credential_source = str(
                        user_input.get(
                            CONF_CREDENTIAL_SOURCE,
                            CREDENTIAL_SOURCE_MANUAL,
                        )
                    )
                    if credential_source == CREDENTIAL_SOURCE_MYNICE:
                        return await self.async_step_cloud_auth()
                    if credential_source != CREDENTIAL_SOURCE_MANUAL:
                        errors["base"] = "invalid_credential_source"
                    else:
                        return await self.async_step_local()

        return self.async_show_form(
            step_id="user",
            data_schema=_mode_schema(user_input),
            errors=errors,
        )

    async def async_step_cloud_auth(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Use account credentials once to obtain accessory credentials."""
        errors: dict[str, str] = {}
        if user_input is not None:
            if not user_input.get(CONF_CLOUD_CONFIRM):
                errors["base"] = "cloud_confirmation_required"
            else:
                login: NiceCloudLogin | None = None
                try:
                    login = NiceCloudLogin(
                        account_username=str(
                            user_input.get(CONF_CLOUD_ACCOUNT, "")
                        ).strip(),
                        account_password=str(
                            user_input.get(CONF_CLOUD_ACCOUNT_PASSWORD, "")
                        ),
                    )
                    result = await _async_fetch_cloud_accessories(
                        self.hass,
                        login,
                    )
                except ValueError:
                    errors["base"] = "cloud_invalid_fields"
                except Exception as err:
                    _LOGGER.warning(
                        "MyNice credential bootstrap failed (%s)",
                        err.__class__.__name__,
                    )
                    errors["base"] = _cloud_error_from_exception(err)
                else:
                    configured_ids = {
                        normalize_device_id(
                            entry.unique_id
                            or str(entry.data.get(CONF_TARGET_MAC) or "")
                        )
                        for entry in self.hass.config_entries.async_entries(
                            DOMAIN,
                            include_ignore=True,
                        )
                        if entry.source != SOURCE_IGNORE
                    }
                    accessories = tuple(
                        accessory
                        for accessory in result.accessories
                        if accessory.identity not in configured_ids
                    )
                    if not accessories:
                        return self.async_abort(
                            reason="all_accessories_configured"
                        )
                    self._cloud_state = _CloudBootstrapFlowState(
                        accessories=accessories,
                        skipped_records=result.skipped_records,
                        mode=self._connection_mode,
                    )
                    return await self.async_step_cloud_accessories()
                finally:
                    if login is not None:
                        login.clear()
                    for key in (
                        CONF_CLOUD_ACCOUNT,
                        CONF_CLOUD_ACCOUNT_PASSWORD,
                    ):
                        if key in user_input:
                            user_input[key] = ""

        return self.async_show_form(
            step_id="cloud_auth",
            data_schema=_cloud_auth_schema(),
            errors=errors,
        )

    async def async_step_cloud_accessories(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Select one or more accessories from the transient result."""
        state = self._cloud_state
        if state is None or not state.accessories:
            return self.async_abort(reason="cloud_session_lost")
        errors: dict[str, str] = {}
        if user_input is not None:
            selected = user_input.get(CONF_CLOUD_ACCESSORIES)
            if not isinstance(selected, list) or not selected:
                errors["base"] = "cloud_select_accessory"
            else:
                try:
                    indices = tuple(dict.fromkeys(int(value) for value in selected))
                    state.selected = tuple(
                        state.accessories[index] for index in indices
                    )
                    if any(index < 0 for index in indices):
                        raise IndexError
                except (IndexError, TypeError, ValueError):
                    errors["base"] = "cloud_select_accessory"
                else:
                    state.entry_data.clear()
                    state.local_index = 0
                    return await self.async_step_cloud_local()

        return self.async_show_form(
            step_id="cloud_accessories",
            data_schema=_cloud_accessory_schema(state.accessories),
            errors=errors,
            description_placeholders={
                "count": str(len(state.accessories)),
                "skipped": str(state.skipped_records),
            },
        )

    async def async_step_cloud_local(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect and validate the LAN endpoint for each selected accessory."""
        state = self._cloud_state
        if (
            state is None
            or not state.selected
            or state.local_index >= len(state.selected)
        ):
            return self.async_abort(reason="cloud_session_lost")

        accessory = state.selected[state.local_index]
        errors: dict[str, str] = {}
        if user_input is not None:
            data = accessory.entry_data(
                mode=state.mode,
                host=(
                    str(user_input.get(CONF_HOST, ""))
                    if state.mode is not ConnectionMode.CLOUD_ONLY
                    else None
                ),
                port=int(user_input.get(CONF_PORT, DEFAULT_PORT)),
                relay_host=(
                    str(user_input.get(CONF_RELAY_HOST, ""))
                    if state.mode is not ConnectionMode.LOCAL_ONLY
                    else None
                ),
                relay_port=int(
                    user_input.get(CONF_RELAY_PORT, DEFAULT_RELAY_PORT)
                ),
                name=str(user_input.get(CONF_NAME, accessory.name)),
                device_id=int(
                    user_input.get(CONF_DEVICE_ID, accessory.device_id)
                ),
                t4_timeout_ms=int(
                    user_input.get(
                        CONF_T4_TIMEOUT_MS,
                        DEFAULT_T4_TIMEOUT_MS,
                    )
                ),
            )
            if state.mode is not ConnectionMode.CLOUD_ONLY:
                data[CONF_LEGACY_LOCAL_TLS] = bool(
                    user_input.get(CONF_LEGACY_LOCAL_TLS, False)
                )
            data = _with_local_defaults(data)
            try:
                await _async_validate_input(self.hass, data)
            except Exception as err:
                _log_validation_failure("cloud_local", data, err)
                errors["base"] = _error_from_exception(err)
            else:
                state.entry_data.append(data)
                state.local_index += 1
                if state.local_index < len(state.selected):
                    return await self.async_step_cloud_local()
                return await self._async_finish_cloud_entries()

        return self.async_show_form(
            step_id="cloud_local",
            data_schema=_cloud_local_schema(accessory, state.mode, user_input),
            errors=errors,
            description_placeholders={
                "name": accessory.name,
                "current": str(state.local_index + 1),
                "total": str(len(state.selected)),
            },
        )

    async def _async_finish_cloud_entries(self) -> FlowResult:
        """Create every validated entry, using import flows for extras."""
        state = self._cloud_state
        entries = state.entry_data if state is not None else []
        if not entries:
            return self.async_abort(reason="cloud_session_lost")

        first = entries[0]
        first_identity = str(first[CONF_TARGET_MAC])
        await _async_remove_ignored_discovery_entry(
            self.hass,
            first_identity,
        )
        await self.async_set_unique_id(first_identity, raise_on_progress=False)
        self._abort_if_unique_id_configured()

        for extra in entries[1:]:
            await self.hass.config_entries.flow.async_init(
                DOMAIN,
                context={"source": "import"},
                data=extra,
            )
        self._cloud_state = None
        return self.async_create_entry(
            title=str(first[CONF_NAME]),
            data=first,
        )

    async def async_step_import(
        self,
        import_data: dict[str, Any],
    ) -> FlowResult:
        """Create an already validated extra accessory from cloud bootstrap."""
        data = _with_local_defaults(
            {
                key: value
                for key, value in import_data.items()
                if key in CONFIG_FIELDS
            }
        )
        identity = normalize_device_id(
            str(data.get(CONF_TARGET_MAC) or "")
        )
        if identity is None:
            return self.async_abort(reason="missing_identity")
        data[CONF_TARGET_MAC] = identity
        try:
            NiceEntryConfig.from_mapping(data)
        except (TypeError, ValueError):
            return self.async_abort(reason="invalid_import")
        await _async_remove_ignored_discovery_entry(self.hass, identity)
        await self.async_set_unique_id(identity, raise_on_progress=False)
        self._abort_if_unique_id_configured()
        return self.async_create_entry(
            title=str(data[CONF_NAME]),
            data=data,
        )

    async def async_step_local(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect normal local connection and credential fields."""
        if user_input is not None:
            advanced = bool(user_input.get(CONF_ADVANCED, False))
            data = _with_local_defaults(
                {
                    **user_input,
                    CONF_CONNECTION_MODE: self._connection_mode.value,
                }
            )
            if advanced:
                self._pending_local_data = data
                return await self.async_step_local_advanced()
            return await self._async_finish_new_local(
                data,
                form_step="local",
            )

        return self.async_show_form(
            step_id="local",
            data_schema=_local_schema(mode=self._connection_mode),
        )

    async def async_step_local_advanced(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect optional local protocol tuning fields."""
        if self._pending_local_data is None:
            return self.async_abort(reason="unknown")
        if user_input is not None:
            data = dict(self._pending_local_data)
            data.update(user_input)
            data = _with_local_defaults(data)
            self._pending_local_data = data
            return await self._async_finish_new_local(
                data,
                form_step="local_advanced",
            )

        return self.async_show_form(
            step_id="local_advanced",
            data_schema=_advanced_schema(
                self._pending_local_data,
                self._connection_mode,
            ),
        )

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ) -> FlowResult:
        """Handle a Nice Bonjour discovery."""
        discovered = NiceDiscoveryInfo.from_service(
            host=discovery_info.host,
            addresses=tuple(
                str(address) for address in discovery_info.ip_addresses
            ),
            port=discovery_info.port,
            name=discovery_info.name,
            hostname=discovery_info.hostname,
            service_type=discovery_info.type,
            properties=discovery_info.properties,
        )
        if discovered.provisioning:
            return self.async_abort(reason="not_operational")
        if not discovered.operational:
            return self.async_abort(reason="unsupported_service")
        if not discovered.supported_family:
            return self.async_abort(reason="unsupported_device")
        if discovered.unique_id is None:
            return self.async_abort(reason="missing_identity")

        await self.async_set_unique_id(discovered.unique_id)
        self._abort_if_unique_id_configured(
            updates={
                CONF_HOST: discovered.host,
                CONF_PORT: discovered.port,
                **discovered.entry_metadata(),
            }
        )
        self._discovery_info = discovered
        self.context.update(
            {
                "title_placeholders": {"name": discovered.name},
                "configuration_url": _configuration_url(discovered.host),
            }
        )
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect credentials for a discovered interface."""
        discovered = self._discovery_info
        if discovered is None or discovered.unique_id is None:
            return self.async_abort(reason="unknown")

        if user_input is not None:
            advanced = bool(user_input.get(CONF_ADVANCED, False))
            data = {
                **user_input,
                CONF_HOST: discovered.host,
                CONF_PORT: discovered.port,
                CONF_TARGET_MAC: discovered.unique_id,
                CONF_CONNECTION_MODE: ConnectionMode.LOCAL_ONLY.value,
                **discovered.entry_metadata(),
            }
            data = _with_local_defaults(data)
            if advanced:
                self._pending_local_data = data
                return await self.async_step_local_advanced()
            return await self._async_finish_new_local(
                data,
                form_step="zeroconf_confirm",
            )

        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=_discovery_schema(
                {CONF_NAME: discovered.name}
            ),
            description_placeholders={
                "host": discovered.host,
                "model": discovered.model or discovered.family.value,
            },
        )

    async def _async_finish_new_local(
        self,
        data: dict[str, Any],
        *,
        form_step: str,
    ) -> FlowResult:
        """Validate and create a new local entry."""
        errors: dict[str, str] = {}
        identity = normalize_device_id(
            str(data.get(CONF_TARGET_MAC) or "")
        )
        if identity is None:
            errors["base"] = "invalid_device_id"
        else:
            data[CONF_TARGET_MAC] = identity
            try:
                await _async_validate_input(self.hass, data)
            except Exception as err:
                _log_validation_failure(form_step, data, err)
                errors["base"] = _error_from_exception(err)
            else:
                await self.async_set_unique_id(identity, raise_on_progress=False)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=data[CONF_NAME],
                    data=data,
                )

        if form_step == "zeroconf_confirm":
            return self.async_show_form(
                step_id=form_step,
                data_schema=_discovery_schema(data),
                description_placeholders={
                    "host": str(data.get(CONF_HOST, "")),
                    "model": str(
                        data.get(CONF_DISCOVERY_MODEL)
                        or data.get(CONF_DISCOVERY_NAME)
                        or "Nice"
                    ),
                },
                errors=errors,
            )
        if form_step == "local_advanced":
            return self.async_show_form(
                step_id=form_step,
                data_schema=_advanced_schema(data, self._connection_mode),
                errors=errors,
            )
        return self.async_show_form(
            step_id=form_step,
            data_schema=_local_schema(data, self._connection_mode),
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: dict[str, Any],
    ) -> FlowResult:
        """Handle a reauthentication request."""
        entry = self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")
        self._reauth_entry = entry
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Confirm reauthentication credentials."""
        entry = self._reauth_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")
        return await self._async_update_existing_entry(
            entry,
            user_input,
            step_id="reauth_confirm",
            log_context="reauth",
            success_reason="reauth_successful",
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle reconfiguration."""
        entry = self._reconfigure_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")
        self._reconfigure_entry = entry
        return await self._async_update_existing_entry(
            entry,
            user_input,
            step_id="reconfigure",
            log_context="reconfigure",
            success_reason="reconfigure_successful",
            release_existing_session=True,
        )

    async def _async_update_existing_entry(
        self,
        entry: ConfigEntry,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        log_context: str,
        success_reason: str,
        release_existing_session: bool = False,
    ) -> FlowResult:
        """Validate and atomically update an existing config entry."""
        errors: dict[str, str] = {}
        if user_input is not None:
            data = _merge_entry_data(entry, user_input)
            expected_identity = normalize_device_id(
                entry.unique_id
                or str(entry.data.get(CONF_TARGET_MAC) or "")
            )
            if data[CONF_TARGET_MAC] != expected_identity:
                errors["base"] = "wrong_device"
            else:
                cannot_release_session = (
                    release_existing_session
                    and not entry.state.recoverable
                )
                release_before_validation = (
                    release_existing_session
                    and entry.state is not ConfigEntryState.NOT_LOADED
                )
                if cannot_release_session:
                    errors["base"] = "cannot_connect"
                elif (
                    release_before_validation
                    and not await self.hass.config_entries.async_unload(
                        entry.entry_id
                    )
                ):
                    _LOGGER.warning(
                        "Nice %s could not release the existing NHK session",
                        log_context,
                    )
                    errors["base"] = "cannot_connect"
                else:
                    validation_succeeded = False
                    try:
                        await _async_validate_input(self.hass, data)
                    except Exception as err:
                        _log_validation_failure(log_context, data, err)
                        errors["base"] = _error_from_exception(err)
                    else:
                        self.hass.config_entries.async_update_entry(
                            entry,
                            title=data[CONF_NAME],
                            data=data,
                        )
                        validation_succeeded = True
                    finally:
                        if release_before_validation:
                            await self.hass.config_entries.async_setup(
                                entry.entry_id
                            )

                    if validation_succeeded:
                        if not release_before_validation:
                            await self.hass.config_entries.async_reload(
                                entry.entry_id
                            )
                        return self.async_abort(reason=success_reason)
            user_input = data

        return self.async_show_form(
            step_id=step_id,
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    def _entry_from_context(self) -> ConfigEntry | None:
        """Return the config entry for the current flow context."""
        entry_id = self.context.get("entry_id")
        if not isinstance(entry_id, str):
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

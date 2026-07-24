"""Config flow for Nice."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow
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
from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError
from .const import (
    CONFIG_ENTRY_VERSION,
    CONF_CONNECTION_MODE,
    CONF_DEVICE_ID,
    CONF_DISCOVERY_MODEL,
    CONF_DISCOVERY_NAME,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DEFAULT_DEVICE_ID,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_T4_TIMEOUT_MS,
    DEFAULT_TIMEOUT,
    DOMAIN,
)
from .errors import NiceProtocolError, NiceUnsupportedError
from .models.config import ConnectionMode, NiceEntryConfig
from .models.discovery import NiceDiscoveryInfo, normalize_device_id
from .redaction import configured_secrets, redact_text

_LOGGER = logging.getLogger(__name__)

CONF_ADVANCED = "advanced"
RECOMMENDED_CONNECTION_MODE = ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
IMPLEMENTED_CONNECTION_MODES = (ConnectionMode.LOCAL_ONLY,)
DEFAULT_NEW_CONNECTION_MODE = (
    RECOMMENDED_CONNECTION_MODE
    if RECOMMENDED_CONNECTION_MODE in IMPLEMENTED_CONNECTION_MODES
    else ConnectionMode.LOCAL_ONLY
)

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
        }
    )


def _local_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return normal local setup fields."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=user_input.get(CONF_NAME, DEFAULT_NAME),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_HOST,
                default=user_input.get(CONF_HOST, ""),
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
    )


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
) -> vol.Schema:
    """Return advanced local protocol fields."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Optional(
                CONF_SOURCE_ID,
                default=user_input.get(CONF_SOURCE_ID, ""),
            ): _TEXT_SELECTOR,
            vol.Optional(
                CONF_PORT,
                default=user_input.get(CONF_PORT, DEFAULT_PORT),
            ): _PORT_SELECTOR,
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
    )


def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    """Return complete entry fields for reauthentication and reconfiguration."""
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_NAME,
                default=user_input.get(CONF_NAME, DEFAULT_NAME),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_HOST,
                default=user_input.get(CONF_HOST, ""),
            ): _TEXT_SELECTOR,
            vol.Required(
                CONF_PORT,
                default=user_input.get(CONF_PORT, DEFAULT_PORT),
            ): _PORT_SELECTOR,
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
    )


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    """Normalize manual and discovered config-entry data."""
    data = dict(user_input)
    for key in (
        CONF_NAME,
        CONF_HOST,
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
    for key in (CONF_PORT, CONF_DEVICE_ID, CONF_T4_TIMEOUT_MS):
        if key in data:
            data[key] = int(data[key])
    if CONF_CONNECTION_MODE in data:
        data[CONF_CONNECTION_MODE] = ConnectionMode(
            str(data[CONF_CONNECTION_MODE])
        ).value
    data.pop(CONF_ADVANCED, None)
    return data


def _with_local_defaults(data: dict[str, Any]) -> dict[str, Any]:
    """Fill safe local defaults after the normal field step."""
    normalized = _normalize_input(data)
    normalized.setdefault(CONF_PORT, DEFAULT_PORT)
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


def _test_connection(data: dict[str, Any]) -> None:
    config = NiceEntryConfig.from_mapping(data)
    endpoint = config.connection.local
    if endpoint is None:
        raise ValueError("Local setup requires a local endpoint")
    client = NiceBidiClient(
        host=endpoint.host,
        port=endpoint.port,
        credentials=config.credentials,
        device_id=config.device_id,
        timeout=DEFAULT_TIMEOUT,
        t4_timeout_ms=config.t4_timeout_ms,
    )
    try:
        client.test_connection()
    finally:
        client.close()


async def _async_validate_input(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> None:
    await hass.async_add_executor_job(_test_connection, data)


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
                elif mode is ConnectionMode.LOCAL_ONLY:
                    return await self.async_step_local()

        return self.async_show_form(
            step_id="user",
            data_schema=_mode_schema(user_input),
            errors=errors,
        )

    async def async_step_local(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Collect normal local connection and credential fields."""
        if user_input is not None:
            advanced = bool(user_input.get(CONF_ADVANCED, False))
            data = _with_local_defaults(user_input)
            if advanced:
                self._pending_local_data = data
                return await self.async_step_local_advanced()
            return await self._async_finish_new_local(
                data,
                form_step="local",
            )

        return self.async_show_form(
            step_id="local",
            data_schema=_local_schema(),
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
            data_schema=_advanced_schema(self._pending_local_data),
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
                await self.async_set_unique_id(identity)
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
                data_schema=_advanced_schema(data),
                errors=errors,
            )
        return self.async_show_form(
            step_id=form_step,
            data_schema=_local_schema(data),
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
        )

    async def _async_update_existing_entry(
        self,
        entry: ConfigEntry,
        user_input: dict[str, Any] | None,
        *,
        step_id: str,
        log_context: str,
        success_reason: str,
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

"""Config flow for Nice."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigEntry, ConfigFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers import selector

from .cloud_api import NiceApiError as NiceCloudApiError
from .cloud_api import NiceAuthError as NiceCloudAuthError
from .cloud_api import NiceCloud
from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError, NiceBidiCredentials
from .const import (
    CONF_CLOUD_TOKEN,
    CONF_CONNECTION_METHOD,
    CONF_SOURCE_ID,
    CONF_DEVICE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    CONNECTION_METHOD_CLOUD,
    CONNECTION_METHOD_LOCAL,
    DEFAULT_DEVICE_ID,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_T4_TIMEOUT_MS,
    DEFAULT_TIMEOUT,
    DOMAIN,
)

_LOGGER = logging.getLogger(__name__)


class NiceCloudNoDevicesError(Exception):
    """The MyNice account did not expose controllable doors."""


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
_CONNECTION_METHOD_SELECTOR = selector.SelectSelector(
    selector.SelectSelectorConfig(
        options=[CONNECTION_METHOD_LOCAL, CONNECTION_METHOD_CLOUD],
        translation_key=CONF_CONNECTION_METHOD,
        mode=selector.SelectSelectorMode.DROPDOWN,
    )
)


def _method_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(
                CONF_CONNECTION_METHOD,
                default=user_input.get(CONF_CONNECTION_METHOD, CONNECTION_METHOD_LOCAL),
            ): _CONNECTION_METHOD_SELECTOR,
        }
    )


def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): _TEXT_SELECTOR,
            vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): _TEXT_SELECTOR,
            vol.Required(CONF_PORT, default=user_input.get(CONF_PORT, DEFAULT_PORT)): _PORT_SELECTOR,
            vol.Required(CONF_TARGET_MAC, default=user_input.get(CONF_TARGET_MAC, "")): _TEXT_SELECTOR,
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): _TEXT_SELECTOR,
            vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): _PASSWORD_SELECTOR,
            vol.Optional(CONF_SOURCE_ID, default=user_input.get(CONF_SOURCE_ID, "")): _TEXT_SELECTOR,
            vol.Optional(CONF_DEVICE_ID, default=user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)): _DEVICE_ID_SELECTOR,
            vol.Optional(CONF_T4_TIMEOUT_MS, default=user_input.get(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS)): _TIMEOUT_SELECTOR,
        }
    )


def _cloud_schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): _TEXT_SELECTOR,
            vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): _PASSWORD_SELECTOR,
        }
    )


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_NAME, CONF_HOST, CONF_TARGET_MAC, CONF_USERNAME, CONF_PASSWORD, CONF_SOURCE_ID):
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip()
    data[CONF_CONNECTION_METHOD] = CONNECTION_METHOD_LOCAL
    data[CONF_TARGET_MAC] = data[CONF_TARGET_MAC].upper()
    data[CONF_PASSWORD] = data[CONF_PASSWORD].upper()
    for key in (CONF_PORT, CONF_DEVICE_ID, CONF_T4_TIMEOUT_MS):
        if key in data:
            data[key] = int(data[key])
    return data


def _normalize_cloud_input(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_USERNAME, CONF_PASSWORD):
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip()
    data[CONF_CONNECTION_METHOD] = CONNECTION_METHOD_CLOUD
    return data


def _merge_entry_data(entry: ConfigEntry, user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(entry.data)
    data.update(user_input)
    return _normalize_input(data)


def _entry_connection_method(entry: ConfigEntry) -> str:
    return str(entry.data.get(CONF_CONNECTION_METHOD, CONNECTION_METHOD_LOCAL))


def _test_connection(data: dict[str, Any]) -> None:
    credentials = NiceBidiCredentials(
        username=data[CONF_USERNAME],
        password_hex=data[CONF_PASSWORD],
        target_mac=data[CONF_TARGET_MAC],
        source_id=data.get(CONF_SOURCE_ID) or None,
    )
    client = NiceBidiClient(
        host=data[CONF_HOST],
        port=data[CONF_PORT],
        credentials=credentials,
        device_id=data.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID),
        timeout=DEFAULT_TIMEOUT,
        t4_timeout_ms=data.get(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS),
    )
    try:
        client.test_connection()
    finally:
        client.close()


async def _async_validate_input(hass: HomeAssistant, data: dict[str, Any]) -> None:
    await hass.async_add_executor_job(_test_connection, data)


async def _async_validate_cloud_input(
    hass: HomeAssistant,
    data: dict[str, Any],
) -> dict[str, Any]:
    session = async_get_clientsession(hass)
    client = NiceCloud(
        session,
        username=data[CONF_USERNAME],
        password=data[CONF_PASSWORD],
    )
    await client.async_login()
    doors = await client.async_discover()
    if not any(door.get("creds") for door in doors):
        raise NiceCloudNoDevicesError
    return {
        CONF_CONNECTION_METHOD: CONNECTION_METHOD_CLOUD,
        CONF_USERNAME: data[CONF_USERNAME],
        CONF_PASSWORD: data[CONF_PASSWORD],
        CONF_CLOUD_TOKEN: client.token,
    }


def _error_from_exception(err: Exception) -> str:
    if isinstance(err, NiceBidiAuthError):
        return "invalid_auth"
    if isinstance(err, NiceBidiConnectionError | OSError):
        return "cannot_connect"
    return "unknown"


def _cloud_error_from_exception(err: Exception) -> str:
    if isinstance(err, NiceCloudAuthError):
        return "invalid_auth"
    if isinstance(err, NiceCloudNoDevicesError):
        return "no_devices"
    if isinstance(err, NiceCloudApiError | OSError):
        return "cannot_connect"
    return "unknown"


def _redact_known_values(message: str, data: dict[str, Any]) -> str:
    redacted = message
    for key in (CONF_USERNAME, CONF_PASSWORD, CONF_SOURCE_ID, CONF_TARGET_MAC):
        value = data.get(key)
        if value:
            redacted = redacted.replace(str(value), "<redacted>")
    return redacted


def _log_validation_failure(step: str, data: dict[str, Any], err: Exception) -> None:
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
        _redact_known_values(str(err), data),
    )


class NiceBidiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Nice config flow."""

    VERSION = 1
    _reauth_entry: ConfigEntry | None = None
    _reconfigure_entry: ConfigEntry | None = None

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        if user_input is not None:
            method = user_input.get(CONF_CONNECTION_METHOD)
            if method == CONNECTION_METHOD_CLOUD:
                return await self.async_step_cloud()
            if method == CONNECTION_METHOD_LOCAL:
                return await self.async_step_local()
            if CONF_HOST in user_input:
                return await self.async_step_local(user_input)

        return self.async_show_form(
            step_id="user",
            data_schema=_method_schema(user_input),
        )

    async def async_step_local(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle local BiDi-WiFi setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalize_input(user_input)
            try:
                await _async_validate_input(self.hass, data)
            except Exception as err:
                _log_validation_failure("user", data, err)
                errors["base"] = _error_from_exception(err)
            else:
                await self.async_set_unique_id(data[CONF_TARGET_MAC])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_NAME], data=data)

            user_input = data

        return self.async_show_form(
            step_id="local",
            data_schema=_schema(user_input),
            errors=errors,
        )

    async def async_step_cloud(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle MyNice cloud account setup."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalize_cloud_input(user_input)
            try:
                entry_data = await _async_validate_cloud_input(self.hass, data)
            except Exception as err:
                _log_validation_failure("cloud", data, err)
                errors["base"] = _cloud_error_from_exception(err)
            else:
                username = entry_data[CONF_USERNAME]
                await self.async_set_unique_id(f"cloud:{username.lower()}")
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=username, data=entry_data)

            user_input = data

        return self.async_show_form(
            step_id="cloud",
            data_schema=_cloud_schema(user_input),
            errors=errors,
        )

    async def async_step_reauth(self, entry_data: dict[str, Any]) -> FlowResult:
        """Handle a reauthentication request."""
        entry = self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")
        self._reauth_entry = entry
        if _entry_connection_method(entry) == CONNECTION_METHOD_CLOUD:
            return await self.async_step_cloud_reauth_confirm()
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Confirm reauthentication credentials."""
        entry = self._reauth_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = _merge_entry_data(entry, user_input)
            if data[CONF_TARGET_MAC] != entry.unique_id:
                errors["base"] = "wrong_device"
            else:
                try:
                    await _async_validate_input(self.hass, data)
                except Exception as err:
                    _log_validation_failure("reauth", data, err)
                    errors["base"] = _error_from_exception(err)
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        title=data[CONF_NAME],
                        data=data,
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reauth_successful")
            user_input = data

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_cloud_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Confirm cloud account reauthentication credentials."""
        entry = self._reauth_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize_cloud_input(
                {
                    CONF_USERNAME: entry.data[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
            )
            try:
                entry_data = await _async_validate_cloud_input(self.hass, data)
            except Exception as err:
                _log_validation_failure("cloud_reauth", data, err)
                errors["base"] = _cloud_error_from_exception(err)
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    data=entry_data,
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reauth_successful")

        return self.async_show_form(
            step_id="cloud_reauth_confirm",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    async def async_step_reconfigure(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle reconfiguration."""
        entry = self._reconfigure_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")
        self._reconfigure_entry = entry

        if _entry_connection_method(entry) == CONNECTION_METHOD_CLOUD:
            return await self.async_step_cloud_reconfigure(user_input)

        errors: dict[str, str] = {}
        if user_input is not None:
            data = _merge_entry_data(entry, user_input)
            if data[CONF_TARGET_MAC] != entry.unique_id:
                errors["base"] = "wrong_device"
            else:
                try:
                    await _async_validate_input(self.hass, data)
                except Exception as err:
                    _log_validation_failure("reconfigure", data, err)
                    errors["base"] = _error_from_exception(err)
                else:
                    self.hass.config_entries.async_update_entry(
                        entry,
                        title=data[CONF_NAME],
                        data=data,
                    )
                    await self.hass.config_entries.async_reload(entry.entry_id)
                    return self.async_abort(reason="reconfigure_successful")
            user_input = data

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=_schema(user_input or dict(entry.data)),
            errors=errors,
        )

    async def async_step_cloud_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> FlowResult:
        """Handle cloud account reconfiguration."""
        entry = self._reconfigure_entry or self._entry_from_context()
        if entry is None:
            return self.async_abort(reason="unknown")

        errors: dict[str, str] = {}
        if user_input is not None:
            data = _normalize_cloud_input(
                {
                    CONF_USERNAME: entry.data[CONF_USERNAME],
                    CONF_PASSWORD: user_input[CONF_PASSWORD],
                }
            )
            try:
                entry_data = await _async_validate_cloud_input(self.hass, data)
            except Exception as err:
                _log_validation_failure("cloud_reconfigure", data, err)
                errors["base"] = _cloud_error_from_exception(err)
            else:
                self.hass.config_entries.async_update_entry(
                    entry,
                    title=entry.data[CONF_USERNAME],
                    data=entry_data,
                )
                await self.hass.config_entries.async_reload(entry.entry_id)
                return self.async_abort(reason="reconfigure_successful")

            user_input = data

        return self.async_show_form(
            step_id="cloud_reconfigure",
            data_schema=vol.Schema({vol.Required(CONF_PASSWORD): _PASSWORD_SELECTOR}),
            description_placeholders={"username": entry.data[CONF_USERNAME]},
            errors=errors,
        )

    def _entry_from_context(self) -> ConfigEntry | None:
        """Return the config entry for the current flow context."""
        entry_id = self.context.get("entry_id")
        if not isinstance(entry_id, str):
            return None
        return self.hass.config_entries.async_get_entry(entry_id)

"""Config flow for Nice BiDi-WiFi."""

from __future__ import annotations

from typing import Any

import voluptuous as vol

from homeassistant.config_entries import ConfigFlow
from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError, NiceBidiCredentials
from .const import (
    CONF_SOURCE_ID,
    CONF_DEVICE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
    DEFAULT_DEVICE_ID,
    DEFAULT_NAME,
    DEFAULT_PORT,
    DEFAULT_T4_TIMEOUT_MS,
    DEFAULT_TIMEOUT,
    DOMAIN,
)


def _schema(user_input: dict[str, Any] | None = None) -> vol.Schema:
    user_input = user_input or {}
    return vol.Schema(
        {
            vol.Required(CONF_NAME, default=user_input.get(CONF_NAME, DEFAULT_NAME)): str,
            vol.Required(CONF_HOST, default=user_input.get(CONF_HOST, "")): str,
            vol.Required(CONF_PORT, default=user_input.get(CONF_PORT, DEFAULT_PORT)): int,
            vol.Required(CONF_TARGET_MAC, default=user_input.get(CONF_TARGET_MAC, "")): str,
            vol.Required(CONF_USERNAME, default=user_input.get(CONF_USERNAME, "")): str,
            vol.Required(CONF_PASSWORD, default=user_input.get(CONF_PASSWORD, "")): str,
            vol.Optional(CONF_SOURCE_ID, default=user_input.get(CONF_SOURCE_ID, "")): str,
            vol.Optional(CONF_DEVICE_ID, default=user_input.get(CONF_DEVICE_ID, DEFAULT_DEVICE_ID)): int,
            vol.Optional(CONF_T4_TIMEOUT_MS, default=user_input.get(CONF_T4_TIMEOUT_MS, DEFAULT_T4_TIMEOUT_MS)): int,
        }
    )


def _normalize_input(user_input: dict[str, Any]) -> dict[str, Any]:
    data = dict(user_input)
    for key in (CONF_NAME, CONF_HOST, CONF_TARGET_MAC, CONF_USERNAME, CONF_PASSWORD, CONF_SOURCE_ID):
        if key in data and isinstance(data[key], str):
            data[key] = data[key].strip()
    data[CONF_TARGET_MAC] = data[CONF_TARGET_MAC].upper()
    data[CONF_PASSWORD] = data[CONF_PASSWORD].upper()
    return data


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


class NiceBidiConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a Nice BiDi-WiFi config flow."""

    VERSION = 1

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> FlowResult:
        """Handle the initial step."""
        errors: dict[str, str] = {}

        if user_input is not None:
            data = _normalize_input(user_input)
            try:
                await _async_validate_input(self.hass, data)
            except NiceBidiAuthError:
                errors["base"] = "invalid_auth"
            except (NiceBidiConnectionError, OSError):
                errors["base"] = "cannot_connect"
            except Exception:
                errors["base"] = "unknown"
            else:
                await self.async_set_unique_id(data[CONF_TARGET_MAC])
                self._abort_if_unique_id_configured()
                return self.async_create_entry(title=data[CONF_NAME], data=data)

            user_input = data

        return self.async_show_form(
            step_id="user",
            data_schema=_schema(user_input),
            errors=errors,
        )

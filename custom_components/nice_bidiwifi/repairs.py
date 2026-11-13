"""Repair flows for Nice."""

from __future__ import annotations

from homeassistant.components.repairs import ConfirmRepairFlow, RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult

from .runtime import get_coordinator


class ControllerLimitsRepairFlow(ConfirmRepairFlow):
    """Accept the new controller travel limits as the reference."""

    def __init__(self, entry_id: str) -> None:
        """Store the config entry this repair belongs to."""
        self._entry_id = entry_id

    async def async_step_confirm(self, user_input: dict | None = None) -> FlowResult:
        """Make the current open position the new baseline."""
        if user_input is None:
            return await super().async_step_confirm(user_input)
        entry = self.hass.config_entries.async_get_entry(self._entry_id)
        if (
            entry is None
            or entry.runtime_data is None
            or not get_coordinator(entry).async_accept_controller_limits()
        ):
            return self.async_abort(reason="not_loaded")
        return self.async_create_entry(data={})


async def async_create_fix_flow(
    hass: HomeAssistant,
    issue_id: str,
    data: dict[str, str | int | float | None] | None,
) -> RepairsFlow:
    """Create the fix flow for a Nice repair issue."""
    return ControllerLimitsRepairFlow(str((data or {}).get("entry_id")))

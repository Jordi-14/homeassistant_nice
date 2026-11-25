"""One-shot app inventory inspection, independent of the NHK router."""

from __future__ import annotations

from .client import NiceCloudBootstrapClient
from .inspection import bind_scope, inspect_macro_inventory


class NiceCloudAutomationInspector(NiceCloudBootstrapClient):
    async def async_inspect(self, login, target_mac):
        token = None
        try:
            token = await self._async_login(login)
            self._active_token = token
            payload = await self._async_macro_user(token)
            scope = bind_scope(payload, target_mac)
            # This is the app's main scene/rule inventory source. The separate
            # /scene and /rules calls are used around CORE synchronization and
            # are not a universal empty-inventory probe for every accessory.
            return inspect_macro_inventory(payload, scope)
        finally:
            if token is not None:
                token.clear()
            self._active_token = None
            login.clear()

"""One-shot read-only automation inspection, independent of the NHK router."""

from __future__ import annotations

import aiohttp

from ..errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudConnectionError,
    NiceCloudSchemaError,
)
from .client import (
    MYNICE_HEADERS,
    REQUEST_TIMEOUT,
    NiceCloudBootstrapClient,
    _async_json_object,
)
from .inspection import (
    RecordCollection,
    bind_scope,
    inspection_now,
    parse_rules,
    parse_scenes,
)

READ_PATHS = frozenset({"api/v1/rules", "api/v1/scene"})


class NiceCloudAutomationInspector(NiceCloudBootstrapClient):
    async def async_inspect(self, login, target_mac):
        token = None
        try:
            token = await self._async_login(login)
            self._active_token = token
            scope = bind_scope(await self._async_macro_user(token), target_mac)
            scenes = RecordCollection(
                "scenes", "unavailable", error_type="RequestFailed"
            )
            rules = RecordCollection("rules", "unavailable", error_type="RequestFailed")
            timers = RecordCollection(
                "timers", "unavailable", error_type="RequestFailed"
            )
            try:
                scenes = parse_scenes(
                    await self._get_records("api/v1/scene", token, scope.target_mac),
                    scope,
                )
            except (
                NiceCloudConnectionError,
                NiceCloudAccessError,
                NiceCloudSchemaError,
            ):
                pass
            try:
                rules, timers = parse_rules(
                    await self._get_records("api/v1/rules", token, scope.target_mac),
                    scope,
                )
            except (
                NiceCloudConnectionError,
                NiceCloudAccessError,
                NiceCloudSchemaError,
            ):
                pass
            return inspection_now(scope, scenes, rules, timers)
        finally:
            if token is not None:
                token.clear()
            self._active_token = None
            login.clear()

    async def _get_records(self, path, token, target_mac):
        if path not in READ_PATHS:
            raise NiceCloudAccessError(
                "This inspector only permits scoped automation reads"
            )
        try:
            async with self._session.get(
                self._url(path),
                params={"accessoryMacAddress": target_mac},
                headers={
                    **MYNICE_HEADERS,
                    "Accept": "application/json",
                    "Authorization": token.authorization,
                },
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise NiceCloudAuthError("The cloud token expired or was rejected")
                if response.status == 403:
                    raise NiceCloudAccessError(
                        "This cloud collection is not accessible"
                    )
                if response.status != 200:
                    raise NiceCloudConnectionError(
                        "The cloud inspection service did not return a collection"
                    )
                return await _async_json_object(response)
        except (TimeoutError, aiohttp.ClientError) as error:
            raise NiceCloudConnectionError(
                "The cloud inspection service could not be reached"
            ) from error

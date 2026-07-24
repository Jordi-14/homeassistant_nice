"""HTTPS client for the optional one-time MyNice credential bootstrap."""

from __future__ import annotations

from collections.abc import Mapping
import json
from typing import Any

import aiohttp

from ..errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudConnectionError,
    NiceCloudSchemaError,
)
from .models import (
    NiceCloudBootstrapResult,
    NiceCloudLogin,
    TransientAccessToken,
    parse_macro_user,
)

MYNICE_BASE_URL = "https://integration.niceappdomain.com/myNiceCloud/"
TOKEN_PATH = "oauth/token"
MACRO_USER_PATH = "api/v1/macrouser/user"
MAX_RESPONSE_BYTES = 2 * 1024 * 1024
REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)


class NiceCloudBootstrapClient:
    """Fetch accessory credentials without retaining a cloud session."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        base_url: str = MYNICE_BASE_URL,
    ) -> None:
        if not base_url.startswith("https://"):
            raise ValueError("The MyNice bootstrap endpoint must use HTTPS")
        self._session = session
        self._base_url = base_url.rstrip("/") + "/"
        self._active_token: TransientAccessToken | None = None

    async def async_fetch_accessories(
        self,
        login: NiceCloudLogin,
    ) -> NiceCloudBootstrapResult:
        """Authenticate, fetch, parse, and immediately discard all secrets."""
        token: TransientAccessToken | None = None
        try:
            token = await self._async_login(login)
            self._active_token = token
            payload = await self._async_macro_user(token)
            return parse_macro_user(payload)
        finally:
            if token is not None:
                token.clear()
            self._active_token = None
            login.clear()

    async def _async_login(
        self,
        login: NiceCloudLogin,
    ) -> TransientAccessToken:
        try:
            async with self._session.post(
                self._url(TOKEN_PATH),
                params={
                    "grant_type": "password",
                    "username": login.account_username,
                    "password": login.account_password,
                },
                auth=aiohttp.BasicAuth(
                    login.oauth_client_id,
                    login.oauth_client_secret,
                ),
                headers={"Accept": "application/json"},
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status in {400, 401}:
                    raise NiceCloudAuthError(
                        "The cloud account or OAuth application credentials were rejected"
                    )
                if response.status == 403:
                    raise NiceCloudAccessError(
                        "The OAuth application is not allowed to access MyNice"
                    )
                if response.status != 200:
                    raise NiceCloudConnectionError(
                        f"The cloud authentication service returned HTTP {response.status}"
                    )
                payload = await _async_json_object(response)
        except TimeoutError as err:
            raise NiceCloudConnectionError(
                "The cloud authentication request timed out"
            ) from err
        except aiohttp.ClientError as err:
            raise NiceCloudConnectionError(
                "The cloud authentication service could not be reached"
            ) from err

        access_token = payload.get("access_token")
        token_type = payload.get("token_type", "Bearer")
        expires_in = payload.get("expires_in")
        if (
            not isinstance(access_token, str)
            or not access_token
            or not isinstance(token_type, str)
            or token_type.casefold() != "bearer"
            or not isinstance(expires_in, int | float)
        ):
            raise NiceCloudSchemaError(
                "The cloud authentication response has an unsupported shape"
            )
        if int(expires_in) <= 0:
            raise NiceCloudAuthError("The cloud service returned an expired token")
        return TransientAccessToken(
            value=access_token,
            token_type="Bearer",
            expires_in=int(expires_in),
        )

    async def _async_macro_user(
        self,
        token: TransientAccessToken,
    ) -> Mapping[str, Any]:
        try:
            async with self._session.get(
                self._url(MACRO_USER_PATH),
                headers={
                    "Accept": "application/json",
                    "Authorization": token.authorization,
                },
                timeout=REQUEST_TIMEOUT,
                allow_redirects=False,
            ) as response:
                if response.status == 401:
                    raise NiceCloudAuthError(
                        "The cloud access token expired or was rejected"
                    )
                if response.status == 403:
                    raise NiceCloudAccessError(
                        "The cloud account cannot access accessory credentials"
                    )
                if response.status != 200:
                    raise NiceCloudConnectionError(
                        f"The cloud bootstrap service returned HTTP {response.status}"
                    )
                return await _async_json_object(response)
        except TimeoutError as err:
            raise NiceCloudConnectionError(
                "The cloud bootstrap request timed out"
            ) from err
        except aiohttp.ClientError as err:
            raise NiceCloudConnectionError(
                "The cloud bootstrap service could not be reached"
            ) from err

    def _url(self, path: str) -> str:
        return self._base_url + path


async def _async_json_object(
    response: aiohttp.ClientResponse,
) -> Mapping[str, Any]:
    """Read one bounded JSON object without exposing response content."""
    if (
        response.content_length is not None
        and response.content_length > MAX_RESPONSE_BYTES
    ):
        raise NiceCloudSchemaError("The cloud response is too large")
    chunks: list[bytes] = []
    size = 0
    async for chunk in response.content.iter_chunked(64 * 1024):
        size += len(chunk)
        if size > MAX_RESPONSE_BYTES:
            raise NiceCloudSchemaError("The cloud response is too large")
        chunks.append(chunk)
    raw = b"".join(chunks)
    try:
        payload = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as err:
        raise NiceCloudSchemaError("The cloud response is not valid JSON") from err
    if not isinstance(payload, Mapping):
        raise NiceCloudSchemaError("The cloud response is not a JSON object")
    return payload

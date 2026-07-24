"""Tests for one-time MyNice credential bootstrap."""

from __future__ import annotations

import json
from typing import Any

import pytest

from custom_components.nice_bidiwifi.cloud.client import (
    NiceCloudBootstrapClient,
)
from custom_components.nice_bidiwifi.cloud.models import (
    NiceCloudLogin,
    parse_macro_user,
)
from custom_components.nice_bidiwifi.const import CONF_TARGET_MAC
from custom_components.nice_bidiwifi.errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudSchemaError,
)
from custom_components.nice_bidiwifi.models.config import (
    ConnectionMode,
    NiceEntryConfig,
)

PASSWORD = "AB" * 32


def _credential(
    *,
    mac: str = "AA:BB:CC:DD:EE:FF",
    name: str = "Driveway",
    **overrides: Any,
) -> dict[str, Any]:
    value: dict[str, Any] = {
        "accessoryMacAddress": mac,
        "accessoryPassword": PASSWORD,
        "accessoryUser": "nhk-user",
        "controllerID": "controller-id",
        "description": name,
    }
    value.update(overrides)
    return value


def _macro_payload(*credentials: object) -> dict[str, Any]:
    return {
        "result": True,
        "data": {
            "smartDevices": [
                {"accessoryCredentials": list(credentials)}
            ]
        },
    }


def test_parse_current_macro_user_response() -> None:
    """Test current accessory credentials become bounded typed models."""
    result = parse_macro_user(_macro_payload(_credential()))

    assert result.skipped_records == 0
    assert len(result.accessories) == 1
    accessory = result.accessories[0]
    assert accessory.name == "Driveway"
    assert accessory.identity == "AA:BB:CC:DD:EE:FF"
    assert accessory.username == "nhk-user"
    assert accessory.password == PASSWORD
    assert accessory.source_id == "controller-id"


def test_parser_skips_incomplete_and_duplicate_records() -> None:
    """Test one malformed record does not hide other usable accessories."""
    missing_password = _credential(mac="11:22:33:44:55:66")
    missing_password.pop("accessoryPassword")
    result = parse_macro_user(
        _macro_payload(
            missing_password,
            _credential(),
            _credential(name="Duplicate"),
        )
    )

    assert len(result.accessories) == 1
    assert result.skipped_records == 2


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": True, "data": {}},
        {"result": True, "data": {"smartDevices": {}}},
        _macro_payload({"description": "Incomplete"}),
        {"result": False, "data": {"smartDevices": []}},
    ],
)
def test_parser_reports_missing_or_changed_schema(payload: object) -> None:
    """Test missing required structures produce a clear schema failure."""
    with pytest.raises(NiceCloudSchemaError):
        parse_macro_user(payload)


def test_entry_builder_preserves_identity_across_connection_modes() -> None:
    """Test bootstrap data feeds the same runtime model in every route mode."""
    accessory = parse_macro_user(_macro_payload(_credential())).accessories[0]
    mappings = (
        accessory.entry_data(
            mode=ConnectionMode.LOCAL_ONLY,
            host="192.0.2.10",
        ),
        accessory.entry_data(
            mode=ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK,
            host="192.0.2.10",
            relay_host="relay.example",
        ),
        accessory.entry_data(
            mode=ConnectionMode.CLOUD_ONLY,
            relay_host="relay.example",
        ),
    )

    configs = tuple(NiceEntryConfig.from_mapping(data) for data in mappings)
    assert {data[CONF_TARGET_MAC] for data in mappings} == {
        "AA:BB:CC:DD:EE:FF"
    }
    assert {config.credentials for config in configs} == {
        configs[0].credentials
    }
    assert {config.name for config in configs} == {"Driveway"}
    assert {config.device_id for config in configs} == {1}


def test_transient_login_can_be_cleared_and_has_safe_repr() -> None:
    """Test account and OAuth secrets are releasable and omitted from repr."""
    login = NiceCloudLogin("account", "account-secret", "client", "client-secret")

    assert "account-secret" not in repr(login)
    assert "client-secret" not in repr(login)
    login.clear()
    assert not login.account_username
    assert not login.account_password
    assert not login.oauth_client_id
    assert not login.oauth_client_secret


class _FakeContent:
    def __init__(self, payload: bytes) -> None:
        self._payload = payload

    async def iter_chunked(self, limit: int):
        for offset in range(0, len(self._payload), limit):
            yield self._payload[offset : offset + limit]


class _FakeResponse:
    def __init__(self, status: int, payload: object) -> None:
        raw = json.dumps(payload).encode()
        self.status = status
        self.content_length = len(raw)
        self.content = _FakeContent(raw)

    async def __aenter__(self) -> _FakeResponse:
        return self

    async def __aexit__(self, *args: object) -> None:
        return None


class _FakeSession:
    def __init__(
        self,
        token_response: _FakeResponse,
        macro_response: _FakeResponse | None = None,
    ) -> None:
        self.token_response = token_response
        self.macro_response = macro_response
        self.post_kwargs: dict[str, Any] = {}
        self.get_kwargs: dict[str, Any] = {}
        self.get_calls = 0

    def post(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.post_kwargs = {"url": url, **kwargs}
        return self.token_response

    def get(self, url: str, **kwargs: Any) -> _FakeResponse:
        self.get_calls += 1
        self.get_kwargs = {"url": url, **kwargs}
        assert self.macro_response is not None
        return self.macro_response


def _login() -> NiceCloudLogin:
    return NiceCloudLogin("account", "account-secret", "client", "client-secret")


async def test_client_fetches_once_and_discards_all_cloud_secrets() -> None:
    """Test a successful bootstrap leaves no account or token session behind."""
    session = _FakeSession(
        _FakeResponse(
            200,
            {
                "access_token": "short-lived-token",
                "token_type": "bearer",
                "expires_in": 60,
            },
        ),
        _FakeResponse(200, _macro_payload(_credential())),
    )
    login = _login()
    client = NiceCloudBootstrapClient(session)  # type: ignore[arg-type]

    result = await client.async_fetch_accessories(login)

    assert len(result.accessories) == 1
    assert client._active_token is None
    assert not login.account_password
    assert not login.oauth_client_secret
    assert session.get_calls == 1
    assert (
        session.get_kwargs["headers"]["Authorization"]
        == "Bearer short-lived-token"
    )


async def test_client_reports_expired_token_without_fetching_account_data() -> None:
    """Test an already expired token stops before the macro-user request."""
    session = _FakeSession(
        _FakeResponse(
            200,
            {
                "access_token": "expired-token",
                "token_type": "Bearer",
                "expires_in": 0,
            },
        )
    )
    login = _login()
    client = NiceCloudBootstrapClient(session)  # type: ignore[arg-type]

    with pytest.raises(NiceCloudAuthError):
        await client.async_fetch_accessories(login)

    assert session.get_calls == 0
    assert client._active_token is None
    assert not login.account_password


async def test_client_reports_access_denied_and_discards_token() -> None:
    """Test HTTP access denial is distinct and still clears transient state."""
    session = _FakeSession(
        _FakeResponse(
            200,
            {
                "access_token": "denied-token",
                "token_type": "Bearer",
                "expires_in": 60,
            },
        ),
        _FakeResponse(403, {}),
    )
    login = _login()
    client = NiceCloudBootstrapClient(session)  # type: ignore[arg-type]

    with pytest.raises(NiceCloudAccessError):
        await client.async_fetch_accessories(login)

    assert client._active_token is None
    assert not login.account_password

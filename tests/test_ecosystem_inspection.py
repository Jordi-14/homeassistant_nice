"""Scoped cloud reads, unknown-state handling, privacy and no vendor mutations."""

import asyncio
from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.cloud.audit_client import (
    NiceCloudAutomationInspector,
)
from custom_components.nice_bidiwifi.cloud.inspection import (
    bind_scope,
    inspection_now,
    parse_rules,
    parse_scenes,
)
from custom_components.nice_bidiwifi.cloud.models import (
    NiceCloudLogin,
    TransientAccessToken,
)
from custom_components.nice_bidiwifi.ecosystem_catalog import (
    DISPOSITIONS,
    require_inspection,
)
from custom_components.nice_bidiwifi.errors import (
    NiceCloudAccessError,
    NiceCloudAuthError,
    NiceCloudSchemaError,
)
from custom_components.nice_bidiwifi.feature_catalog import FeatureBlocked
from tests.test_cloud_bootstrap import _FakeResponse
from tests.test_coordinator import _coordinator

MAC = "AA:BB:CC:DD:EE:FF"
OTHER_MAC = "11:22:33:44:55:66"


def macro(mac=MAC):
    return {
        "result": True,
        "data": {
            "user": {"id": 7, "email": "private@example.invalid"},
            "homes": [
                {
                    "id": 10,
                    "homeName": "Private home",
                    "street": "Private address",
                    "users": [
                        {
                            "id": 7,
                            "userRoleHome": "ADMIN",
                            "email": "private@example.invalid",
                        }
                    ],
                    "accessories": [
                        {
                            "accessoryMacAddress": mac,
                            "cloudSecurePassword": "secret",
                            "setupCode": "123-456",
                        }
                    ],
                }
            ],
        },
    }


def scene_payload(mac=MAC):
    return {
        "result": True,
        "scenes": [
            {
                "id": 31,
                "accessoryMacAddress": mac,
                "sceneName": "Private Scene",
                "status": "1",
                "inputOutput": "private-payload",
            }
        ],
    }


def rule_payload(mac=MAC):
    return {
        "result": True,
        "rules": {
            "rule": [
                {
                    "id": 41,
                    "accessoryMacAddress": mac,
                    "ruleName": "Private Rule",
                    "ruleStatus": "unknown-vendor-code",
                    "ruleUser": "private@example.invalid",
                }
            ],
            "timers": [
                {
                    "id": "51",
                    "ruleId": 41,
                    "timerStatus": "1",
                    "scheduledTime": "06:42",
                    "timerRepeat": "private-repeat",
                    "dayTime": "DAWN",
                }
            ],
        },
    }


def snapshot():
    scope = bind_scope(macro(), MAC)
    rules, timers = parse_rules(rule_payload(), scope)
    return inspection_now(scope, parse_scenes(scene_payload(), scope), rules, timers)


class Session:
    def __init__(self, *, macro_payload=None, scenes=None, rules=None):
        self.responses = {
            "oauth/token": _FakeResponse(
                200,
                {
                    "access_token": "private-token",
                    "token_type": "Bearer",
                    "expires_in": 60,
                },
            ),
            "api/v1/macrouser/user": _FakeResponse(
                200, macro_payload if macro_payload is not None else macro()
            ),
            "api/v1/scene": scenes or _FakeResponse(200, scene_payload()),
            "api/v1/rules": rules or _FakeResponse(200, rule_payload()),
        }
        self.calls = []

    def _call(self, method, url, kwargs):
        path = next((p for p in self.responses if url.endswith(p)), None)
        assert path is not None
        assert method == "GET" or path == "oauth/token"
        assert kwargs["allow_redirects"] is False
        self.calls.append((method, path, kwargs))
        return self.responses[path]

    def post(self, url, **kwargs):
        return self._call("POST", url, kwargs)

    def get(self, url, **kwargs):
        return self._call("GET", url, kwargs)


async def test_inspection_uses_only_scoped_reads_and_clears_account_and_token():
    session = Session()
    login = NiceCloudLogin("private-account", "private-password")
    client = NiceCloudAutomationInspector(session)
    result = await client.async_inspect(login, MAC)
    assert result.state == "records_found"
    assert [(method, path) for method, path, _ in session.calls] == [
        ("POST", "oauth/token"),
        ("GET", "api/v1/macrouser/user"),
        ("GET", "api/v1/scene"),
        ("GET", "api/v1/rules"),
    ]
    for _method, path, kwargs in session.calls:
        if path in {"api/v1/scene", "api/v1/rules"}:
            assert kwargs["params"] == {"accessoryMacAddress": MAC}
    assert not login.account_password and not login.account_username
    assert client._active_token is None
    public = str(result.summary())
    assert not any(
        v in public
        for v in [
            "Private Scene",
            "Private Rule",
            "private@example",
            "06:42",
            "private-repeat",
            MAC,
            "private-token",
            "private-password",
            "Private address",
        ]
    )
    assert "Private Scene" in result.private_lines()
    assert "06:42" in result.private_lines()
    assert not result.summary()["device_execution_confirmed"]
    assert result.summary()["confirmed_conflicts"] is None


async def test_wrong_account_stops_before_device_collections():
    session = Session(macro_payload=macro(OTHER_MAC))
    login = NiceCloudLogin("account", "password")
    client = NiceCloudAutomationInspector(session)
    with pytest.raises(NiceCloudAccessError):
        await client.async_inspect(login, MAC)
    assert len(session.calls) == 2
    assert not login.account_password and client._active_token is None


async def test_auth_expiration_is_not_retried_and_does_not_query_more_resources():
    session = Session(scenes=_FakeResponse(401, {}))
    login = NiceCloudLogin("account", "password")
    client = NiceCloudAutomationInspector(session)
    with pytest.raises(NiceCloudAuthError):
        await client.async_inspect(login, MAC)
    assert len(session.calls) == 3
    assert not login.account_password and client._active_token is None


async def test_unavailable_section_is_unknown_not_false_absence():
    result = await NiceCloudAutomationInspector(
        Session(scenes=_FakeResponse(503, {}))
    ).async_inspect(NiceCloudLogin("a", "b"), MAC)
    assert result.state == "partial"
    assert result.scenes.summary()["count"] is None
    assert result.rules.summary()["count"] == 1
    assert "absence is not established" in result.private_lines()


async def test_inspector_allowlist_rejects_mutating_paths_before_http():
    session = Session()
    client = NiceCloudAutomationInspector(session)
    with pytest.raises(NiceCloudAccessError):
        await client._get_records(
            "api/v1/home", TransientAccessToken("token", "Bearer", 60), MAC
        )
    assert not session.calls


def test_empty_valid_collections_only_mean_no_records_reported_in_cloud():
    scope = bind_scope(macro(), MAC)
    rules, timers = parse_rules(
        {"result": True, "rules": {"rule": [], "timers": []}}, scope
    )
    result = inspection_now(
        scope, parse_scenes({"result": True, "scenes": []}, scope), rules, timers
    )
    assert result.state == "no_records_reported"
    assert not result.summary()["device_execution_confirmed"]
    assert not result.summary()["ha_automations_compared"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": True},
        {"result": True, "scenes": None},
        {"result": True, "scenes": [{"id": 31, "accessoryMacAddress": OTHER_MAC}]},
        {"result": True, "scenes": [{"id": 31, "accessoryMacAddress": []}]},
        {"result": True, "scenes": [], "errorCode": []},
    ],
)
def test_malformed_or_out_of_scope_scenes_are_unknown(payload):
    result = parse_scenes(payload, bind_scope(macro(), MAC))
    assert result.state == "unavailable" and result.summary()["count"] is None


def test_timers_must_bind_to_exact_rules_from_the_same_accessory():
    scope = bind_scope(macro(), MAC)
    payload = rule_payload()
    payload["rules"]["timers"][0]["ruleId"] = 99
    rules, timers = parse_rules(payload, scope)
    assert rules.summary()["count"] == 1 and timers.summary()["count"] is None
    payload = rule_payload(OTHER_MAC)
    rules, timers = parse_rules(payload, scope)
    assert rules.summary()["count"] is None and timers.summary()["count"] is None


def test_duplicate_ids_and_excess_collections_are_not_claimed_complete():
    scope = bind_scope(macro(), MAC)
    payload = scene_payload()
    payload["scenes"] *= 2
    assert parse_scenes(payload, scope).state == "unavailable"
    payload["scenes"] *= 129
    assert parse_scenes(payload, scope).state == "unavailable"


def test_scope_can_use_credential_presence_without_reading_credential_secrets():
    payload = {
        "result": True,
        "data": {
            "user": {"id": 7},
            "smartDevices": [
                {
                    "accessoryCredentials": [
                        {"accessoryMacAddress": MAC, "accessoryPassword": "not-parsed"}
                    ]
                }
            ],
        },
    }
    scope = bind_scope(payload, MAC)
    assert scope.home_id is None and scope.cloud_role == "UNKNOWN"
    assert "not-parsed" not in repr(scope)


def test_unknown_roles_do_not_become_module_or_cloud_admin():
    payload = macro()
    payload["data"]["homes"][0]["users"][0]["userRoleHome"] = "future-owner"
    assert bind_scope(payload, MAC).cloud_role == "UNKNOWN"
    payload["data"]["homes"][0]["users"][0]["userRoleHome"] = "DISABLE"
    with pytest.raises(NiceCloudAccessError):
        bind_scope(payload, MAC)


def test_real_elapsed_token_expiry_is_enforced(monkeypatch):
    token = TransientAccessToken("secret", "Bearer", 60, issued_at=100)
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.cloud.models.time.monotonic", lambda: 160
    )
    with pytest.raises(NiceCloudSchemaError, match="expired"):
        _ = token.authorization


@pytest.mark.parametrize(
    "operation",
    [item.key for item in DISPOSITIONS if item.key != "inspection"]
    + ["create_home", "create_scene", "execute_rule", "sync", "send_notification"],
)
def test_no_resource_mutation_or_execution_operation_is_available(operation):
    with pytest.raises(FeatureBlocked):
        require_inspection(operation)


async def test_runtime_keeps_inspection_separate_from_credentials_connection_and_ha_resources(
    hass, monkeypatch
):
    owner = _coordinator(hass)
    original = dict(owner.config_entry.data)
    client = type("Client", (), {"async_inspect": AsyncMock(return_value=snapshot())})()
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.ecosystem.NiceCloudAutomationInspector",
        lambda session: client,
    )
    owner.hass.config_entries.async_update_entry = lambda *a, **k: pytest.fail(
        "must not update entries"
    )
    login = NiceCloudLogin("a", "b")
    await owner.ecosystem.async_inspect(login, confirmed=True)
    assert owner.config_entry.data == original
    assert owner.ecosystem.snapshot.state == "records_found"
    assert not login.account_password
    public = str(owner.ecosystem.diagnostics())
    assert not any(
        value in public
        for value in ["Private Scene", "Private Rule", "06:42", "private-repeat", MAC]
    )


async def test_reload_cancels_own_inspection_and_clears_secrets(hass, monkeypatch):
    owner = _coordinator(hass)
    entered = asyncio.Event()

    async def inspect(login, target):
        try:
            entered.set()
            await asyncio.Event().wait()
        finally:
            login.clear()

    client = type("Client", (), {"async_inspect": staticmethod(inspect)})()
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.ecosystem.NiceCloudAutomationInspector",
        lambda session: client,
    )
    login = NiceCloudLogin("a", "b")
    task = asyncio.create_task(owner.ecosystem.async_inspect(login, confirmed=True))
    await entered.wait()
    await owner.ecosystem.async_close()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not login.account_password and owner.ecosystem.snapshot is None


def test_nullable_sdk_result_flag_still_requires_valid_scoped_collections():
    scope = bind_scope(macro(), MAC)
    payload = scene_payload()
    payload.pop("result")
    assert parse_scenes(payload, scope).summary()["count"] == 1
    payload["result"] = False
    assert parse_scenes(payload, scope).summary()["count"] is None


def test_non_string_role_stays_unknown_instead_of_granting_authority():
    payload = macro()
    payload["data"]["homes"][0]["users"][0]["userRoleHome"] = ["ADMIN"]
    assert bind_scope(payload, MAC).cloud_role == "UNKNOWN"

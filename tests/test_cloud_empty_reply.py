"""Live b10 no-record report: distinguish positive empty replies from failure."""

from unittest.mock import AsyncMock

import pytest

from custom_components.nice_bidiwifi.cloud.inspection import (
    bind_scope,
    collection_shape,
    inspection_now,
    parse_rules,
    parse_scenes,
)
from custom_components.nice_bidiwifi.cloud.models import NiceCloudLogin
from tests.test_coordinator import _coordinator
from tests.test_ecosystem_inspection import MAC, macro, snapshot


def test_explicit_successful_null_collections_are_reported_empty():
    scope = bind_scope(macro(), MAC)
    scenes = parse_scenes({"result": True, "scenes": None}, scope)
    rules, timers = parse_rules({"result": True, "rules": None}, scope)
    result = inspection_now(scope, scenes, rules, timers)
    assert result.state == "no_records_reported"
    assert all(section.summary()["count"] == 0 for section in [scenes, rules, timers])
    assert not result.summary()["device_execution_confirmed"]


@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"result": True},
        {"result": False, "scenes": None},
        {"scenes": None},
        {"result": True, "scenes": None, "errorCode": "rejected"},
    ],
)
def test_missing_or_rejected_collections_never_become_zero(payload):
    assert parse_scenes(payload, bind_scope(macro(), MAC)).summary()["count"] is None


def test_nullable_rule_and_timer_fields_require_positive_success():
    scope = bind_scope(macro(), MAC)
    rules, timers = parse_rules(
        {"result": True, "rules": {"rule": None, "timers": None}}, scope
    )
    assert rules.summary()["count"] == timers.summary()["count"] == 0
    rules, timers = parse_rules({"rules": {"rule": None, "timers": None}}, scope)
    assert rules.summary()["count"] is timers.summary()["count"] is None


def test_shape_evidence_cannot_export_arbitrary_keys_or_values():
    shape = collection_shape(
        {
            "result": False,
            "scenes": None,
            "rules": {"rule": [], "timers": None, "secret-name": "private-password"},
            "private-email@example.invalid": "secret",
            "errorCode": "private-password",
            "message": "private-name",
        }
    )
    assert shape["scenes"] == "null" and shape["rules.rule"] == "list"
    assert shape["result"] == "false" and shape["error_code_present"] is True
    assert not any(
        x in str(shape) for x in ["private", "secret-name", "message", "password"]
    )


async def test_all_unknown_collections_are_failed_and_do_not_claim_no_error(
    hass, monkeypatch
):
    owner = _coordinator(hass)
    scope = bind_scope(macro(), MAC)
    rules, timers = parse_rules({}, scope)
    failed = inspection_now(scope, parse_scenes({}, scope), rules, timers)
    assert failed.state == "failed"
    client = type("Client", (), {"async_inspect": AsyncMock(return_value=failed)})()
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.ecosystem.NiceCloudAutomationInspector",
        lambda session: client,
    )
    await owner.ecosystem.async_inspect(NiceCloudLogin("a", "b"), confirmed=True)
    assert owner.ecosystem.last_error_type == "CollectionsUnavailable"
    assert owner.ecosystem.snapshot is failed
    old = owner.ecosystem.snapshot = snapshot()
    await owner.ecosystem.async_inspect(NiceCloudLogin("a", "b"), confirmed=True)
    assert owner.ecosystem.snapshot is old
    assert owner.ecosystem.snapshot.captured_at == old.captured_at

"""Preserve cloud identifiers and recognize only exact source-declared empty errors."""

import pytest

from custom_components.nice_bidiwifi.cloud.audit_client import (
    NiceCloudAutomationInspector,
)
from custom_components.nice_bidiwifi.cloud.inspection import (
    bind_scope,
    collection_shape,
    parse_rules,
    parse_scenes,
)
from custom_components.nice_bidiwifi.cloud.models import NiceCloudLogin
from tests.test_ecosystem_inspection import (
    MAC,
    Session,
    macro,
    rule_payload,
    scene_payload,
)


@pytest.mark.parametrize(
    "identifier",
    ["aabbccddeeff", "AABBCCDDEEFF", "aa:bb:cc:dd:ee:ff", "AA-BB-CC-DD-EE-FF"],
)
async def test_cloud_queries_keep_server_identifier_while_comparisons_stay_normalized(
    identifier,
):
    session = Session(macro_payload=macro(identifier))
    result = await NiceCloudAutomationInspector(session).async_inspect(
        NiceCloudLogin("a", "b"), MAC
    )
    assert (
        result.scope.target_mac == MAC and result.scope.request_identifier == identifier
    )
    assert [(method, path) for method, path, _ in session.calls] == [
        ("POST", "oauth/token"),
        ("GET", "api/v1/macrouser/user"),
    ]
    assert result.state == "records_found"
    assert identifier not in str(result.summary())


def test_known_error_identifiers_are_safe_but_arbitrary_messages_are_not_exported():
    shape = collection_shape(
        {
            "result": False,
            "errorCode": "404",
            "message": "RECORD_NOT_FOUND",
            "scenes": [],
        }
    )
    assert shape["recognized_error_code"] == "404"
    assert shape["recognized_error_description"] == "RECORD_NOT_FOUND"
    assert shape["scenes_length"] == 0
    shape = collection_shape(
        {"result": False, "errorCode": "private-account-id", "message": "private-name"}
    )
    assert (
        shape["recognized_error_code"]
        == shape["recognized_error_description"]
        == "unrecognized"
    )
    assert "private" not in str(shape)


@pytest.mark.parametrize(
    "code", ["404", 404, "NOT_FOUND", "ERROR_GENERIC", "RECORD_NOT_FOUND"]
)
def test_specific_record_not_found_with_explicit_empty_lists_reports_empty(code):
    scope = bind_scope(macro(), MAC)
    root = {"result": False, "errorCode": code, "message": "RECORD_NOT_FOUND"}
    scenes = parse_scenes(root | {"scenes": []}, scope)
    rules, timers = parse_rules(root | {"rules": {"rule": [], "timers": []}}, scope)
    assert (
        scenes.summary()["count"]
        == rules.summary()["count"]
        == timers.summary()["count"]
        == 0
    )
    assert scenes.response_shape["empty_reply_basis"] == "vendor_record_not_found"


@pytest.mark.parametrize(
    ("code", "description"),
    [
        ("403", "PERMISSION_DENIED"),
        ("404", "ACCESSORY_NOT_FOUND"),
        ("500", "INTERNAL_SERVER_ERROR"),
        ("ERROR_GENERIC", "unrecognized"),
        ("404", "unrecognized"),
    ],
)
def test_generic_failures_and_missing_accessories_do_not_report_zero(code, description):
    scope = bind_scope(macro(), MAC)
    root = {"result": False, "errorCode": code, "message": description}
    assert parse_scenes(root | {"scenes": []}, scope).summary()["count"] is None
    rules, timers = parse_rules(root | {"rules": {"rule": [], "timers": []}}, scope)
    assert rules.summary()["count"] is timers.summary()["count"] is None


def test_record_not_found_with_nonempty_or_missing_lists_is_not_accepted():
    scope = bind_scope(macro(), MAC)
    root = {"result": False, "errorCode": "404", "message": "RECORD_NOT_FOUND"}
    assert (
        parse_scenes(root | {"scenes": scene_payload()["scenes"]}, scope).summary()[
            "count"
        ]
        is None
    )
    assert parse_scenes(root, scope).summary()["count"] is None
    rules, timers = parse_rules(root | {"rules": rule_payload()["rules"]}, scope)
    assert rules.summary()["count"] is timers.summary()["count"] is None


def test_contradictory_denial_description_does_not_become_empty():
    scope = bind_scope(macro(), MAC)
    root = {
        "result": False,
        "errorCode": "RECORD_NOT_FOUND",
        "message": "ACCESSORY_NOT_FOUND",
        "scenes": [],
    }
    assert parse_scenes(root, scope).summary()["count"] is None

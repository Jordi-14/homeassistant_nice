"""Read the app's actual home inventory without querying CORE-only list paths."""

import pytest

from custom_components.nice_bidiwifi.cloud.audit_client import (
    NiceCloudAutomationInspector,
)
from custom_components.nice_bidiwifi.cloud.inspection import (
    bind_scope,
    inspect_macro_inventory,
)
from custom_components.nice_bidiwifi.cloud.models import NiceCloudLogin
from tests.test_cloud_bootstrap import _FakeResponse
from tests.test_ecosystem_inspection import (
    MAC,
    OTHER_MAC,
    Session,
    macro,
    rule_payload,
    scene_payload,
)


@pytest.mark.parametrize("empty", [None, []])
async def test_empty_macro_inventory_reports_zero_without_failing_per_accessory_requests(
    empty,
):
    payload = macro()
    home = payload["data"]["homes"][0]
    home["scenes"] = home["rules"] = empty
    session = Session(
        macro_payload=payload,
        scenes=_FakeResponse(200, {"result": False, "errorCode": "unexpected"}),
        rules=_FakeResponse(200, {"result": False, "errorCode": "unexpected"}),
    )
    result = await NiceCloudAutomationInspector(session).async_inspect(
        NiceCloudLogin("a", "b"), MAC
    )
    assert result.state == "no_records_reported"
    assert all(
        section.summary()["count"] == 0
        for section in [result.scenes, result.rules, result.timers]
    )
    assert len(session.calls) == 2
    assert result.scenes.response_shape["inventory_source"] == "macro_user_home"


def test_missing_macro_fields_are_unknown_not_assumed_empty():
    payload = macro()
    home = payload["data"]["homes"][0]
    home.pop("scenes")
    home.pop("rules")
    result = inspect_macro_inventory(payload, bind_scope(payload, MAC))
    assert result.state == "failed"
    assert all(
        section.summary()["count"] is None
        for section in [result.scenes, result.rules, result.timers]
    )


def test_home_inventory_is_filtered_to_current_accessory_and_matching_parent_rules():
    payload = macro()
    home = payload["data"]["homes"][0]
    other_scene = scene_payload(OTHER_MAC)["scenes"][0]
    other_scene["id"] = 32
    other_scene["sceneName"] = "Other private scene"
    home["scenes"].append(other_scene)
    other_group = rule_payload(OTHER_MAC)["rules"]
    other_group["rule"][0]["id"] = 42
    other_group["timers"][0]["id"] = "52"
    other_group["timers"][0]["ruleId"] = 42
    home["rules"].append(other_group)
    result = inspect_macro_inventory(payload, bind_scope(payload, MAC))
    assert (
        result.scenes.summary()["count"]
        == result.rules.summary()["count"]
        == result.timers.summary()["count"]
        == 1
    )
    assert "Other private scene" not in result.private_lines()
    assert result.timers.items[0].parent_rule_id == 41


def test_orphan_timer_or_missing_accessory_scope_does_not_become_false_absence():
    payload = macro()
    payload["data"]["homes"][0]["rules"][0]["timers"][0]["ruleId"] = 999
    result = inspect_macro_inventory(payload, bind_scope(payload, MAC))
    assert (
        result.rules.summary()["count"] is None
        and result.timers.summary()["count"] is None
    )
    payload = macro()
    payload["data"]["homes"][0]["scenes"][0].pop("accessoryMacAddress")
    result = inspect_macro_inventory(payload, bind_scope(payload, MAC))
    assert result.scenes.summary()["count"] is None


def test_other_account_homes_do_not_contribute_current_gateway_records():
    payload = macro()
    payload["data"]["homes"][0]["scenes"] = []
    payload["data"]["homes"][0]["rules"] = []
    other = macro()["data"]["homes"][0]
    other["id"] = 11
    other["accessories"][0]["accessoryMacAddress"] = OTHER_MAC
    payload["data"]["homes"].append(other)
    result = inspect_macro_inventory(payload, bind_scope(payload, MAC))
    assert result.state == "no_records_reported"

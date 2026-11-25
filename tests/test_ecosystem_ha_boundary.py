"""HA owns automation and organization; category routing never fabricates controls."""

import asyncio
from dataclasses import replace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.components.cover import CoverDeviceClass
from homeassistant.data_entry_flow import FlowResultType

from custom_components.nice_bidiwifi.cloud.models import NiceCloudLogin
from custom_components.nice_bidiwifi.cloud.session import ephemeral_cloud_session
from custom_components.nice_bidiwifi.cover import NiceBidiCover
from custom_components.nice_bidiwifi.ecosystem_catalog import (
    blocks_gate_adapter,
    category_for,
    require_gate_adapter,
)
from custom_components.nice_bidiwifi.entities.ecosystem import (
    NiceAutomationInspectionSensor,
)
from custom_components.nice_bidiwifi.errors import NiceParameterError
from custom_components.nice_bidiwifi.feature_catalog import (
    FEATURES_BY_KEY,
    FeatureBlocked,
)
from custom_components.nice_bidiwifi.options_flow import NiceAccessOptionsFlow
from tests.conftest import config_entry, make_device_info
from tests.test_coordinator import _coordinator
from tests.test_ecosystem_inspection import snapshot


def flow_for(hass):
    owner = _coordinator(hass)
    flow = NiceAccessOptionsFlow(config_entry())
    flow.hass = hass
    flow._owner = lambda: owner
    return flow, owner


async def test_opening_boundary_menu_never_logs_into_cloud_or_changes_resources(hass):
    flow, owner = flow_for(hass)
    owner.ecosystem.async_inspect = AsyncMock()
    result = await flow.async_step_ecosystem()
    assert result["type"] is FlowResultType.MENU
    await flow.async_step_ecosystem_ha()
    result = await flow.async_step_ecosystem_report()
    assert result["description_placeholders"]["state"] == "not_inspected"
    owner.ecosystem.async_inspect.assert_not_awaited()


async def test_inspection_form_requires_consent_and_does_not_retain_submitted_secrets(
    hass,
):
    flow, owner = flow_for(hass)
    owner.ecosystem.async_inspect = AsyncMock()
    submitted = {
        "account": "private-account",
        "account_password": "private-password",
        "confirm": False,
    }
    result = await flow.async_step_ecosystem_cloud(submitted)
    assert result["errors"]["base"] == "ecosystem_consent_required"
    owner.ecosystem.async_inspect.assert_not_awaited()
    assert submitted["account"] == submitted["account_password"] == ""

    async def inspect(login, **kwargs):
        owner.ecosystem.snapshot = snapshot()

    owner.ecosystem.async_inspect = AsyncMock(side_effect=inspect)
    submitted = {
        "account": "private-account",
        "account_password": "private-password",
        "confirm": True,
    }
    result = await flow.async_step_ecosystem_cloud(submitted)
    assert result["step_id"] == "ecosystem_report"
    assert "Private Scene" in result["description_placeholders"]["records"]
    login = owner.ecosystem.async_inspect.await_args.args[0]
    assert not login.account_password
    assert submitted["account"] == submitted["account_password"] == ""


async def test_failed_cloud_inspection_preserves_cached_capture_age_and_local_configuration(
    hass, monkeypatch
):
    owner = _coordinator(hass)
    old = owner.ecosystem.snapshot = snapshot()
    original = dict(owner.config_entry.data)
    client = type(
        "Client",
        (),
        {"async_inspect": AsyncMock(side_effect=OSError("private diagnostic text"))},
    )()
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.ecosystem.NiceCloudAutomationInspector",
        lambda session: client,
    )
    with pytest.raises(OSError):
        await owner.ecosystem.async_inspect(NiceCloudLogin("a", "b"), confirmed=True)
    assert owner.ecosystem.snapshot is old
    assert owner.ecosystem.snapshot.captured_at == old.captured_at
    assert owner.ecosystem.last_error_type == "OSError"
    assert owner.config_entry.data == original
    assert "private diagnostic text" not in str(owner.ecosystem.diagnostics())


async def test_obsolete_cloud_capture_is_discarded_after_reload_generation_change(
    hass, monkeypatch
):
    owner = _coordinator(hass)
    entered, release = asyncio.Event(), asyncio.Event()

    async def inspect(login, target):
        entered.set()
        await release.wait()
        return snapshot()

    client = type("Client", (), {"async_inspect": staticmethod(inspect)})()
    monkeypatch.setattr(
        "custom_components.nice_bidiwifi.ecosystem.NiceCloudAutomationInspector",
        lambda session: client,
    )
    task = asyncio.create_task(
        owner.ecosystem.async_inspect(NiceCloudLogin("a", "b"), confirmed=True)
    )
    await entered.wait()
    owner.observations.invalidate()
    release.set()
    with pytest.raises(Exception, match="obsolete"):
        await task
    assert owner.ecosystem.snapshot is None


async def test_account_session_uses_no_cookie_jar_and_is_detached(hass):
    with ephemeral_cloud_session(hass) as session:
        assert isinstance(session.cookie_jar, aiohttp.DummyCookieJar)
        assert not session.closed
    assert session.closed


async def test_optional_sensor_exports_no_private_names_times_or_ids(hass):
    owner = _coordinator(hass)
    entity = NiceAutomationInspectionSensor(owner, config_entry())
    assert entity.native_value == "not_inspected"
    assert not entity.entity_registry_enabled_default
    owner.ecosystem.snapshot = snapshot()
    assert entity.native_value == "records_found"
    assert "Private Scene" not in str(entity.extra_state_attributes)
    assert "06:42" not in str(entity.extra_state_attributes)
    assert entity.unique_id.endswith("_nice_automation_inspection")


@pytest.mark.parametrize(
    "kind",
    [
        "DIMMER.LIGHTS",
        "RELAY.LIGHTS",
        "RELAY.POWERLOAD",
        "RELAY.ELECTRICAL_LOCK",
        "SCREEN.BLIND",
    ],
)
def test_known_non_gate_categories_do_not_inherit_gate_controls(kind):
    info = replace(make_device_info(), device_type=kind)
    assert blocks_gate_adapter(info)
    with pytest.raises(NiceParameterError):
        require_gate_adapter(info)
    category = category_for(kind)
    assert category.ha_domain in {"light", "switch", "lock", "cover"}
    assert category.gap == "ecosystem.device_adapter"


@pytest.mark.parametrize(
    ("kind", "device_class"),
    [
        ("sliding", CoverDeviceClass.GATE),
        ("swing", CoverDeviceClass.GATE),
        ("sectional", CoverDeviceClass.GARAGE),
        ("unrecognized-legacy", CoverDeviceClass.GATE),
    ],
)
def test_cover_ids_and_gate_compatibility_survive_category_metadata(
    hass, kind, device_class
):
    owner = _coordinator(hass)
    owner.device_info = replace(make_device_info(), device_type=kind)
    entity = NiceBidiCover(owner, config_entry())
    assert entity.device_class == device_class
    assert entity.unique_id == "aabbccddeeff_1_cover"
    require_gate_adapter(owner.device_info)


@pytest.mark.parametrize(
    "key",
    [
        "areas",
        "dashboards",
        "groups",
        "scenes",
        "schedules",
        "notifications",
        "permissions",
        "sharing",
        "vendor_resources",
        "service_linking",
    ],
)
def test_native_and_manufacturer_functions_are_deliberate_dispositions_not_missing_operations(
    key,
):
    feature = FEATURES_BY_KEY["ecosystem." + key]
    assert str(feature.implementation) in {"ha_native", "manufacturer_app"}
    with pytest.raises(FeatureBlocked, match="managed by"):
        feature.require_implemented()


async def test_controller_modes_use_only_existing_observations_and_never_guess_off(
    hass,
):
    flow, owner = flow_for(hass)
    before = owner.ecosystem.controller_behavior()
    assert before["settings"]["automatic_close"]["enabled_reported"] is None
    owner.parameters.observe(owner.parameters.address(4, 0x80), b"\x01")
    owner.parameters.observe(owner.parameters.address(4, 0x84), b"\x00")
    owner.parameters.observe(owner.parameters.address(4, 0x88), b"\x02")
    result = await flow.async_step_ecosystem_controller()
    assert result["step_id"] == "ecosystem_controller"
    modes = owner.ecosystem.controller_behavior()
    assert modes["settings"]["automatic_close"]["enabled_reported"] is True
    assert modes["settings"]["close_after_photocell"]["enabled_reported"] is False
    assert modes["settings"]["always_close"]["enabled_reported"] is None
    assert not modes["new_reads_performed"] and not modes["settings_modified"]


async def test_unverified_core_readback_stops_before_any_transport(hass):
    from custom_components.nice_bidiwifi.ecosystem_catalog import (
        CoreAutomationInspectionAdapter,
    )
    from custom_components.nice_bidiwifi.errors import NiceUnsupportedError

    flow, owner = flow_for(hass)
    with pytest.raises(NiceUnsupportedError):
        await CoreAutomationInspectionAdapter().read("unresolved-target")
    result = await flow.async_step_ecosystem_core()
    assert result["reason"] == "ecosystem_core_unverified"


async def test_non_cover_metadata_suppresses_a_gate_entity_even_with_generic_action_advertisement(
    hass,
):
    from custom_components.nice_bidiwifi.cover import COVERS
    from custom_components.nice_bidiwifi.entities.factory import (
        EntitySupport,
        entity_support,
    )

    owner = _coordinator(hass)
    owner._store_device_info(replace(make_device_info(), device_type="RELAY.LIGHTS"))
    owner.capabilities = replace(owner.capabilities, high_level_actions=True)
    assert entity_support(owner, COVERS[0]) is EntitySupport.UNSUPPORTED
    with pytest.raises(NiceParameterError):
        owner._control_dispatch_guard("open")


async def test_optional_cloud_failure_is_not_device_unavailability(hass):
    owner = _coordinator(hass)
    entity = NiceAutomationInspectionSensor(owner, config_entry())
    owner.ecosystem.last_error_type = "NiceCloudAuthError"
    assert entity.native_value == "failed"
    assert owner.ecosystem.snapshot is None


async def test_known_non_gate_category_cannot_offer_legacy_t4_gate_buttons(hass):
    owner = _coordinator(hass)
    owner.device_info = replace(make_device_info(), device_type="DIMMER.LIGHTS")
    assert not owner.t4_action_supported("step_step")


@pytest.mark.parametrize(
    ("kind", "expected"),
    [
        ("DOOR.DEFAULT", "door"),
        ("DOOR.SECTIONAL", "garage"),
        ("SCREEN.PATIO", None),
        ("SCREEN.MONO", None),
        ("SCREEN.SHUTTER", "shutter"),
        ("SCREEN.VENETIAN", "blind"),
    ],
)
def test_category_contract_does_not_guess_ambiguous_subtype_classes(kind, expected):
    assert category_for(kind).device_class == expected

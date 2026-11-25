"""Controller metadata, permission and HA compatibility regression coverage."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.controller_catalog import (
    ACTION_BINDINGS,
    PARAMETERS,
    PARAMETERS_BY_KEY,
    parameter_for_setting,
)
from custom_components.nice_bidiwifi.entities.configuration import (
    ConfigurationNumber,
    ConfigurationSelect,
    ConfigurationSensor,
)
from custom_components.nice_bidiwifi.errors import NiceParameterError
from custom_components.nice_bidiwifi.number import NUMBERS, NiceBidiNumber
from custom_components.nice_bidiwifi.parameters import ParameterRepository, SupportState
from custom_components.nice_bidiwifi.protocol.t4.actions import T4_ACTION_BY_KEY
from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo, decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.menu import (
    MenuEntry,
    MenuPermission,
    MenuRole,
    MenuWireDecoder,
)
from custom_components.nice_bidiwifi.protocol.t4.settings import (
    ADDITIONAL_SETTINGS,
    PAUSE_TIME,
)
from tests.conftest import FakeClient, config_entry, make_status
from tests.test_coordinator import _coordinator


def _observed(instance, definition, info, raw, index=None):
    address = instance.configuration.address(definition)
    instance.parameters.record_info(address, info)
    instance.parameters.observe(instance.configuration.address(definition, index), raw)


async def _instance(hass):
    instance = _coordinator(hass)
    instance.client = FakeClient()
    instance.async_set_updated_data(make_status(state="closed"))
    await instance.access.async_refresh()
    return instance


def test_inventory_covers_all_symbols_and_table_rows_without_using_app_ids_as_registers():
    assert len(PARAMETERS) == 358
    assert len({row.key for row in PARAMETERS}) == 358
    assert (
        sum(row.binding is not None and row.binding[0] == 4 for row in PARAMETERS)
        == 236
    )
    assert PARAMETERS_BY_KEY["ctrl_pause_time"].binding == (4, 0x81)
    assert PARAMETERS_BY_KEY["std_menu"].binding == (0, 0x10)
    assert all(
        row.binding is None for row in PARAMETERS if row.symbol.startswith("PSU_")
    )
    assert all(value in T4_ACTION_BY_KEY for value in ACTION_BINDINGS.values())
    assert len(ACTION_BINDINGS) == len(T4_ACTION_BY_KEY) == 25
    assert all(
        PARAMETERS_BY_KEY[symbol.lower()].protocol == "DEP"
        for symbol in ACTION_BINDINGS
    )


@pytest.mark.parametrize(
    "value",
    ["invalid,y,y,y,n,0,123,33028", "y,n,y,n,n,0,-1,33028", "y,n,z,n,n,0,123,33028"],
)
def test_malformed_menu_fields_never_grant_a_write(value):
    with pytest.raises(NiceParameterError):
        MenuEntry.from_decoded_fields(tuple(value.split(",")))


def test_decoded_menu_role_bits_and_wire_decoder_are_distinct():
    entry = MenuEntry.from_decoded_fields(
        ("n", "y", "y", "y", "n", "0", "123", "33028")
    )
    assert entry.binding == (4, 0x81)
    assert entry.allows(MenuRole.USER, "set") is False
    assert entry.allows(MenuRole.INSTALLER, "set") is True
    assert entry.allows(MenuRole.UNKNOWN, "set") is None
    with pytest.raises(NiceParameterError, match="Truncated"):
        MenuWireDecoder().decode(b"not a decoded native record")


@pytest.mark.parametrize("setting", ADDITIONAL_SETTINGS, ids=lambda item: item.key)
def test_additional_setters_are_blocked_without_menu_before_any_payload(setting):
    repository = ParameterRepository(1)
    with pytest.raises(NiceParameterError, match="permissions"):
        repository.prepare_write(setting, 1)


def test_future_verified_menu_evidence_is_scoped_and_revoked_on_session_change():
    repository = ParameterRepository(1)
    setting = next(item for item in ADDITIONAL_SETTINGS if item.parameter == 0x5A)
    definition = parameter_for_setting(setting.key)
    address = repository.address(4, 0x5A)
    info = decode_dmp_info(bytes.fromhex("01001111016401000a"))
    repository.record_info(address, info)
    entry = MenuEntry(False, True, True, True, False, 0, definition.app_id, 0x5A04)
    permission = MenuPermission(1, (4, 0x5A), MenuRole.INSTALLER, entry, 0)
    repository.record_menu_permission(address, permission)
    assert repository.prepare_native_write(setting, 2.5).payload == b"\x19"
    with pytest.raises(NiceParameterError, match="represented exactly"):
        repository.prepare_native_write(setting, 2.55)
    repository.invalidate_menu_permissions()
    with pytest.raises(NiceParameterError, match="permissions"):
        repository.prepare_native_write(setting, 2.5)
    with pytest.raises(NiceParameterError, match="session"):
        repository.record_menu_permission(address, permission)


async def test_read_views_keep_named_options_and_legacy_number_identity(hass):
    instance = await _instance(hass)
    definition = parameter_for_setting("photo_close_mode")
    info = decode_dmp_info(bytes.fromhex("0100f561021011"))
    _observed(instance, definition, info, b"\x11")
    select = ConfigurationSelect(instance, config_entry(), definition)
    old = NiceBidiNumber(
        instance,
        config_entry(),
        next(row for row in NUMBERS if row.setting.key == "photo_close_mode"),
    )
    assert select.current_option == "code_17"
    assert select.options == ["code_16", "code_17"]
    assert old.unique_id.endswith("_bus_t4_photo_close_mode")
    assert select.unique_id.endswith("_parameter_04_86")
    instance.async_write_setting = AsyncMock()
    await select.async_select_option(select.current_option)
    instance.async_write_setting.assert_awaited_once_with(definition.setting, 17)
    with pytest.raises(HomeAssistantError, match="not advertised"):
        await select.async_select_option("code_18")
    await instance.async_shutdown()


async def test_scaled_number_uses_exact_units_and_retains_raw_integer_legacy_api(hass):
    instance = await _instance(hass)
    definition = parameter_for_setting("pause_time")
    info = decode_dmp_info(bytes.fromhex("0100111100fa01000a"))
    _observed(instance, definition, info, b"\x14")
    entity = ConfigurationNumber(instance, config_entry(), definition)
    assert entity.native_value == 2
    assert entity.native_max_value == 25
    assert entity.native_step == 0.1
    assert entity.native_unit_of_measurement == "s"
    instance.async_write_setting = AsyncMock()
    await entity.async_set_native_value(2.5)
    instance.async_write_setting.assert_awaited_once_with(PAUSE_TIME, 25)
    with pytest.raises(NiceParameterError):
        await entity.async_set_native_value(2.55)
    old = NiceBidiNumber(
        instance,
        config_entry(),
        next(row for row in NUMBERS if row.setting.key == "pause_time"),
    )
    with pytest.raises(ValueError, match="whole number"):
        await old.async_set_native_value(2.5)
    await instance.async_shutdown()


async def test_indexed_values_stay_separate_and_never_become_a_scalar_motor_setting(
    hass,
):
    instance = await _instance(hass)
    definition = parameter_for_setting("opening_force")
    info = DmpInfo(1, 0, 0x0A, 0x81, minimum=0, maximum=100, step=1, array_size=2)
    _observed(instance, definition, info, b"\x00\x14", index=0)
    instance.parameters.observe(
        instance.configuration.address(definition, 1), b"\x01\x28"
    )
    assert instance.configuration.view(definition.key).state == "indexed"
    first = instance.configuration.view(definition.key, index=0)
    second = instance.configuration.view(definition.key, index=1)
    assert (first.value, second.value) == (20, 40)
    assert not first.writable and first.gap == "controller.index_identity"
    first_entity = ConfigurationSensor(instance, config_entry(), definition, index=0)
    second_entity = ConfigurationSensor(instance, config_entry(), definition, index=1)
    assert first_entity.unique_id != second_entity.unique_id
    assert "motor" not in first_entity.name.casefold()
    instance.async_write_setting = AsyncMock()
    with pytest.raises(HomeAssistantError, match="Indexed"):
        await instance.configuration.async_write(definition.key, 30, index=0)
    instance.async_write_setting.assert_not_called()
    await instance.async_shutdown()


async def test_blocked_catalog_paths_cannot_queue_reads_or_cancel_controls(hass):
    instance = await _instance(hass)
    instance.client.read_dmp_info = AsyncMock()
    instance.position_controller._async_cancel_position_target = AsyncMock()
    for definition in PARAMETERS:
        if not definition.safe_read:
            with pytest.raises(NiceParameterError):
                await instance.configuration.async_refresh_parameter(definition.key)
    instance.client.read_dmp_info.assert_not_called()
    instance.position_controller._async_cancel_position_target.assert_not_called()
    assert instance.tasks.pending == 0
    await instance.async_shutdown()


async def test_missing_unsupported_and_undecoded_are_different(hass):
    instance = await _instance(hass)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    address = instance.configuration.address(definition)
    assert instance.configuration.view(definition.key).state == "unavailable"
    instance.parameters.record_info(address, None, state=SupportState.UNSUPPORTED)
    assert instance.configuration.view(definition.key).state == "unsupported"
    instance.parameters.record_info(address, DmpInfo(4, 0x81, 0xE2, 0x41))
    assert instance.configuration.view(definition.key).state == "blocked"
    await instance.async_shutdown()


async def test_new_scalar_read_is_opt_in_and_cannot_teach_movement(hass):
    instance = await _instance(hass)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    instance.client.read_dmp_info = lambda *args, **kwargs: decode_dmp_info(
        bytes.fromhex("01001111016401000a")
    )
    instance.client.read_dmp_value = lambda *args, **kwargs: b"\x14"
    revision = instance.observations.revision
    await instance.configuration.async_refresh_parameter(definition.key)
    assert instance.configuration.view(definition.key).value == 2
    assert instance.configuration.view(definition.key).writable is False
    assert instance.observations.revision == revision
    assert instance.position_controller.estimator.observation is None
    await instance.async_shutdown()


async def test_diagnostics_list_actions_using_the_existing_verified_binding(hass):
    instance = await _instance(hass)
    report = instance.configuration.report()
    assert report["ctrl_open_partial_1"]["operation"] == "t4.partial_open_1"
    assert report["ctrl_open_partial_1"]["state"] == "implemented"
    assert report["ctrl_man_present_open_motor_1"]["gap"] == "controller.action_binding"
    await instance.async_shutdown()


async def test_legacy_number_uses_latest_shared_observation_without_changing_raw_units(
    hass,
):
    instance = await _instance(hass)
    definition = parameter_for_setting("pause_time")
    _observed(
        instance,
        definition,
        decode_dmp_info(bytes.fromhex("0100111100fa01000a")),
        b"\x14",
    )
    old = NiceBidiNumber(
        instance,
        config_entry(),
        next(row for row in NUMBERS if row.setting.key == "pause_time"),
    )
    new = ConfigurationNumber(instance, config_entry(), definition)
    assert old.native_value == 20
    assert new.native_value == 2
    await instance.async_shutdown()


async def test_first_class_new_setter_validates_menu_and_reads_back_without_unknown_status_fields(
    hass,
):
    from custom_components.nice_bidiwifi.models.commands import CommandReceipt

    instance = await _instance(hass)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    address = instance.configuration.address(definition)
    info = decode_dmp_info(bytes.fromhex("01001111016401000a"))
    instance.parameters.record_info(address, info)
    instance.parameters.observe(address, b"\x14")
    instance.client.read_dmp_value = lambda *args, **kwargs: b"\x19"
    sent = []

    def write(prepared, *, before_dispatch):
        before_dispatch()
        sent.append(prepared.payload)
        return CommandReceipt()

    instance.client.write_parameter = write
    with pytest.raises(HomeAssistantError, match="permissions"):
        await instance.configuration.async_write(definition.key, 2.5)
    assert not sent
    from tests.native_menu_fixtures import install_menu_fixture
    install_menu_fixture(instance)
    instance.client.dmp_info = {(4, 0x5A): info}
    await instance.controller_menu.async_refresh()
    await instance.configuration.async_write(definition.key, 2.5)
    assert sent == [b"\x19"]
    assert instance.configuration.view(definition.key).value == 2.5
    assert instance.data.state == "closed"
    assert not hasattr(instance.data, "config_electric_lock_time")
    await instance.async_shutdown()


def test_execute_and_relearning_symbols_are_never_configuration_get_candidates():
    for definition in PARAMETERS:
        if any(
            token in definition.symbol
            for token in (
                "SEARCH",
                "DELETE",
                "RESET",
                "CALIBRATION",
                "TEST",
                "CMD_",
                "ACTIVATE",
                "RESERVED",
                "PASSWORD",
            )
        ):
            assert not definition.safe_read, definition.symbol


def test_named_choices_have_valid_stable_translation_keys_in_all_languages():
    import json
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1] / "custom_components/nice_bidiwifi"
    for path in [
        root / "strings.json",
        root / "translations/en.json",
        root / "translations/ca.json",
    ]:
        data = json.loads(path.read_text())["entity"]["select"]
        for key in ["photo_close_mode", "always_close_mode"]:
            assert all(
                re.fullmatch(r"code_\d+", option) for option in data[key]["state"]
            )
        assert data["photo_close_mode"]["state"]["code_17"]


async def test_alias_names_use_ha_translations_instead_of_becoming_nameless(hass):
    from types import SimpleNamespace

    instance = await _instance(hass)
    select = ConfigurationSelect(
        instance, config_entry(), parameter_for_setting("photo_close_mode")
    )
    select.platform_data = SimpleNamespace(
        platform_name="nice_bidiwifi", domain="select"
    )
    translations = {
        "component.nice_bidiwifi.entity.select.photo_close_mode.name": "Photo close mode"
    }
    assert select._name_internal(None, translations) == "Photo close mode"
    number = ConfigurationNumber(
        instance, config_entry(), parameter_for_setting("pause_time")
    )
    number.platform_data = SimpleNamespace(
        platform_name="nice_bidiwifi", domain="number"
    )
    translations = {
        "component.nice_bidiwifi.entity.number.controller_parameter_value.name": "{parameter} (engineering units)"
    }
    assert number._name_internal(None, translations) == "Pause time (engineering units)"
    await instance.async_shutdown()


async def test_unread_parameters_are_not_starved_when_earlier_reads_become_due(
    hass, monkeypatch
):
    from custom_components.nice_bidiwifi import configuration as module

    instance = await _instance(hass)
    keys = [row.key for row in PARAMETERS if row.safe_read][:6]
    model = instance.configuration
    model._watched.update(keys)
    model._last_attempt.update({key: 0 for key in keys[:3]})
    monkeypatch.setattr(module.time, "monotonic", lambda: 1000)
    attempts = []

    async def read(key):
        attempts.append(key)
        model._last_attempt[key] = 1000

    model.async_refresh_parameter = read
    await model._refresh_watched()
    assert attempts == keys[3:]
    attempts.clear()
    await model._refresh_watched()
    assert attempts == keys[:3]
    await instance.async_shutdown()

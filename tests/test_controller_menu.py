"""Paged native menu framing, session/role boundaries and actual guarded writes."""

import time
from dataclasses import replace
from unittest.mock import Mock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.controller_catalog import PARAMETERS_BY_KEY
from custom_components.nice_bidiwifi.entities.configuration import (
    configuration_controls,
)
from custom_components.nice_bidiwifi.errors import (
    NiceParameterError,
    NiceProtocolError,
    NiceReadPreemptedError,
    NiceResponseError,
)
from custom_components.nice_bidiwifi.protocol.t4.codec import dmp_checksum
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.menu import (
    MenuRole,
    build_controller_menu_request,
    decode_menu_records,
)
from custom_components.nice_bidiwifi.protocol.t4.replies import checked_dmp_reply
from tests.conftest import FakeClient, make_device_info, make_status
from tests.native_menu_fixtures import install_menu_fixture
from tests.test_client import _client
from tests.test_coordinator import _coordinator


def menu_reply(value, *, sequence=0, final=True, target=(0, 3), status=0):
    payload = [0, 0x10, 0x19 if final else 0x18, sequence, status, *value]
    header = [0x50, 0x91, *target, 8, len(payload) + 1]
    body = [*header, dmp_checksum(*header), *payload, dmp_checksum(*payload)]
    return bytes([0x55, len(body), *body, len(body)])


def test_menu_request_matches_native_structure_and_checksum():
    raw = build_controller_menu_request(0x42)
    assert raw[9:-2] == bytes.fromhex("00 10 99 42 01 04")
    assert raw[8] == dmp_checksum(*raw[2:8])
    assert raw[-2] == dmp_checksum(*raw[9:-2])
    assert raw[1] == len(raw) - 3 == raw[-1]
    for bad in [-1, 256, True]:
        with pytest.raises(NiceParameterError):
            build_controller_menu_request(bad)


def test_sequence_is_not_a_value_length():
    raw = menu_reply(bytes.fromhex("70 5a"), sequence=42)
    parsed = checked_dmp_reply(
        raw, target=(0, 3), group=0, parameter=0x10, operation=0x19
    )
    assert parsed.sequence == 42 and parsed.value == bytes.fromhex("70 5a")
    assert parsed.finished


def test_complete_menu_is_accumulated_in_one_session(monkeypatch):
    client = _client()
    replies = iter(
        [
            menu_reply(bytes.fromhex("70 5a"), sequence=42, final=False),
            menu_reply(bytes.fromhex("50 45"), sequence=77),
        ]
    )
    requests = []

    def exchange(protocol, raw, *args):
        requests.append(raw)
        return b"<Response/>", [next(replies)]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    snapshot = client._read_controller_menu_locked()
    assert snapshot.complete and snapshot.pages == 2
    assert [row.binding for row in snapshot.records] == [(4, 0x5A), (4, 0x45)]
    assert [raw[12] for raw in requests] == [0, 42]


@pytest.mark.parametrize(
    "case",
    [
        "cycle",
        "empty_nonfinal",
        "odd",
        "wrong_target",
        "multiple",
        "page_limit",
        "oversized",
        "denied",
        "session_change",
        "queued_command",
    ],
)
def test_partial_or_unscoped_menu_never_succeeds(monkeypatch, case):
    client = _client()
    calls = []

    def exchange(*args):
        calls.append(1)
        raw = menu_reply(bytes.fromhex("70 5a"), sequence=1, final=False)
        if case == "empty_nonfinal":
            raw = menu_reply(b"", sequence=1, final=False)
        if case == "odd":
            raw = menu_reply(b"\x70")
        if case == "wrong_target":
            raw = menu_reply(b"", target=(0, 10))
        if case == "page_limit":
            raw = menu_reply(bytes.fromhex("70 5a"), sequence=len(calls), final=False)
        if case == "oversized":
            raw = None
        if case == "denied":
            raw = menu_reply(b"", status=0xF9)
        if case == "session_change":
            client.session_state.invalidate("test")
        if case == "queued_command":
            client._should_yield = lambda: True
        if case == "oversized":
            # 16 well-formed pages with 40 records exceed the total cap.
            raw = menu_reply(
                bytes.fromhex("70 5a") * 40, sequence=len(calls), final=False
            )
        return b"<Response/>", [raw, raw] if case == "multiple" else [raw]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    with pytest.raises(
        (
            NiceProtocolError,
            NiceParameterError,
            NiceReadPreemptedError,
            NiceResponseError,
            Exception,
        )
    ):
        client._read_controller_menu_locked()
    assert len(calls) <= 16


async def instance(hass):
    owner = _coordinator(hass)
    owner.client = FakeClient()
    owner.device_info = make_device_info()
    owner.async_set_updated_data(make_status(state="closed"))
    await owner.access.async_refresh()
    return owner


async def test_module_admin_and_menu_flags_do_not_grant_controller_role(hass):
    owner = await instance(hass)
    install_menu_fixture(owner)
    owner.controller_menu.role_adapter = None
    await owner.controller_menu.async_refresh()
    report = owner.controller_menu.report()
    assert report["state"] == "role_unverified"
    assert report["controller_role"] == "unknown"
    assert not report["module_admin_grants_installer"]
    setting = PARAMETERS_BY_KEY["ctrl_time_electric_lock"].setting
    with pytest.raises(NiceParameterError, match="role"):
        await owner.controller_menu.async_prepare_write(setting)
    assert owner.client.dmp_writes == []
    assert not [
        e
        for e in configuration_controls(owner, owner.config_entry, "number")
        if e.definition.setting.requires_menu_permission
    ]
    await owner.async_shutdown()


@pytest.mark.parametrize(
    "change",
    [
        "expired",
        "session",
        "firmware",
        "identity",
        "denied",
        "unknown",
        "proof_scope",
        "proof_future",
    ],
)
async def test_role_and_scope_revocation_are_checked_at_dispatch(hass, change):
    owner = await instance(hass)
    install_menu_fixture(owner)
    await owner.controller_menu.async_refresh()
    setting = PARAMETERS_BY_KEY["ctrl_time_electric_lock"].setting
    owner.controller_menu.require_permission(setting)
    if change == "expired":
        owner.controller_menu.captured -= 31
    elif change == "session":
        owner.client.reconnect_count += 1
    elif change == "firmware":
        owner.device_info = replace(owner.device_info, device_fw_version="different")
    elif change == "identity":
        owner.parameters.device_id = 2
    elif change in {"denied", "unknown"}:
        owner.controller_menu.proof = replace(
            owner.controller_menu.proof,
            role=MenuRole.USER if change == "denied" else MenuRole.UNKNOWN,
        )
    elif change == "proof_scope":
        owner.controller_menu.proof = replace(
            owner.controller_menu.proof, session_scope=("cloud", 99)
        )
    elif change == "proof_future":
        owner.controller_menu.proof = replace(
            owner.controller_menu.proof, verified_monotonic=time.monotonic() + 100
        )
    with pytest.raises(NiceParameterError):
        owner.controller_menu.require_permission(setting)
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_incomplete_conflicting_and_late_menus_revoke_prior_grants(hass):
    owner = await instance(hass)
    install_menu_fixture(owner)
    await owner.controller_menu.async_refresh()
    snapshot = owner.controller_menu.snapshot
    for bad in [
        replace(snapshot, complete=False),
        replace(snapshot, device_id=2),
        replace(
            snapshot, records=decode_menu_records(bytes.fromhex("70 5a 00 5a"), group=4)
        ),
    ]:
        owner.client.read_controller_menu = lambda bad=bad, **kwargs: bad
        with pytest.raises(NiceParameterError):
            await owner.controller_menu.async_refresh()
        assert owner.controller_menu.snapshot is None
        assert owner.parameters._menu_permissions == {}
    owner.client.read_controller_menu = lambda **kwargs: (
        owner.observations.invalidate(close=True) or snapshot
    )
    with pytest.raises((NiceParameterError, HomeAssistantError)):
        await owner.controller_menu.async_refresh()
    assert owner.controller_menu.snapshot is None
    await owner.async_shutdown()


async def test_new_control_requires_fresh_info_and_respects_queue_revocation(hass):
    owner = await instance(hass)
    install_menu_fixture(owner)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    info = decode_dmp_info(bytes.fromhex("01001111016401000a"))
    owner.client.dmp_info = {(4, 0x5A): info}
    await owner.controller_menu.async_refresh()
    owner.parameters.record_info(owner.parameters.address(4, 0x5A), info)
    owner.parameters.observe(owner.parameters.address(4, 0x5A), b"\x14")
    assert [
        e
        for e in configuration_controls(owner, owner.config_entry, "number")
        if e.definition.key == definition.key
    ]

    def revoke(prepared, *, before_dispatch):
        owner.controller_menu.invalidate()
        before_dispatch()
        raise AssertionError("Revoked write reached transmission")

    owner.client.write_parameter = revoke
    with pytest.raises(HomeAssistantError, match="permissions"):
        await owner.configuration.async_write(definition.key, 2.5)
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_fresh_metadata_can_revoke_a_previously_scalar_write(hass):
    owner = await instance(hass)
    install_menu_fixture(owner)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    address = owner.parameters.address(4, 0x5A)
    info = decode_dmp_info(bytes.fromhex("01001111016401000a"))
    owner.parameters.record_info(address, info)
    owner.parameters.observe(address, b"\x14")
    await owner.controller_menu.async_refresh()
    # The controller's new INFO requires indexing; the earlier scalar UI is stale.
    owner.client.dmp_info = {(4, 0x5A): replace(info, flags=0x81, array_size=2)}
    with pytest.raises(HomeAssistantError, match="indexed"):
        await owner.configuration.async_write(definition.key, 2.5)
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_verified_controller_denial_also_protects_legacy_settings(hass):
    from custom_components.nice_bidiwifi.protocol.t4.settings import PAUSE_TIME

    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex("40 81"))
    await owner.controller_menu.async_refresh()
    with pytest.raises(HomeAssistantError, match="denies"):
        await owner.async_write_setting(PAUSE_TIME, 20)
    assert owner.client.dmp_writes == []
    await owner.async_shutdown()


async def test_ambiguous_additional_write_is_never_replayed(hass):
    from custom_components.nice_bidiwifi.errors import NiceBidiConnectionError

    owner = await instance(hass)
    install_menu_fixture(owner)
    definition = PARAMETERS_BY_KEY["ctrl_time_electric_lock"]
    info = decode_dmp_info(bytes.fromhex("01001111016401000a"))
    owner.client.dmp_info = {(4, 0x5A): info}
    owner.parameters.record_info(owner.parameters.address(4, 0x5A), info)
    owner.parameters.observe(owner.parameters.address(4, 0x5A), b"\x14")
    await owner.controller_menu.async_refresh()
    sent = []

    def ambiguous(prepared, *, before_dispatch):
        before_dispatch()
        sent.append(prepared.payload)
        raise NiceBidiConnectionError("Lost acknowledgement after dispatch")

    owner.client.write_parameter = ambiguous
    with pytest.raises(HomeAssistantError):
        await owner.configuration.async_write(definition.key, 2.5)
    assert sent == [b"\x19"]
    assert owner.command_executor.last_result.acknowledgement == "unknown"
    await owner.async_shutdown()


async def test_menu_never_reads_password_or_parameter_values(hass):
    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex("f0 fb f0 45"))
    owner.controller_menu.role_adapter = None
    owner.client.dmp_values[(4, 0xFB)] = b"secret-not-to-export"
    await owner.controller_menu.async_refresh()
    assert owner.client.value_reads == []
    assert getattr(owner.client, "dmp_info_reads", []) == []
    assert "secret-not-to-export" not in str(owner.controller_menu.report(include_records=True))
    await owner.async_shutdown()


async def test_menu_inspection_is_blocked_before_queue_during_installer_work(hass):
    owner = await instance(hass)
    owner.client.read_controller_menu = Mock(
        side_effect=AssertionError("Unexpected menu read")
    )
    owner.lifecycle.require_idle = Mock(
        side_effect=HomeAssistantError("Installer operation is busy")
    )
    with pytest.raises(HomeAssistantError, match="busy"):
        await owner.controller_menu.async_refresh()
    owner.client.read_controller_menu.assert_not_called()
    await owner.async_shutdown()


async def test_menu_entity_summary_does_not_repeat_full_table_in_state_history(hass):
    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex('70 5a 40 81'))
    owner.controller_menu.role_adapter = None
    await owner.controller_menu.async_refresh()
    summary = owner.controller_menu.report()
    assert summary['reported_record_count'] == 2
    assert summary['binding_count'] == 2
    assert summary['reported_installer_set_rows'] == 1
    assert 'records' not in summary
    evidence = owner.controller_menu.report(include_records=True)
    assert [row['binding'] for row in evidence['records']] == ['04/5A', '04/81']
    await owner.async_shutdown()

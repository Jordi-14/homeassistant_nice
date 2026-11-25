"""Independent vectors for receiver framing, telemetry and private data handling."""

import json
from dataclasses import replace

import pytest

from custom_components.nice_bidiwifi.controller_catalog import PARAMETERS_BY_KEY
from custom_components.nice_bidiwifi.errors import NiceParameterError, NiceProtocolError
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.radio import (
    RadioAuthContext,
    decode_radio_protocols,
    decode_receiver_counts,
    decode_receiver_outputs,
    decode_remote_record,
    encode_radio_protocols,
)
from tests.native_menu_fixtures import install_menu_fixture
from tests.test_client import _client
from tests.test_controller_menu import instance
from tests.test_parameter_commands import reply


def test_native_counter_shape_codes_and_unknown_values():
    decoded = decode_receiver_counts(bytes.fromhex("00 03 01 00 02 05 00 01"))
    assert decoded["reported_total"] == 3
    assert decoded["protocol_counts"] == [
        {"protocol_code": 1, "protocol": "Nice O-Code", "count": 2},
        {"protocol_code": 5, "protocol": "SMILO", "count": 1},
    ]
    assert decode_receiver_counts(b"\0\0")["reported_total"] == 0
    assert (
        decode_receiver_counts(bytes.fromhex("00 01 fd 00 01"))["protocol_counts"][0][
            "protocol"
        ]
        is None
    )
    for raw in [
        None,
        b"",
        b"\0",
        bytes(3),
        bytes(4),
        b"\xff" * 2,
        bytes(201),
        bytes.fromhex("0002 01 0001 01 0001"),
    ]:
        assert decode_receiver_counts(raw) is None


def test_receiver_output_flags_and_big_endian_timers():
    decoded = decode_receiver_outputs(bytes.fromhex("a5 00 01 12 34"))
    assert decoded["timer_1_raw"] == 1
    assert decoded["timer_2_raw"] == 0x1234
    assert decoded["reported_outputs"] == [
        {"channel": 1, "on": True, "active": False},
        {"channel": 2, "on": False, "active": True},
        {"channel": 3, "on": True, "active": False},
        {"channel": 4, "on": False, "active": True},
    ]
    assert decoded["physical_relay_state"] == "not_inferred"
    for raw in [None, b"", bytes(4), bytes(6), b"\xff" * 5]:
        assert decode_receiver_outputs(raw) is None


def test_signed_radio_bitmap_independent_known_vector_and_context():
    context = RadioAuthContext(0x04030201, 0x01010101, 0x08070605, 0x0C0B0A09, 0x71)
    frame = bytes.fromhex("4e5f4e44494f4e4491131419")
    assert encode_radio_protocols([1, 12], context) == frame
    assert decode_radio_protocols(frame, context) == (1, 12)
    assert str(context) == "RadioAuthContext(command=113)"
    for wrong in [frame[:-1], frame[:-1] + b"\0"]:
        with pytest.raises(NiceProtocolError):
            decode_radio_protocols(wrong, context)
    with pytest.raises(NiceProtocolError):
        decode_radio_protocols(frame, replace(context, receiver_nonce=1))
    with pytest.raises(NiceParameterError):
        encode_radio_protocols([64], context)


def test_memory_codec_never_exports_remote_code_or_rolling_counter():
    record = decode_remote_record(
        bytes.fromhex("67 45 23 21 02 01 64 97 0c 08"), position=3
    )
    assert record.code == 0x1234567 and record.rolling_counter == 258
    public = record.public_summary()
    assert (
        public["position"] == 3
        and public["button"] == 2
        and public["protocol_code"] == 12
    )
    assert public["function_code"] == 6 and public["t4_group"] == 4
    assert "code" not in public and "rolling_counter" not in public
    assert str(0x1234567) not in repr(record)
    assert "258" not in repr(record)
    assert decode_remote_record(bytes(8), position=0).public_summary()["empty"]
    assert decode_remote_record(b"\xff" * 8, position=0).public_summary()["virgin"]
    for raw, index in [(bytes(7), 0), (bytes(11), 0), (bytes(8), 1024), (bytes(8), -1)]:
        with pytest.raises(NiceParameterError):
            decode_remote_record(raw, position=index)


def test_receiver_inspector_queries_only_reviewed_passive_registers(monkeypatch):
    client = _client()
    sent = []
    values = {
        (0, 4): b"\x0a",
        (0, 9): b"OXIBD",
        (0, 10): b"672B",
        (0, 11): b"BK00E",
        (10, 0): b"\x01",
        (10, 0x41): b"\x5f",
        (10, 8): bytes.fromhex("01 00 01 00 02"),
        (10, 0x70): bytes.fromhex("0001 01 0001"),
    }

    def exchange(protocol, request, *args):
        g, p = request[9:11]
        sent.append((g, p, request[11]))
        return b"<Response/>", [reply(g, p, values[(g, p)], target=(0, 10))]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    result = client._read_receiver_diagnostics_locked()
    assert (
        result["state"] == "available" and result["transmitters"]["reported_total"] == 1
    )
    assert result["coding"]["name"] == "flor"
    assert all(flag == 0x99 for _, _, flag in sent)
    assert {(g, p) for g, p, _ in sent}.isdisjoint(
        {(0, 7), (10, 0x10), (10, 0x25), (10, 0x30), (10, 0x31), (10, 0x6F)}
    )
    for target in [(10, 0x25), (10, 0x30), (0, 7)]:
        with pytest.raises(NiceParameterError):
            client._receiver_get_locked(*target)


async def test_menu_targeted_metadata_discovers_new_scalar_control_without_writes(hass):
    owner = await instance(hass)
    install_menu_fixture(owner, bytes.fromhex("70 24"))
    definition = PARAMETERS_BY_KEY["ctrl_deceleration_position_opening"]
    info = decode_dmp_info(bytes.fromhex("02 00 14 01 0000 2000 0001"))
    owner.client.dmp_info = {(4, 0x24): info}
    await owner.controller_menu.async_refresh()
    task = owner.configuration._pro_metadata_task
    if task is not None:
        await task
    assert owner.parameters.info_for(owner.parameters.address(4, 0x24)) == info
    assert owner.client.dmp_info_reads == [(4, 0x24, True)]
    assert owner.client.dmp_writes == []
    assert owner.setting_write_block_reason(definition.setting) is None
    await owner.async_shutdown()


async def test_receiver_inspection_fences_obsolete_results(hass):
    owner = await instance(hass)

    def read(**kwargs):
        owner.observations.invalidate()
        return {
            "session_scope": owner.client.access_session_scope,
            "state": "available",
            "transmitters": {"reported_total": 3},
        }

    owner.client.read_receiver_diagnostics = read
    with pytest.raises(NiceParameterError):
        await owner.receiver.async_inspect()
    assert owner.receiver.snapshot is None
    assert "reported_total" not in json.dumps(owner.receiver.report())
    await owner.async_shutdown()


async def test_default_metadata_loader_does_not_probe_pro_settings(hass):
    from custom_components.nice_bidiwifi.coordinator import (
        DIAGNOSTIC_INFO_REGISTERS,
        SCALED_REGISTERS,
    )
    from custom_components.nice_bidiwifi.protocol.t4.settings import ADDITIONAL_SETTINGS
    from tests.conftest import FakeClient
    from tests.test_coordinator import _coordinator
    owner=_coordinator(hass)
    owner.client=FakeClient()
    await owner._async_load_setting_info()
    already_reviewed=set(SCALED_REGISTERS+DIAGNOSTIC_INFO_REGISTERS)
    pro_only={(s.group,s.parameter) for s in ADDITIONAL_SETTINGS}-already_reviewed
    assert not {(g,p) for g,p,_ in owner.client.dmp_info_reads}&pro_only
    await owner.async_shutdown()


async def test_binary_enum_is_named_from_complete_range_without_guessing(hass):
    owner=await instance(hass)
    install_menu_fixture(owner,bytes.fromhex('70 a2'))
    info=decode_dmp_info(bytes.fromhex('01 00 01 01 00 01 01'))
    owner.parameters.record_info(owner.parameters.address(4,0xA2),info)
    owner.parameters.observe(owner.parameters.address(4,0xA2),b'\x01')
    await owner.controller_menu.async_refresh()
    view=owner.configuration.view('ctrl_decelerations')
    assert view.options==(('0: Off',0),('1: On',1))
    assert view.writable
    assert owner.client.dmp_writes==[]
    await owner.async_shutdown()

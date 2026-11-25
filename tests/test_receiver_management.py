"""Private memory queries and receiver-authentication metadata transactions."""

import json
from unittest.mock import Mock

import pytest

from custom_components.nice_bidiwifi.errors import (
    NiceBidiConnectionError,
    NiceParameterError,
    NiceProtocolError,
    NiceReceiverFormatError,
)
from custom_components.nice_bidiwifi.protocol.t4.radio import (
    RadioAuthContext,
    build_receiver_request,
    encode_radio_protocols,
)
from tests.test_client import _client
from tests.test_connection_router import _router
from tests.test_parameter_commands import reply


def test_memory_page_validates_echo_and_exports_no_private_code(monkeypatch):
    client = _client()
    sent = []
    record = bytes.fromhex("67452321020164970c08")

    def exchange(protocol, raw, *args):
        g, p = raw[9:11]
        data = raw[14:-2]
        sent.append((g, p, raw[11], data))
        value = b"\x0a" if (g, p) == (0, 4) else data + record
        return b"<Response/>", [reply(g, p, value, target=(0, 10))]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    result = client._read_receiver_memory_page_locked(3, 2)
    assert [r["position"] for r in result["records"]] == [3, 4]
    assert all(flag == 0x99 for _, _, flag, _ in sent)
    assert not result["complete_receiver_inventory"]
    assert not result["private_codes_retained"]
    assert "19088743" not in json.dumps(result)
    assert "rolling_counter" not in json.dumps(result)


def test_memory_wrong_echo_is_not_a_valid_page(monkeypatch):
    client = _client()
    monkeypatch.setattr(
        client,
        "_receiver_exchange_locked",
        lambda g, p, data=b"", **kw: b"\x0a" if g == 0 else bytes(10),
    )
    with pytest.raises(NiceReceiverFormatError):
        client._read_receiver_memory_page_locked(1, 1)
    for start, count in [(1020, 8), (-1, 1), (0, 17), (0, 0), (True, 1)]:
        with pytest.raises(NiceParameterError):
            client.read_receiver_memory_page(start, count)
    with pytest.raises(NiceParameterError):
        build_receiver_request(10, 0x25, bytes(10), flags=0xA9)


def test_protocol_inspection_uses_scoped_fresh_challenges_and_verified_frames(
    monkeypatch,
):
    import secrets

    client = _client()
    writes = []
    monkeypatch.setattr(secrets, "randbits", lambda bits: 0x08070605)

    def exchange(g, p, data=b"", *, flags=0x99, target=(0, 10)):
        assert target == (0, 3)
        if g == 0:
            return b"\x04" if p == 4 else bytes.fromhex("01020304aabb")
        assert g == 4
        if flags == 0x89:
            size = 4 if p == 0xC1 else 12
            return bytes([size, 0, 0, 0]) + bytes(size * 3)
        if flags == 0xA9:
            assert p == 0xC1 and data == bytes.fromhex("05060708")
            writes.append(p)
            return b""
        if p == 0xC1:
            return bytes.fromhex("090a0b0c")
        context = RadioAuthContext(0x04030201, 0, 0x08070605, 0x0C0B0A09, p)
        assert data == context.signature(b"").to_bytes(4, "little")
        return encode_radio_protocols([1, 2, 12] if p == 0xBE else [2], context)

    monkeypatch.setattr(client, "_receiver_exchange_locked", exchange)
    guard = Mock()
    result = client._inspect_receiver_protocols_locked(guard)
    assert result["supported"] == (1, 2, 12) and result["enabled"] == (2,)
    assert writes == [0xC1, 0xC1]
    assert result["gate_configuration_changed"] is False
    assert "mac" not in result and "client_nonce" not in result
    guard.assert_called()


def test_uncertain_authentication_is_not_replayed_on_cloud_fallback():
    router, _, clients = _router("local_with_cloud_fallback")
    sent = []

    def uncertain(*, before_dispatch):
        before_dispatch()
        sent.append("local")
        raise NiceBidiConnectionError("ACK lost")

    clients[0].inspect_receiver_protocols = uncertain
    clients[1].inspect_receiver_protocols = lambda **kwargs: sent.append("cloud")
    with pytest.raises(NiceBidiConnectionError):
        router.inspect_receiver_protocols()
    assert sent == ["local"]
    router.close()


async def test_operator_reads_drop_obsolete_results_and_failures_clear_old_data(hass):
    from tests.native_menu_fixtures import set_pro_controls
    from tests.test_controller_menu import instance

    owner = await instance(hass)
    set_pro_controls(owner, True)
    receiver = owner.receiver
    receiver.memory_page = {"records": [{"position": 1}]}

    def changed_session(start, count):
        receiver.invalidate()
        return {"session_scope": owner.client.access_session_scope, "records": []}

    owner.client.read_receiver_memory_page = changed_session
    with pytest.raises(NiceParameterError, match="obsolete"):
        await receiver.async_memory_page()
    assert receiver.memory_page is None
    receiver.protocols = {"supported": [1]}
    owner.client.inspect_receiver_protocols = Mock(
        side_effect=NiceProtocolError("bad reply")
    )
    with pytest.raises(NiceProtocolError):
        await receiver.async_protocols(confirmed=True)
    assert receiver.protocols is None
    owner.client.inspect_receiver_protocols.reset_mock()
    with pytest.raises(NiceParameterError, match="Confirm"):
        await receiver.async_protocols()
    owner.client.inspect_receiver_protocols.assert_not_called()
    await owner.async_shutdown()


async def test_read_only_installer_page_shares_receiver_redaction_and_guards(hass):
    from custom_components.nice_bidiwifi.feature_catalog import FeatureBlocked
    from tests.native_menu_fixtures import set_pro_controls
    from tests.test_controller_menu import instance

    owner = await instance(hass)
    set_pro_controls(owner, False)
    read = Mock(
        return_value={
            "session_scope": owner.client.access_session_scope,
            "records": [{"position": 0, "empty": True}],
            "private_codes_retained": False,
        }
    )
    owner.client.read_receiver_memory_page = read
    with pytest.raises(FeatureBlocked, match="Pro controls"):
        await owner.lifecycle.async_run("radio_inspect")
    read.assert_not_called()
    set_pro_controls(owner, True)
    await owner.lifecycle.async_run("radio_inspect")
    read.assert_called_once_with(0, 8)
    assert owner.lifecycle.runner.record.state == "completed"
    assert owner.receiver.memory_page["records"] == [{"position": 0, "empty": True}]
    assert "records" not in owner.receiver.report()
    await owner.async_shutdown()


async def test_failed_operator_query_records_only_safe_error_details(hass):
    from custom_components.nice_bidiwifi.errors import NiceResponseError, ProtocolLayer
    from tests.native_menu_fixtures import set_pro_controls
    from tests.test_controller_menu import instance

    owner = await instance(hass)
    set_pro_controls(owner, True)
    owner.client.read_receiver_memory_page = Mock(
        side_effect=NiceResponseError(
            "private-remote-code must not appear",
            layer=ProtocolLayer.DMP,
            code="FD",
            request_type="T4_REQUEST",
            register=(10, 0x25),
            unsupported=True,
        )
    )
    with pytest.raises(NiceResponseError):
        await owner.receiver.async_memory_page()
    value = owner.receiver.report(include_memory=True)
    assert value["memory_page"] is None
    assert value["operator_errors"]["memory_page"]["state"] == "unsupported"
    assert value["operator_errors"]["memory_page"]["register"] == "0A/25"
    assert "private-remote-code" not in json.dumps(value)
    assert "operator_errors" not in owner.receiver.report()
    await owner.async_shutdown()


async def test_operator_report_cannot_reuse_old_generation_without_passive_snapshot(
    hass,
):
    from tests.native_menu_fixtures import set_pro_controls
    from tests.test_controller_menu import instance

    owner = await instance(hass)
    set_pro_controls(owner, True)
    owner.client.read_receiver_memory_page = lambda *args: {
        "session_scope": owner.client.access_session_scope,
        "records": [{"position": 0, "empty": True}],
    }
    await owner.receiver.async_memory_page()
    assert owner.receiver.report(include_memory=True)["memory_page"]["records"]
    owner.observations.generation += 1
    assert owner.receiver.report(include_memory=True)["memory_page"] is None
    await owner.async_shutdown()


@pytest.mark.parametrize("case", ["wrong_profile", "metadata_size", "metadata_missing"])
def test_builtin_radio_requires_positive_metadata_before_any_nonce_set(
    monkeypatch, case
):
    from custom_components.nice_bidiwifi.errors import NiceUnsupportedError

    client = _client()
    calls = []

    def exchange(g, p, data=b"", *, flags=0x99, target=(0, 10)):
        calls.append((target, g, p, flags))
        assert flags != 0xA9
        if g == 0:
            return b"\x0a" if case == "wrong_profile" else b"\x04"
        assert flags == 0x89
        return b"" if case == "metadata_missing" else bytes.fromhex("01000000000000")

    monkeypatch.setattr(client, "_receiver_exchange_locked", exchange)
    with pytest.raises(NiceUnsupportedError):
        client._inspect_receiver_protocols_locked(Mock())
    assert calls and all(target == (0, 3) for target, _, _, _ in calls)


def test_authentication_request_contract_cannot_target_external_receiver():
    for g, p, flags, data in [
        (10, 0x6F, 0xA9, bytes(4)),
        (10, 0x71, 0x99, bytes(4)),
        (4, 0xC1, 0xA9, bytes(4)),
    ]:
        with pytest.raises(NiceParameterError):
            build_receiver_request(g, p, data, flags=flags)
    raw = build_receiver_request(4, 0xC1, bytes(4), flags=0xA9, target=(0, 3))
    assert raw[2:4] == b"\0\x03" and raw[9:11] == b"\x04\xc1"


def test_builtin_radio_can_use_verified_native_layout_when_only_info_is_unimplemented(
    monkeypatch,
):
    from custom_components.nice_bidiwifi.errors import NiceResponseError, ProtocolLayer
    from custom_components.nice_bidiwifi.protocol.t4.radio import RadioAuthContext

    client = _client()
    writes = []

    def exchange(g, p, data=b"", *, flags=0x99, target=(0, 10)):
        assert target == (0, 3)
        if g == 0:
            return b"\x04" if p == 4 else bytes.fromhex("010203040506")
        if flags == 0x89:
            raise NiceResponseError(
                "INFO not implemented",
                layer=ProtocolLayer.DMP,
                code="F0",
                request_type="T4_REQUEST",
                unsupported=True,
            )
        if flags == 0xA9:
            writes.append(int.from_bytes(data, "little"))
            return b""
        if p == 0xC1:
            return bytes.fromhex("090a0b0c")
        context = RadioAuthContext(0x04030201, 0, writes[-1], 0x0C0B0A09, p)
        return encode_radio_protocols([2], context)

    monkeypatch.setattr(client, "_receiver_exchange_locked", exchange)
    result = client._inspect_receiver_protocols_locked(Mock())
    assert result["supported"] == (2,) and result["enabled"] == (2,)
    assert len(writes) == 2


def test_mismatched_reply_diagnostics_include_headers_without_private_payload(
    monkeypatch,
):
    client = _client()
    private = b"private-remote-counter"
    monkeypatch.setattr(
        client,
        "_t4_request_locked",
        lambda *a: (b"<Response/>", [reply(10, 0x25, private, target=(0, 3))]),
    )
    with pytest.raises(NiceReceiverFormatError) as error:
        client._receiver_exchange_locked(10, 0x25, b"\0\0")
    evidence = error.value.inspection_evidence
    assert error.value.code == "receiver_reply_count"
    assert error.value.inspection_stage == "0A/25@00.0A"
    assert evidence["headers"][0]["source"] == "0003"
    assert private.decode() not in str(vars(error.value))
    assert private.hex() not in str(vars(error.value))


def test_answered_unknown_memory_layout_does_not_reconnect_or_replay(monkeypatch):
    client = _client()
    sent = []
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: None)
    close = Mock()
    monkeypatch.setattr(client, "_close_locked", close)

    def exchange(g, p, data=b"", **kw):
        sent.append((g, p))
        return b"\x0a" if g == 0 else b"\0\0" + bytes(14)

    monkeypatch.setattr(client, "_receiver_exchange_locked", exchange)
    with pytest.raises(NiceReceiverFormatError) as captured:
        client.read_receiver_memory_page(0, 1)
    assert captured.value.code == "memory_record_length"
    assert captured.value.inspection_evidence == {
        "response_size": 16,
        "requested_slot": 0,
    }
    assert sent == [(0, 4), (10, 0x25)]
    close.assert_not_called()


async def test_transport_wrapping_cannot_erase_sanitized_receiver_failure_details(hass):
    from tests.test_controller_menu import instance

    owner = await instance(hass)
    cause = NiceProtocolError("private text", code="memory_record_length")
    cause.inspection_stage = "memory_record_decode"
    cause.inspection_evidence = {"response_size": 16, "requested_slot": 0}
    outer = NiceBidiConnectionError("private text")
    outer.__cause__ = cause
    owner.receiver._record_operator_error("memory_page", outer)
    data = owner.receiver.report(include_memory=True)["operator_errors"]["memory_page"]
    assert (
        data["code"] == "memory_record_length" and data["shape"]["response_size"] == 16
    )
    assert data["outer_error_type"] == "NiceBidiConnectionError"
    assert "private text" not in json.dumps(data)
    await owner.async_shutdown()


@pytest.mark.parametrize("operation", [0x01, 0x19])
def test_oxibd_memory_query_accepts_scoped_fin_only_or_normal_get_reply(
    monkeypatch, operation
):
    client = _client()
    private_record = bytes.fromhex("6745232102016497")

    def exchange(protocol, request, *args):
        g, p = request[9:11]
        value = b"\x0a" if g == 0 else request[14:-2] + private_record
        return b"<Response/>", [
            reply(g, p, value, operation=0x19 if g == 0 else operation, target=(0, 10))
        ]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    result = client._read_receiver_memory_page_locked(0, 1)
    assert result["records"][0]["position"] == 0
    assert result["records"][0]["button"] == 2
    assert not result["private_codes_retained"]
    assert "19088743" not in json.dumps(result)


@pytest.mark.parametrize("operation", [0x00, 0x09, 0x29])
def test_alternate_memory_reply_does_not_accept_unrelated_operation(
    monkeypatch, operation
):
    client = _client()
    monkeypatch.setattr(
        client,
        "_t4_request_locked",
        lambda *args: (
            b"<Response/>",
            [reply(10, 0x25, bytes(10), operation=operation, target=(0, 10))],
        ),
    )
    with pytest.raises(NiceReceiverFormatError):
        client._receiver_exchange_locked(10, 0x25, bytes(2))

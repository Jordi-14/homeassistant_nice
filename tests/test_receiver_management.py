"""Private memory queries and receiver-authentication metadata transactions."""

import json
from unittest.mock import Mock

import pytest

from custom_components.nice_bidiwifi.errors import (
    NiceBidiConnectionError,
    NiceParameterError,
    NiceProtocolError,
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
    with pytest.raises(NiceProtocolError, match="echo"):
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

    def exchange(g, p, data=b"", *, flags=0x99):
        if g == 0:
            return b"\x0a" if p == 4 else bytes.fromhex("01020304")
        if flags == 0xA9:
            assert p == 0x6F and data == bytes.fromhex("05060708")
            writes.append(p)
            return b""
        if p == 0x6F:
            return bytes.fromhex("090a0b0c")
        context = RadioAuthContext(0x04030201, 0, 0x08070605, 0x0C0B0A09, p)
        assert data == context.signature(b"").to_bytes(4, "little")
        return encode_radio_protocols([1, 2, 12] if p == 0x71 else [2], context)

    monkeypatch.setattr(client, "_receiver_exchange_locked", exchange)
    guard = Mock()
    result = client._inspect_receiver_protocols_locked(guard)
    assert result["supported"] == (1, 2, 12) and result["enabled"] == (2,)
    assert writes == [0x6F, 0x6F]
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

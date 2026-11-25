"""Read-only broadcast scope, malformed replies and public identity bounds."""

from unittest.mock import Mock

import pytest

from custom_components.nice_bidiwifi.errors import NiceProtocolError
from custom_components.nice_bidiwifi.protocol.t4.discovery import decode_profile_replies
from tests.test_client import _client
from tests.test_controller_menu import instance
from tests.test_parameter_commands import reply


def test_profiles_keep_unknown_codes_without_granting_roles_or_completeness():
    frames = [
        reply(0, 4, b"\x04"),
        reply(0, 4, b"\x0a", target=(0, 10)),
        reply(0, 4, b"\x42", target=(2, 7)),
        reply(4, 1, b"\0"),
    ]
    values, rejected, ignored = decode_profile_replies(frames)
    assert values == {(0, 3): 4, (0, 10): 10, (2, 7): 66}
    assert rejected == [] and ignored == 1
    values, _, _ = decode_profile_replies([frames[0], frames[0]])
    assert values == {(0, 3): 4}
    with pytest.raises(NiceProtocolError, match="conflicting"):
        decode_profile_replies([frames[0], reply(0, 4, b"\x0a")])
    with pytest.raises(NiceProtocolError, match="bound"):
        decode_profile_replies([frames[0]] * 65)
    with pytest.raises(NiceProtocolError, match="layout"):
        decode_profile_replies([reply(0, 4, b"")])


def test_bus_discovery_uses_profile_broadcast_then_only_public_scoped_gets(monkeypatch):
    client = _client()
    sent = []

    def exchange(protocol, request, address, endpoint, timeout):
        target = (address, endpoint)
        g, p = request[9:11]
        sent.append((target, g, p, request[11]))
        if target == (255, 255):
            assert (g, p) == (0, 4) and request[2:4] == b"\xff\xff"
            return b"<Response/>", [
                reply(0, 4, b"\x04"),
                reply(0, 4, b"\x0a", target=(3, 10)),
            ]
        assert target in {(0, 3), (3, 10)} and g == 0 and p in {9, 10, 11}
        return b"<Response/>", [reply(0, p, b"Model", target=target)]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    value = client._read_bus_profiles_locked()
    assert (
        len(value["devices"]) == 2
        and not value["complete_inventory"]
        and not value["absence_established"]
    )
    assert not value["configuration_changed"]
    assert all(flag == 0x99 for _, _, _, flag in sent)
    assert all(p not in {7, 8, 0x10, 0x25, 0x30} for _, _, p, _ in sent)


async def test_lifecycle_bus_observations_are_scoped_and_cannot_authorize_writes(hass):
    owner = await instance(hass)
    owner.client.read_bus_profiles = Mock(
        return_value={
            "session_scope": owner.client.access_session_scope,
            "devices": [{"source": "03.0A", "profile_code": 10}],
            "complete_inventory": False,
        }
    )
    await owner.lifecycle.async_run("discover_bus")
    data = owner.lifecycle.diagnostics()["bus_discovery"]
    assert (
        data["state"] == "observed" and data["data"]["devices"][0]["source"] == "03.0A"
    )
    assert "session_scope" not in data["data"]
    assert not owner.client.dmp_writes
    owner.observations.closed = True
    assert owner.lifecycle.diagnostics()["bus_discovery"]["state"] == "stale"
    owner.observations.closed = False
    await owner.async_shutdown()


def test_profile_consumer_uses_first_byte_without_interpreting_extensions():
    profiles, _, _ = decode_profile_replies([reply(0, 4, b"\x04\xaa\x55")])
    assert profiles == {(0, 3): 4}


def test_bus_identity_inspection_yields_before_the_next_request(monkeypatch):
    from custom_components.nice_bidiwifi.errors import NiceReadPreemptedError

    client = _client()
    calls = []
    client._should_yield = lambda: bool(calls)

    def exchange(*args):
        calls.append(1)
        return b"<Response/>", [reply(0, 4, b"\x04")]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    with pytest.raises(NiceReadPreemptedError):
        client._read_bus_profiles_locked()
    assert calls == [1]

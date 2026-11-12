"""Client and router tests for shared Wi-Fi administration."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from custom_components.nice_bidiwifi.client import NiceBidiClient
from custom_components.nice_bidiwifi.errors import (
    NiceBidiConnectionError,
    NiceReadPreemptedError,
    NiceUnsupportedError,
)
from custom_components.nice_bidiwifi.models.administration import NiceInterfaceClock
from custom_components.nice_bidiwifi.models.credentials import NiceCredentials
from custom_components.nice_bidiwifi.protocol.nhk.codec import ETX, STX


class AdministrationClient(NiceBidiClient):
    """Client fake that executes operations without opening a socket."""

    def __init__(self) -> None:
        super().__init__(
            "192.0.2.10",
            443,
            NiceCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
            device_id=2,
        )
        self.requests: list[tuple[str, str]] = []
        self.responses: dict[str, bytes] = {}
        self.command_runner_calls = 0

    def _run_with_reconnect(self, operation: Callable):
        return operation()

    def _run_command_once(self, operation: Callable):
        self.command_runner_calls += 1
        return operation()

    def _signed_exchange_locked(self, request_type: str, body: str = "") -> bytes:
        self.requests.append((request_type, body))
        return self.responses.get(
            request_type,
            STX + f'<Response type="{request_type}" />'.encode() + ETX,
        )


def test_client_reads_logs_and_groups_through_signed_requests() -> None:
    client = AdministrationClient()
    client.responses["LOGS"] = (
        STX
        + b'<Response type="LOGS"><Interface><Events>'
        + b'<Service type="restart" time="2026-07-24T12:00:00Z"/>'
        + b"</Events></Interface></Response>"
        + ETX
    )
    client.responses["GROUPS"] = (
        STX
        + b'<Response type="GROUPS"><Groups><Group id="private">'
        + b"<Devices><Device id=\"2\"><Allow id=\"x\" value=\"y\"/>"
        + b"</Device></Devices></Group></Groups></Response>"
        + ETX
    )

    logs = client.read_logs(8)
    groups = client.read_groups()

    assert logs.count == 1
    assert logs.events[0].event_type == "restart"
    assert groups.count == 1
    assert client.requests == [
        (
            "LOGS",
            '<Interface events="8" />\r\n'
            '<Device id="2" events="8" />\r\n',
        ),
        ("GROUPS", ""),
    ]


def test_client_mutations_use_non_replaying_command_runner() -> None:
    client = AdministrationClient()
    clock = NiceInterfaceClock(
        date="2026-07-24T12:00:00Z",
        zone="+01:00",
        dst="+01:00",
    )

    client.update_interface_name("Gate & garage")
    client.update_interface_clock(clock)
    client.reboot_interface()

    assert client.command_runner_calls == 3
    assert [request_type for request_type, _ in client.requests] == [
        "CHANGE",
        "CHANGE",
        "CHANGE",
    ]
    assert "<Name>Gate &amp; garage</Name>" in client.requests[0][1]
    assert "<Date>2026-07-24T12:00:00Z</Date>" in client.requests[1][1]
    assert "<Reboot>true</Reboot>" in client.requests[2][1]


def test_client_mutation_maps_device_error_without_retry() -> None:
    client = AdministrationClient()
    client.responses["CHANGE"] = (
        STX
        + b'<Response type="CHANGE"><Error><Code>14</Code></Error></Response>'
        + ETX
    )

    with pytest.raises(NiceBidiConnectionError, match="<Code>14</Code>"):
        client.reboot_interface()

    assert client.command_runner_calls == 1
    assert len(client.requests) == 1


def test_client_reads_t4_logs_with_the_read_only_history_request() -> None:
    client = AdministrationClient()
    client.responses["T4_LOGS"] = (
        STX + Path(__file__).with_name("fixtures").joinpath("t4_logs_reply.xml").read_bytes() + ETX
    )

    rows = client.read_t4_logs()

    assert [row.id for row in rows] == [1028, 1027, 1013, 655]
    assert client.requests == [
        (
            "T4_LOGS",
            '<Interface id="2">\r\n<SAddress>00</SAddress>\r\n'
            '<SEndpoint>03</SEndpoint>\r\n<Start max="100"/>\r\n</Interface>\r\n',
        )
    ]
    assert client.command_runner_calls == 0


class RealPathClient(AdministrationClient):
    """Keeps the real _run_with_reconnect; only the socket is faked."""

    def __init__(self, reply: bytes) -> None:
        super().__init__()
        self.reply = reply
        self.closes = 0

    def _run_with_reconnect(self, operation: Callable):
        return NiceBidiClient._run_with_reconnect(self, operation)

    def _ensure_connected_locked(self) -> None:
        return None

    def _close_locked(self) -> None:
        self.closes += 1

    def _signed_exchange_locked(self, request_type: str, body: str = "") -> bytes:
        self.requests.append((request_type, body))
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


@pytest.mark.parametrize(
    "reply",
    [
        STX + b'<Response type="T4_LOGS"><Error><Code>34</Code></Error></Response>' + ETX,
        STX + b"<Response type=" + ETX,
    ],
)
def test_t4_logs_rejection_keeps_the_session(
    reply: bytes, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleeps: list[float] = []
    monkeypatch.setattr("custom_components.nice_bidiwifi.client.time.sleep", sleeps.append)
    client = RealPathClient(reply)

    with pytest.raises(NiceUnsupportedError):
        client.read_t4_logs()

    assert len(client.requests) == 1
    assert client.closes == 0
    assert sleeps == []


def test_t4_logs_transport_failure_is_still_a_connection_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("custom_components.nice_bidiwifi.client.time.sleep", lambda _s: None)
    client = RealPathClient(NiceBidiConnectionError("device did not respond"))

    with pytest.raises(NiceBidiConnectionError):
        client.read_t4_logs()

    assert len(client.requests) == 2
    assert client.closes == 2


def test_t4_logs_preemptible_read_yields_before_sending() -> None:
    client = AdministrationClient()
    client._should_yield = lambda: True

    with pytest.raises(NiceReadPreemptedError):
        client.read_t4_logs(preemptible=True)
    assert client.requests == []

"""Client and router tests for shared Wi-Fi administration."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from custom_components.nice_bidiwifi.client import NiceBidiClient
from custom_components.nice_bidiwifi.errors import NiceBidiConnectionError
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

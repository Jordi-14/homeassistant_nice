"""Connection policy and failover tests."""

from __future__ import annotations

from collections import deque
from typing import Any

import pytest

from custom_components.nice_bidiwifi.connection import (
    NiceConnectionHealth,
    NiceConnectionRoute,
    NiceRouteState,
)
from custom_components.nice_bidiwifi.connection_router import NiceConnectionRouter
from custom_components.nice_bidiwifi.errors import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
)
from custom_components.nice_bidiwifi.models.config import NiceEntryConfig
from custom_components.nice_bidiwifi.transport.lan import LegacyLanTlsTransport
from custom_components.nice_bidiwifi.transport.relay import RelayTlsTransport
from tests.conftest import config_entry_data


class RouteClient:
    """Scriptable protocol client used to observe router decisions."""

    def __init__(self, **kwargs: Any) -> None:
        self.kwargs = kwargs
        self.route = str(kwargs.get("route_name", "local"))
        self.read_results: deque[Any] = deque()
        self.commands: list[str] = []
        self.administration_commands: list[tuple[str, object | None]] = []
        self.callbacks: list = []
        self.failure_callbacks: list = []
        self.reconnect_count = 0
        self.event_stream_active = False
        self.event_stream_error = None

    def read_status(self, *, include_extended: bool = False):
        result = self.read_results.popleft()
        if isinstance(result, Exception):
            raise result
        return result

    def send_action(self, action: str) -> None:
        self.commands.append(action)
        if self.read_results:
            result = self.read_results.popleft()
            if isinstance(result, Exception):
                raise result

    def read_logs(self, event_count: int = 32):
        return self.read_status()

    def read_groups(self):
        return self.read_status()

    def update_interface_name(self, name: str) -> None:
        self.administration_commands.append(("update_name", name))

    def update_interface_clock(self, clock) -> None:
        self.administration_commands.append(("update_clock", clock))

    def reboot_interface(self) -> None:
        self.administration_commands.append(("reboot", None))

    def add_event_callback(self, callback):
        self.callbacks.append(callback)
        return lambda: self.callbacks.remove(callback)

    def add_event_failure_callback(self, callback):
        self.failure_callbacks.append(callback)
        return lambda: self.failure_callbacks.remove(callback)

    def close(self) -> None:
        return None


def _router(mode: str, *, clock=None, legacy_local_tls: bool = False):
    data = config_entry_data(
        connection_mode=mode,
        relay_host="relay.example",
        relay_port=7890,
        legacy_local_tls=legacy_local_tls,
    )
    config = NiceEntryConfig.from_mapping(data)
    health = NiceConnectionHealth.from_policy(config.connection)
    clients: list[RouteClient] = []

    def factory(**kwargs):
        client = RouteClient(**kwargs)
        clients.append(client)
        return client

    router = NiceConnectionRouter(
        config,
        health,
        client_factory=factory,
        monotonic=clock or (lambda: 0.0),
    )
    return router, health, clients


@pytest.mark.parametrize(
    ("mode", "routes"),
    [
        ("local_only", ["local"]),
        ("cloud_only", ["cloud"]),
        ("local_with_cloud_fallback", ["local", "cloud"]),
    ],
)
def test_router_only_constructs_policy_routes(mode: str, routes: list[str]) -> None:
    router, health, clients = _router(mode)

    assert [client.route for client in clients] == routes
    assert health.local is (
        NiceRouteState.UNKNOWN
        if "local" in routes
        else NiceRouteState.NOT_CONFIGURED
    )
    assert health.cloud is (
        NiceRouteState.UNKNOWN
        if "cloud" in routes
        else NiceRouteState.NOT_CONFIGURED
    )
    if mode == "cloud_only":
        assert clients[0].kwargs["transport_factory"] == RelayTlsTransport.connect
    router.close()


def test_legacy_tls_factory_is_used_only_for_the_local_route() -> None:
    """The opt-in compatibility transport never changes the cloud route."""
    router, _, clients = _router(
        "local_with_cloud_fallback",
        legacy_local_tls=True,
    )
    local, cloud = clients

    assert local.kwargs["transport_factory"] == LegacyLanTlsTransport.connect
    assert cloud.kwargs["transport_factory"] == RelayTlsTransport.connect
    assert router.legacy_local_tls_enabled is True
    router.close()


def test_fallback_switches_after_bounded_local_failures() -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [NiceBidiConnectionError("down"), NiceBidiConnectionError("down")]
    )
    cloud.read_results.append("cloud-status")

    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-status"
    assert router.selected_route == "cloud"
    assert health.active is NiceConnectionRoute.CLOUD
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.CONNECTED


def test_fallback_requires_stable_local_recovery() -> None:
    now = [0.0]
    router, health, clients = _router(
        "local_with_cloud_fallback",
        clock=lambda: now[0],
    )
    local, cloud = clients
    local.read_results.extend(
        [
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
            "local-probe-1",
            "local-probe-2",
        ]
    )
    cloud.read_results.extend(["cloud-1", "cloud-2"])

    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-1"
    now[0] = 30.0
    assert router.read_status() == "cloud-2"
    assert router.selected_route == "cloud"
    now[0] = 60.0
    assert router.read_status() == "local-probe-2"
    assert router.selected_route == "local"
    assert health.active is NiceConnectionRoute.LOCAL


def test_auth_failure_does_not_try_another_route() -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.append(NiceBidiAuthError("denied"))
    cloud.read_results.append("must-not-run")

    with pytest.raises(NiceBidiAuthError):
        router.read_status()
    assert len(cloud.read_results) == 1
    assert health.active is NiceConnectionRoute.NONE


def test_ambiguous_command_is_never_replayed_on_cloud() -> None:
    router, _, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [NiceBidiConnectionError("lost"), NiceBidiConnectionError("lost")]
    )

    with pytest.raises(NiceBidiConnectionError):
        router.send_action("open")
    with pytest.raises(NiceBidiConnectionError):
        router.send_action("open")

    assert local.commands == ["open", "open"]
    assert cloud.commands == []
    router.send_action("open")
    assert cloud.commands == ["open"]


def test_cloud_only_uses_relay_without_lan() -> None:
    router, health, clients = _router("cloud_only")
    clients[0].read_results.append("cloud")

    assert router.read_status() == "cloud"
    assert health.active is NiceConnectionRoute.CLOUD
    assert health.local is NiceRouteState.NOT_CONFIGURED


def test_administration_reads_and_writes_honor_route_policy() -> None:
    router, health, clients = _router("cloud_only")
    cloud = clients[0]
    cloud.read_results.extend(["logs", "groups"])
    clock = object()

    assert router.read_logs(8) == "logs"
    assert router.read_groups() == "groups"
    router.update_interface_name("Gate")
    router.update_interface_clock(clock)
    router.reboot_interface()

    assert health.active is NiceConnectionRoute.CLOUD
    assert cloud.administration_commands == [
        ("update_name", "Gate"),
        ("update_clock", clock),
        ("reboot", None),
    ]


def test_failed_recovery_probe_backs_off_and_does_not_flap() -> None:
    now = [0.0]
    router, health, clients = _router(
        "local_with_cloud_fallback",
        clock=lambda: now[0],
    )
    local, cloud = clients
    local.read_results.extend(
        [
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("still down"),
            "not-due",
        ]
    )
    cloud.read_results.extend(["cloud-1", "cloud-2", "cloud-3"])

    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-1"
    now[0] = 30.0
    assert router.read_status() == "cloud-2"
    assert router.local_probe_interval_seconds == 60.0
    now[0] = 59.0
    assert router.read_status() == "cloud-3"
    assert len(local.read_results) == 1
    assert health.active is NiceConnectionRoute.CLOUD


def test_cloud_failure_is_reported_without_lan_attempt() -> None:
    router, health, clients = _router("cloud_only")
    clients[0].read_results.append(NiceBidiConnectionError("relay down"))

    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert health.cloud is NiceRouteState.DISCONNECTED
    assert health.local is NiceRouteState.NOT_CONFIGURED

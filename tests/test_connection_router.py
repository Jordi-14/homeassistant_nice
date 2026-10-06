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
from custom_components.nice_bidiwifi.connection_router import (
    STARTUP_LOCAL_RETRY_DELAY_SECONDS,
    NiceConnectionRouter,
)
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
        self.close_count = 0
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
        self.close_count += 1


def _router(
    mode: str,
    *,
    clock=None,
    legacy_local_tls: bool = False,
    sleeps: list[float] | None = None,
):
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
        sleep=(sleeps.append if sleeps is not None else lambda _seconds: None),
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
        [
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
        ]
    )
    cloud.read_results.append("cloud-status")

    assert router.read_status() == "initial-local-status"
    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-status"
    assert router.selected_route == "cloud"
    assert health.active is NiceConnectionRoute.CLOUD
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.CONNECTED


def test_first_local_connection_failure_uses_cloud_fallback(
    caplog: pytest.LogCaptureFixture,
) -> None:
    sleeps: list[float] = []
    router, health, clients = _router("local_with_cloud_fallback", sleeps=sleeps)
    local, cloud = clients
    local.read_results.extend(
        [
            NiceBidiConnectionError("connection reset by peer"),
            NiceBidiConnectionError("connection reset by peer"),
        ]
    )
    cloud.read_results.append("cloud-status")

    assert router.read_status() == "cloud-status"
    assert sleeps == [STARTUP_LOCAL_RETRY_DELAY_SECONDS]
    assert "using the cloud relay" in caplog.text
    assert router.selected_route == "cloud"
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.CONNECTED
    assert health.active is NiceConnectionRoute.CLOUD


@pytest.mark.parametrize("error_code", ["5", "14"])
def test_unsupported_request_is_not_a_local_route_failure(error_code: str) -> None:
    """CU_WIFI answers DMP status reads with Code 14 on a working session."""
    sleeps: list[float] = []
    router, health, clients = _router("local_with_cloud_fallback", sleeps=sleeps)
    local, cloud = clients
    local.read_results.append(
        NiceBidiConnectionError(
            f"<Error><Code>{error_code}</Code></Error> (type=T4_REQUEST)"
        )
    )
    cloud.read_results.append("must-not-run")

    with pytest.raises(NiceBidiConnectionError, match=f"<Code>{error_code}</Code>"):
        router.read_status()

    assert sleeps == []
    assert len(cloud.read_results) == 1
    assert router.selected_route == "local"
    assert router.local_failure_count == 0
    assert health.local is NiceRouteState.CONNECTED
    assert health.active is NiceConnectionRoute.LOCAL


def test_startup_retries_local_before_cloud_fallback() -> None:
    """A restart can briefly leave the previous session open on the interface."""
    sleeps: list[float] = []
    router, health, clients = _router("local_with_cloud_fallback", sleeps=sleeps)
    local, cloud = clients
    local.read_results.extend(
        [NiceBidiConnectionError("connection reset by peer"), "local-status"]
    )
    cloud.read_results.append("must-not-run")

    assert router.read_status() == "local-status"
    assert sleeps == [STARTUP_LOCAL_RETRY_DELAY_SECONDS]
    assert router.selected_route == "local"
    assert health.active is NiceConnectionRoute.LOCAL
    assert len(cloud.read_results) == 1


def test_runtime_local_failure_does_not_wait_for_a_retry() -> None:
    sleeps: list[float] = []
    router, _, clients = _router("local_with_cloud_fallback", sleeps=sleeps)
    local, _ = clients
    local.read_results.extend(["initial-status", NiceBidiConnectionError("down")])

    assert router.read_status() == "initial-status"
    with pytest.raises(NiceBidiConnectionError):
        router.read_status()

    assert sleeps == []


def test_fallback_requires_stable_local_recovery() -> None:
    now = [0.0]
    router, health, clients = _router(
        "local_with_cloud_fallback",
        clock=lambda: now[0],
    )
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
            "local-probe-1",
            "local-probe-2",
        ]
    )
    cloud.read_results.extend(["cloud-1", "cloud-2"])

    assert router.read_status() == "initial-local-status"
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


def test_recovery_probe_releases_the_other_nhk_session() -> None:
    now = [0.0]
    router, health, clients = _router(
        "local_with_cloud_fallback",
        clock=lambda: now[0],
    )
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
            "local-probe-1",
        ]
    )
    cloud.read_results.extend(["cloud-1", "cloud-2"])

    assert router.read_status() == "initial-local-status"
    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-1"
    assert cloud.close_count == 0

    now[0] = 30.0
    assert router.read_status() == "cloud-2"

    assert cloud.close_count == 1
    assert local.close_count == 1
    assert health.active is NiceConnectionRoute.CLOUD
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.CONNECTED


def test_auth_failure_does_not_try_another_route() -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.append(NiceBidiAuthError("denied"))
    cloud.read_results.append("must-not-run")

    with pytest.raises(NiceBidiAuthError):
        router.read_status()
    assert len(cloud.read_results) == 1
    assert health.active is NiceConnectionRoute.NONE


@pytest.mark.parametrize("error_code", ["7", "15"])
def test_runtime_connect_error_is_retryable_after_success(
    error_code: str,
) -> None:
    router, health, clients = _router("local_only")
    local = clients[0]
    local.read_results.extend(
        [
            "initial-status",
            NiceBidiAuthError(
                f"<Error><Code>{error_code}</Code></Error> (type=CONNECT)"
            ),
        ]
    )

    assert router.read_status() == "initial-status"
    with pytest.raises(NiceBidiConnectionError) as caught:
        router.read_status()

    assert f"<Code>{error_code}</Code>" in str(caught.value)
    assert health.active is NiceConnectionRoute.NONE
    assert health.local is NiceRouteState.DISCONNECTED


@pytest.mark.parametrize("error_code", ["7", "15"])
def test_connect_error_remains_auth_failure_before_runtime_success(
    error_code: str,
) -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.append(
        NiceBidiAuthError(
            f"<Error><Code>{error_code}</Code></Error> (type=CONNECT)"
        )
    )
    cloud.read_results.append("must-not-run")

    with pytest.raises(NiceBidiAuthError):
        router.read_status()

    assert len(cloud.read_results) == 1
    assert health.active is NiceConnectionRoute.NONE


def test_unobserved_connect_code_remains_auth_failure_after_success() -> None:
    router, _, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-status",
            NiceBidiAuthError(
                "<Error><Code>1</Code></Error> (type=CONNECT)"
            ),
        ]
    )
    cloud.read_results.append("must-not-run")

    assert router.read_status() == "initial-status"
    with pytest.raises(NiceBidiAuthError):
        router.read_status()

    assert len(cloud.read_results) == 1


def test_first_cloud_connect_error_is_retryable_after_local_success() -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
        ]
    )
    cloud.read_results.append(
        NiceBidiAuthError(
            "<Error><Code>15</Code></Error> (type=CONNECT)"
        )
    )

    assert router.read_status() == "initial-local-status"
    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    with pytest.raises(NiceBidiConnectionError) as caught:
        router.read_status()

    assert "<Code>15</Code>" in str(caught.value)
    assert isinstance(caught.value.__cause__, NiceBidiAuthError)
    assert router.selected_route == "cloud"
    assert health.active is NiceConnectionRoute.NONE
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.DISCONNECTED


def test_retryable_connect_error_releases_local_before_cloud_fallback() -> None:
    router, health, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiAuthError(
                "<Error><Code>7</Code></Error> (type=CONNECT)"
            ),
        ]
    )
    cloud.read_results.append("cloud-status")

    assert router.read_status() == "initial-local-status"
    with pytest.raises(NiceBidiConnectionError):
        router.read_status()
    assert router.read_status() == "cloud-status"

    assert local.close_count == 1
    assert health.active is NiceConnectionRoute.CLOUD
    assert health.local is NiceRouteState.DISCONNECTED
    assert health.cloud is NiceRouteState.CONNECTED


def test_retryable_connect_error_never_replays_a_command() -> None:
    router, _, clients = _router("local_with_cloud_fallback")
    local, cloud = clients
    local.read_results.extend(
        [
            "initial-status",
            NiceBidiAuthError(
                "<Error><Code>7</Code></Error> (type=CONNECT)"
            ),
        ]
    )

    assert router.read_status() == "initial-status"
    with pytest.raises(NiceBidiConnectionError):
        router.send_action("open")

    assert local.commands == ["open"]
    assert cloud.commands == []


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
            "initial-local-status",
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("down"),
            NiceBidiConnectionError("still down"),
            "not-due",
        ]
    )
    cloud.read_results.extend(["cloud-1", "cloud-2", "cloud-3"])

    assert router.read_status() == "initial-local-status"
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

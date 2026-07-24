"""Route NHK operations between local and verified relay connections."""

from __future__ import annotations

from collections.abc import Callable
import threading
import time
from typing import Any

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError
from .connection import NiceConnectionHealth, NiceConnectionRoute, NiceRouteState
from .models.config import ConnectionMode, NiceEntryConfig, NiceEndpoint
from .protocol.nhk.administration import LOG_EVENTS_PER_SCOPE
from .transport.relay import RelayTlsTransport

LOCAL_FAILURE_THRESHOLD = 2
LOCAL_RECOVERY_THRESHOLD = 2
LOCAL_PROBE_INTERVAL_SECONDS = 30.0
LOCAL_PROBE_MAX_INTERVAL_SECONDS = 300.0


class NiceConnectionRouter:
    """Provide one client API while enforcing the selected route policy."""

    def __init__(
        self,
        config: NiceEntryConfig,
        health: NiceConnectionHealth,
        *,
        client_factory: Callable[..., NiceBidiClient] = NiceBidiClient,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mode = config.connection.mode
        self._health = health
        self._monotonic = monotonic
        self._lock = threading.RLock()
        common = {
            "credentials": config.credentials,
            "device_id": config.device_id,
            "t4_timeout_ms": config.t4_timeout_ms,
        }
        self._local = self._make_client(
            client_factory,
            config.connection.local,
            **common,
        )
        self._cloud = self._make_client(
            client_factory,
            config.connection.relay,
            transport_factory=RelayTlsTransport.connect,
            route_name="cloud",
            **common,
        )
        self._selected = (
            NiceConnectionRoute.CLOUD
            if self._mode is ConnectionMode.CLOUD_ONLY
            else NiceConnectionRoute.LOCAL
        )
        self._local_failures = 0
        self._local_recovery_successes = 0
        self._probe_interval = LOCAL_PROBE_INTERVAL_SECONDS
        self._next_local_probe = 0.0

    @staticmethod
    def _make_client(
        client_factory: Callable[..., NiceBidiClient],
        endpoint: NiceEndpoint | None,
        **kwargs: Any,
    ) -> NiceBidiClient | None:
        if endpoint is None:
            return None
        return client_factory(host=endpoint.host, port=endpoint.port, **kwargs)

    @property
    def active_route(self) -> NiceConnectionRoute:
        """Return the route that most recently completed an operation."""
        return self._health.active

    @property
    def reconnect_count(self) -> int:
        """Return reconnect attempts across configured routes."""
        return sum(
            client.reconnect_count
            for client in (self._local, self._cloud)
            if client is not None
        )

    @property
    def local_failure_count(self) -> int:
        return self._local_failures

    @property
    def local_recovery_success_count(self) -> int:
        return self._local_recovery_successes

    @property
    def selected_route(self) -> str:
        """Return the route selected for the next operation."""
        return self._selected.value

    @property
    def local_probe_interval_seconds(self) -> float | None:
        """Return the current recovery probe interval in fallback mode."""
        if self._mode is not ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK:
            return None
        return self._probe_interval

    @property
    def event_stream_active(self) -> bool:
        client = self._active_client
        return bool(client and client.event_stream_active)

    @property
    def event_stream_error(self) -> str | None:
        client = self._active_client
        return client.event_stream_error if client is not None else None

    @property
    def _active_client(self) -> NiceBidiClient | None:
        if self._health.active is NiceConnectionRoute.LOCAL:
            return self._local
        if self._health.active is NiceConnectionRoute.CLOUD:
            return self._cloud
        return None

    def _client(self, route: NiceConnectionRoute) -> NiceBidiClient:
        client = self._local if route is NiceConnectionRoute.LOCAL else self._cloud
        if client is None:
            raise NiceBidiConnectionError(f"{route.value} route is not configured")
        return client

    def _mark_success(self, route: NiceConnectionRoute, *, active: bool = True) -> None:
        self._health.set_route_state(route, NiceRouteState.CONNECTED, active=active)
        if route is NiceConnectionRoute.LOCAL:
            self._local_failures = 0

    def _mark_failure(self, route: NiceConnectionRoute) -> None:
        self._health.set_route_state(route, NiceRouteState.DISCONNECTED)
        if route is NiceConnectionRoute.LOCAL:
            self._local_failures += 1
            self._local_recovery_successes = 0

    def _call(
        self,
        route: NiceConnectionRoute,
        method: str,
        *args,
        active: bool = True,
        **kwargs,
    ):
        try:
            result = getattr(self._client(route), method)(*args, **kwargs)
        except NiceBidiAuthError:
            self._mark_failure(route)
            raise
        except (NiceBidiConnectionError, OSError):
            self._mark_failure(route)
            raise
        self._mark_success(route, active=active)
        return result

    def _read(self, method: str, *args, **kwargs):
        with self._lock:
            if self._mode is ConnectionMode.LOCAL_ONLY:
                return self._call(NiceConnectionRoute.LOCAL, method, *args, **kwargs)
            if self._mode is ConnectionMode.CLOUD_ONLY:
                return self._call(NiceConnectionRoute.CLOUD, method, *args, **kwargs)

            if self._selected is NiceConnectionRoute.LOCAL:
                try:
                    return self._call(
                        NiceConnectionRoute.LOCAL, method, *args, **kwargs
                    )
                except NiceBidiAuthError:
                    raise
                except (NiceBidiConnectionError, OSError):
                    if self._local_failures < LOCAL_FAILURE_THRESHOLD:
                        raise
                    self._selected = NiceConnectionRoute.CLOUD
                    self._next_local_probe = (
                        self._monotonic() + self._probe_interval
                    )
                    return self._call(
                        NiceConnectionRoute.CLOUD, method, *args, **kwargs
                    )

            now = self._monotonic()
            if now >= self._next_local_probe:
                try:
                    local_result = self._call(
                        NiceConnectionRoute.LOCAL,
                        method,
                        *args,
                        active=False,
                        **kwargs,
                    )
                except NiceBidiAuthError:
                    raise
                except (NiceBidiConnectionError, OSError):
                    self._probe_interval = min(
                        self._probe_interval * 2,
                        LOCAL_PROBE_MAX_INTERVAL_SECONDS,
                    )
                    self._next_local_probe = now + self._probe_interval
                else:
                    self._local_recovery_successes += 1
                    self._next_local_probe = now + LOCAL_PROBE_INTERVAL_SECONDS
                    if (
                        self._local_recovery_successes
                        >= LOCAL_RECOVERY_THRESHOLD
                    ):
                        self._selected = NiceConnectionRoute.LOCAL
                        self._probe_interval = LOCAL_PROBE_INTERVAL_SECONDS
                        self._mark_success(
                            NiceConnectionRoute.LOCAL,
                            active=True,
                        )
                        return local_result
            return self._call(NiceConnectionRoute.CLOUD, method, *args, **kwargs)

    def _write(self, method: str, *args, **kwargs) -> None:
        """Execute a command once; an ambiguous failure is never replayed."""
        with self._lock:
            route = self._selected
            try:
                self._call(route, method, *args, **kwargs)
            except (NiceBidiConnectionError, OSError):
                if (
                    self._mode is ConnectionMode.LOCAL_WITH_CLOUD_FALLBACK
                    and route is NiceConnectionRoute.LOCAL
                    and self._local_failures >= LOCAL_FAILURE_THRESHOLD
                ):
                    self._selected = NiceConnectionRoute.CLOUD
                    self._next_local_probe = (
                        self._monotonic() + self._probe_interval
                    )
                raise

    def read_status(self, *, include_extended: bool = False):
        return self._read("read_status", include_extended=include_extended)

    def read_nhk_status(self):
        return self._read("read_nhk_status")

    def read_info(self):
        return self._read("read_info")

    def read_info_xml(self) -> str:
        return self._read("read_info_xml")

    def read_logs(self, event_count: int = LOG_EVENTS_PER_SCOPE):
        return self._read("read_logs", event_count)

    def read_groups(self):
        return self._read("read_groups")

    def test_connection(self):
        return self._read("test_connection")

    def send_action(self, action: str) -> None:
        self._write("send_action", action)

    def send_dep_action(self, action: str) -> None:
        self._write("send_dep_action", action)

    def write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
    ) -> None:
        self._write(
            "write_dmp_register",
            group,
            parameter,
            value,
            size=size,
        )

    def update_interface_name(self, name: str) -> None:
        self._write("update_interface_name", name)

    def update_interface_clock(self, clock) -> None:
        self._write("update_interface_clock", clock)

    def reboot_interface(self) -> None:
        self._write("reboot_interface")

    def add_event_callback(self, callback) -> Callable[[], None]:
        removers: list[Callable[[], None]] = []
        for route, client in (
            (NiceConnectionRoute.LOCAL, self._local),
            (NiceConnectionRoute.CLOUD, self._cloud),
        ):
            if client is not None:
                removers.append(
                    client.add_event_callback(
                        lambda frame, current=route: (
                            callback(frame)
                            if self._health.active is current
                            else None
                        )
                    )
                )
        return lambda: [remove() for remove in removers]

    def add_event_failure_callback(self, callback) -> Callable[[], None]:
        removers: list[Callable[[], None]] = []
        for route, client in (
            (NiceConnectionRoute.LOCAL, self._local),
            (NiceConnectionRoute.CLOUD, self._cloud),
        ):
            if client is not None:
                removers.append(
                    client.add_event_failure_callback(
                        lambda error, current=route: (
                            callback(error)
                            if self._health.active is current
                            else None
                        )
                    )
                )
        return lambda: [remove() for remove in removers]

    def close(self) -> None:
        with self._lock:
            for client in (self._local, self._cloud):
                if client is not None:
                    client.close()

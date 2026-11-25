"""Route NHK operations between local and Nice relay connections."""

from __future__ import annotations

from collections.abc import Callable
import logging
import threading
import time
from typing import Any

from .client import NiceBidiAuthError, NiceBidiClient, NiceBidiConnectionError
from .connection import NiceConnectionHealth, NiceConnectionRoute, NiceRouteState
from .errors import (
    RUNTIME_RETRYABLE_CONNECT_ERROR_CODES,
    is_unsupported_request_error,
    is_request_rejection,
    nice_error_code,
)
from .models.config import ConnectionMode, NiceEntryConfig, NiceEndpoint
from .parameters import ParameterRepository
from .scheduler import OperationExpired, OperationPriority, SessionScheduler
from .protocol.nhk.administration import LOG_EVENTS_PER_SCOPE, MAX_T4_LOG_ROWS
from .transport.lan import LegacyLanTlsTransport
from .transport.relay import RelayTlsTransport

_LOGGER = logging.getLogger(__name__)

LOCAL_FAILURE_THRESHOLD = 2
# A restart can leave the previous process's NHK session open on the
# interface for a few seconds, and the interface allows only one session.
STARTUP_LOCAL_RETRY_DELAY_SECONDS = 3.0
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
        sleep: Callable[[float], None] = time.sleep,
        parameters: ParameterRepository | None = None,
        command_timeout: float = 15.0,
        deadline_clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._mode = config.connection.mode
        self._health = health
        self._monotonic = monotonic
        self._sleep = sleep
        self.parameters = parameters if parameters is not None else ParameterRepository(config.device_id)
        self._lock = SessionScheduler(clock=deadline_clock)
        self._deadline_clock = deadline_clock
        self._command_timeout = command_timeout
        self._closed = False
        self._shutdown_complete = threading.Event()
        self._waiting_commands = 0
        self._waiting_commands_lock = threading.Lock()
        common = {
            "credentials": config.credentials,
            "device_id": config.device_id,
            "t4_timeout_ms": config.t4_timeout_ms,
            "should_yield": self._command_waiting,
            "parameters": self.parameters,
        }
        local_kwargs: dict[str, Any] = {}
        if config.legacy_local_tls:
            local_kwargs["transport_factory"] = LegacyLanTlsTransport.connect
        self._local = self._make_client(
            client_factory,
            config.connection.local,
            **local_kwargs,
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
        self._legacy_local_tls_enabled = config.legacy_local_tls
        self._authenticated_once = False

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
    def scheduler_diagnostics(self) -> dict:
        return self._lock.diagnostics()

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
    def legacy_local_tls_enabled(self) -> bool:
        """Return whether legacy compatibility is enabled for the local route."""
        return self._legacy_local_tls_enabled

    @property
    def local_tls_legacy_active(self) -> bool:
        """Return whether the current local transport uses legacy compatibility."""
        return bool(
            self._local
            and getattr(self._local, "legacy_tls_active", False)
        )

    @property
    def local_tls_cipher_security_level(self) -> int | None:
        """Return the local TLS cipher security level when connected."""
        if self._local is None:
            return None
        return getattr(self._local, "tls_cipher_security_level", None)

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

    def _release_other_session(self, route: NiceConnectionRoute) -> None:
        """Release the other route before opening the sole allowed NHK session."""
        other_route = (
            NiceConnectionRoute.CLOUD
            if route is NiceConnectionRoute.LOCAL
            else NiceConnectionRoute.LOCAL
        )
        other_state = (
            self._health.cloud
            if other_route is NiceConnectionRoute.CLOUD
            else self._health.local
        )
        if other_state is not NiceRouteState.CONNECTED:
            return
        self._client(other_route).close()
        self._health.set_route_state(
            other_route,
            NiceRouteState.DISCONNECTED,
        )

    def _call(
        self,
        route: NiceConnectionRoute,
        method: str,
        *args,
        active: bool = True,
        **kwargs,
    ):
        self._release_other_session(route)
        try:
            result = getattr(self._client(route), method)(*args, **kwargs)
        except NiceBidiAuthError as err:
            self._client(route).close()
            self._mark_failure(route)
            # Coded authentication errors currently originate only from CONNECT.
            # The router only serves stored entries, whose credentials were
            # already validated by the config or reauth flow, so these session
            # rejections stay retryable across reloads and restarts. Credential
            # rejections such as Code 1 still trigger reauthentication.
            if nice_error_code(err) in RUNTIME_RETRYABLE_CONNECT_ERROR_CODES:
                raise NiceBidiConnectionError(str(err), layer=err.layer, code=err.code,
                                              request_type=err.request_type) from err
            raise
        except (NiceBidiConnectionError, OSError) as err:
            if isinstance(err, OperationExpired):
                raise
            if is_request_rejection(err):
                # The route works; only this request is unsupported.
                self._mark_success(route, active=active)
                self._authenticated_once = True
                raise
            self._mark_failure(route)
            raise
        self._mark_success(route, active=active)
        self._authenticated_once = True
        return result

    def _read(self, method: str, *args, operation_priority=None, **kwargs):
        priority = (OperationPriority.METADATA if method in {"read_info", "read_info_xml", "read_dmp_info", "read_dmp_indexed"}
                    else OperationPriority.READBACK if method == "read_dmp_value"
                    else OperationPriority.DIAGNOSTIC if method in {"read_logs", "read_t4_logs", "read_groups"}
                    else OperationPriority.STATUS)
        if operation_priority is not None:
            priority = operation_priority
        with self._lock.hold(priority):
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
                except (NiceBidiConnectionError, OSError) as local_err:
                    if is_unsupported_request_error(local_err):
                        raise
                    # Setup creates a fresh router for every retry. Waiting for
                    # a second failure here would prevent the initial cloud
                    # fallback from ever being reached.
                    if (
                        self._authenticated_once
                        and self._local_failures < LOCAL_FAILURE_THRESHOLD
                    ):
                        raise
                    failure: Exception = local_err
                    if not self._authenticated_once:
                        self._sleep(STARTUP_LOCAL_RETRY_DELAY_SECONDS)
                        try:
                            return self._call(
                                NiceConnectionRoute.LOCAL, method, *args, **kwargs
                            )
                        except NiceBidiAuthError:
                            raise
                        except (NiceBidiConnectionError, OSError) as retry_err:
                            failure = retry_err
                    _LOGGER.warning(
                        "Nice local connection failed (%s); using the cloud relay "
                        "until the local route recovers",
                        failure,
                    )
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
                except (NiceBidiConnectionError, OSError) as probe_err:
                    if is_unsupported_request_error(probe_err):
                        raise
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

    def _command_waiting(self) -> bool:
        """Return whether a command is queued behind the current operation."""
        return self._waiting_commands > 0 or self._lock.should_yield()

    def _acquire_for_command(self, *, priority=OperationPriority.CONTROL, timeout=15.0) -> None:
        """Take the session lock, asking preemptible reads to yield meanwhile."""
        with self._waiting_commands_lock:
            self._waiting_commands += 1
        try:
            if not self._lock.acquire(timeout=timeout, priority=priority):
                raise OperationExpired()
        finally:
            with self._waiting_commands_lock:
                self._waiting_commands -= 1

    def _write(self, method: str, *args, **kwargs):
        """Execute a command once; an ambiguous failure is never replayed."""
        deadline = self._deadline_clock() + self._command_timeout
        priority = (OperationPriority.STOP if method in {"send_action", "send_dep_action", "send_t4_action"} and args in {("stop",), ("stop_remote",)} else OperationPriority.CONTROL)
        self._acquire_for_command(priority=priority, timeout=self._command_timeout)
        try:
            original_guard = kwargs.pop("before_dispatch", None)
            def dispatch_guard():
                if self._closed or self._deadline_clock() >= deadline:
                    raise OperationExpired("Command expired or entry unloaded before dispatch")
                if original_guard is not None:
                    original_guard()
            kwargs["before_dispatch"] = dispatch_guard
            route = self._selected
            try:
                return self._call(route, method, *args, **kwargs)
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
        finally:
            self._lock.release()

    def read_status(
        self,
        *,
        include_extended: bool = False,
        preemptible: bool = False,
        extra_registers: tuple[tuple[int, int], ...] = (),
    ):
        # passed only when set, so route clients without the argument keep working
        return self._read(
            "read_status",
            include_extended=include_extended,
            preemptible=preemptible,
            **({"extra_registers": extra_registers} if extra_registers else {}),
        )

    def read_nhk_status(self, *, preemptible: bool = False):
        return self._read("read_nhk_status", preemptible=preemptible)

    def read_info(self):
        return self._read("read_info")

    def read_info_xml(self) -> str:
        return self._read("read_info_xml")

    def read_dmp_indexed(self, group: int, parameter: int, index: int, **kwargs):
        background = kwargs.pop("background", False)
        return self._read("read_dmp_indexed", group, parameter, index,
                          operation_priority=OperationPriority.DIAGNOSTIC if background else None, **kwargs)

    def read_dmp_info(self, group: int, parameter: int, **kwargs):
        return self._read("read_dmp_info", group, parameter, **kwargs)

    def read_dmp_value(self, group: int, parameter: int, **kwargs):
        background = kwargs.pop("background", False)
        return self._read("read_dmp_value", group, parameter,
                          operation_priority=OperationPriority.DIAGNOSTIC if background else None, **kwargs)

    @property
    def access_session_scope(self):
        client = self._active_client
        return (str(self.active_route),client.session_state.generation) if client is not None else None

    @property
    def session_diagnostics(self):
        return {name:client.session_state.diagnostics() for name,client in (("local",self._local),("cloud",self._cloud)) if client is not None}

    def read_device_inventory(self):
        return self._read("read_device_inventory", operation_priority=OperationPriority.METADATA)

    def read_boot_state(self, operation, interface_id, *, before_dispatch=None):
        return self._read("read_boot_state", operation, interface_id,
                          before_dispatch=before_dispatch, operation_priority=OperationPriority.DIAGNOSTIC)

    def read_pairings(self):
        return self._read("read_pairings",operation_priority=OperationPriority.METADATA)

    def change_user(self,target,operation,role,*,before_dispatch=None):
        return self._write("change_user",target,operation,role,before_dispatch=before_dispatch)

    def identify_interface(self,*,before_dispatch=None):
        return self._write("identify_interface",before_dispatch=before_dispatch)

    def read_logs(self, event_count: int = LOG_EVENTS_PER_SCOPE):
        return self._read("read_logs", event_count)

    def read_t4_logs(self, max_rows: int = MAX_T4_LOG_ROWS, *, preemptible: bool = False):
        return self._read("read_t4_logs", max_rows, preemptible=preemptible)

    def read_groups(self):
        return self._read("read_groups")

    def test_connection(self):
        return self._read("test_connection")

    def send_action(self, action: str) -> None:
        self._write("send_action", action)

    def send_dep_action(self, action: str) -> None:
        self._write("send_dep_action", action)

    def send_t4_action(self, action: str) -> None:
        self._write("send_t4_action", action)

    def write_parameter(self, prepared, *, before_dispatch=None):
        return self._write("write_parameter", prepared, before_dispatch=before_dispatch)

    def write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
        byte_order: str = "big",
        before_dispatch: Callable[[], None] | None = None,
    ):
        return self._write(
            "write_dmp_register",
            group,
            parameter,
            value,
            size=size,
            **({"byte_order": byte_order} if byte_order != "big" else {}),
            **({"before_dispatch": before_dispatch} if before_dispatch is not None else {}),
        )

    def update_interface_name(self, name: str, *, before_dispatch=None) -> None:
        self._write("update_interface_name", name, before_dispatch=before_dispatch)

    def update_interface_clock(self, clock, *, before_dispatch=None) -> None:
        self._write("update_interface_clock", clock, before_dispatch=before_dispatch)

    def reboot_interface(self, *, before_dispatch=None) -> None:
        self._write("reboot_interface", before_dispatch=before_dispatch)

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
        with self._lock.hold(OperationPriority.STOP, allow_closed=True):
            for client in (self._local, self._cloud):
                if client is not None:
                    client.close()

    def shutdown(self) -> None:
        """Fence queued work before waiting for the current exchange to finish."""
        self._closed = True
        def finalize():
            try:
                for client in (self._local, self._cloud):
                    if client is not None:
                        client.close()
            finally:
                self._shutdown_complete.set()
        self._lock.cancel_pending(on_idle=finalize)

    def wait_closed(self, timeout: float = 15.0) -> bool:
        """Bounded drain after fencing: prevent a reload overtaking session close."""
        return self._shutdown_complete.wait(timeout)

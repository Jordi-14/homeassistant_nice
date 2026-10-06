"""Guarded shared Wi-Fi administration controller."""

from __future__ import annotations

from collections import deque
from dataclasses import replace
from datetime import UTC, datetime
import logging
import time
from typing import Any

from homeassistant.exceptions import HomeAssistantError

from ..connection import (
    CONNECTION_STATE_AUTH_FAILED,
    CONNECTION_STATE_CONNECTED,
    CONNECTION_STATE_FAILED,
    CONNECTION_STATE_RECONNECTING,
)
from ..errors import (
    NiceBidiAuthError,
    NiceBidiConnectionError,
    NiceBidiError,
    nice_error_code,
)
from ..models.administration import (
    NiceAdministrationOperation,
    NiceGroupSnapshot,
    NiceLogSnapshot,
)
from ..protocol.nhk.administration import (
    interface_clock_for,
    validate_interface_name,
)
from .base import OwnerBoundController

_LOGGER = logging.getLogger(__name__)

ADMINISTRATION_HISTORY_LIMIT = 16
NAME_VERIFICATION = "info_name_round_trip"
TIME_VERIFICATION = "info_clock_round_trip"
CLOCK_TOLERANCE_SECONDS = 120


class NiceAdministrationController(OwnerBoundController):
    """Own bounded reads and safety-checked shared Wi-Fi mutations."""

    def __init__(self, owner) -> None:
        super().__init__(owner)
        self.interface_log_snapshot: NiceLogSnapshot | None = None
        self.access_group_snapshot: NiceGroupSnapshot | None = None
        self.administration_history: deque[NiceAdministrationOperation] = deque(
            maxlen=ADMINISTRATION_HISTORY_LIMIT
        )
        self.last_administration_operation: NiceAdministrationOperation | None = None

    def administration_capability(self, name: str) -> bool:
        """Return whether current INFO identifies a supported operation."""
        capabilities = self.capabilities
        return bool(capabilities and getattr(capabilities, name, None) is True)

    def _require_capability(self, name: str, action: str) -> None:
        if not self.administration_capability(name):
            raise HomeAssistantError(
                f"Nice {action} is not supported by this shared Wi-Fi interface"
            )

    async def _async_prepare_mutation(self, action: str) -> None:
        status = self.data
        if status is not None and status.is_moving:
            raise HomeAssistantError(
                f"Nice {action} is blocked while the gate is moving"
            )
        await self._async_cancel_position_target()
        await self._async_cancel_calibration(reason=f"administration:{action}")

    def _notify_entities(self) -> None:
        callback = getattr(self.owner, "async_update_listeners", None)
        if callback is not None:
            callback()

    def _record_operation(
        self,
        *,
        action: str,
        read_only: bool,
        started: float,
        status: str,
        verification: str,
        error: Exception | None = None,
        route: str | None = None,
    ) -> NiceAdministrationOperation:
        operation = NiceAdministrationOperation(
            action=action,
            status=status,
            read_only=read_only,
            route=route or self.active_connection_route,
            latency_ms=round((time.monotonic() - started) * 1000),
            completed_at=datetime.now(UTC),
            verification=verification,
            error_type=type(error).__name__ if error is not None else None,
            error_code=nice_error_code(error) if error is not None else None,
        )
        self.last_administration_operation = operation
        self.administration_history.append(operation)
        self._notify_entities()
        log = _LOGGER.info if error is None else _LOGGER.warning
        log(
            "Nice administration action=%s status=%s route=%s latency_ms=%s "
            "verification=%s error_type=%s error_code=%s",
            operation.action,
            operation.status,
            operation.route,
            operation.latency_ms,
            operation.verification,
            operation.error_type or "none",
            operation.error_code or "none",
        )
        return operation

    async def _async_execute(
        self,
        action: str,
        operation,
        *,
        read_only: bool,
        verification: str = "not_required",
    ) -> Any:
        started = time.monotonic()
        attempted_route = getattr(
            self.client,
            "selected_route",
            self.active_connection_route,
        )
        _LOGGER.debug(
            "Starting Nice administration action=%s read_only=%s selected_route=%s",
            action,
            read_only,
            getattr(self.client, "selected_route", self.active_connection_route),
        )
        try:
            result = await self.hass.async_add_executor_job(operation)
        except NiceBidiAuthError as err:
            await self.hass.async_add_executor_job(self.client.close)
            self._set_connection_state(CONNECTION_STATE_AUTH_FAILED)
            self.last_error = str(err)
            self._record_operation(
                action=action,
                read_only=read_only,
                started=started,
                status="failed",
                verification="not_completed",
                error=err,
                route=attempted_route,
            )
            raise HomeAssistantError(
                f"Nice authentication failed during {action}"
            ) from err
        except (NiceBidiConnectionError, NiceBidiError, OSError, ValueError) as err:
            await self.hass.async_add_executor_job(self.client.close)
            self._set_connection_state(CONNECTION_STATE_FAILED)
            self.last_error = str(err)
            self._record_operation(
                action=action,
                read_only=read_only,
                started=started,
                status="failed",
                verification="not_completed",
                error=err,
                route=attempted_route,
            )
            raise HomeAssistantError(f"Nice {action} failed: {err}") from err

        self._set_connection_state(CONNECTION_STATE_CONNECTED)
        self.last_error = None
        self._record_operation(
            action=action,
            read_only=read_only,
            started=started,
            status="completed",
            verification=verification,
            route=self.active_connection_route,
        )
        return result

    async def async_refresh_interface_logs(self) -> None:
        """Retrieve a bounded, redacted interface and device log."""
        self._require_capability("logs", "log retrieval")
        snapshot = await self._async_execute(
            "refresh_logs",
            self.client.read_logs,
            read_only=True,
        )
        self.interface_log_snapshot = replace(
            snapshot,
            retrieved_at=datetime.now(UTC),
        )
        self._notify_entities()

    async def async_refresh_access_groups(self) -> None:
        """Retrieve bounded, count-only local access-group summaries."""
        self._require_capability("groups", "group retrieval")
        snapshot = await self._async_execute(
            "refresh_groups",
            self.client.read_groups,
            read_only=True,
        )
        self.access_group_snapshot = replace(
            snapshot,
            retrieved_at=datetime.now(UTC),
        )
        self._notify_entities()

    async def async_update_interface_name(self, name: str) -> None:
        """Update and verify the shared Wi-Fi interface name."""
        self._require_capability("interface_name_write", "name update")
        validated = validate_interface_name(name)
        await self._async_prepare_mutation("name update")

        def update_and_verify():
            self.client.update_interface_name(validated)
            info = self.client.read_info()
            if info.interface_name != validated:
                raise NiceBidiConnectionError(
                    "interface name write was acknowledged but INFO verification failed"
                )
            return info

        info = await self._async_execute(
            "update_name",
            update_and_verify,
            read_only=False,
            verification=NAME_VERIFICATION,
        )
        self._store_device_info(info)

    async def async_sync_interface_time(self) -> None:
        """Synchronize and verify UTC, standard offset, and DST fields."""
        self._require_capability("time_sync", "time synchronization")
        await self._async_prepare_mutation("time synchronization")
        clock = interface_clock_for(
            datetime.now(UTC),
            self.hass.config.time_zone,
        )

        def update_and_verify():
            self.client.update_interface_clock(clock)
            info = self.client.read_info()
            try:
                reported = datetime.strptime(
                    info.interface_date or "",
                    "%Y-%m-%dT%H:%M:%SZ",
                ).replace(tzinfo=UTC)
                expected = datetime.strptime(
                    clock.date,
                    "%Y-%m-%dT%H:%M:%SZ",
                ).replace(tzinfo=UTC)
            except ValueError as err:
                raise NiceBidiConnectionError(
                    "clock write was acknowledged but INFO returned an invalid date"
                ) from err
            if (
                abs((reported - expected).total_seconds())
                > CLOCK_TOLERANCE_SECONDS
                or info.interface_zone != clock.zone
                or info.interface_dst != clock.dst
            ):
                raise NiceBidiConnectionError(
                    "clock write was acknowledged but INFO verification failed"
                )
            return info

        info = await self._async_execute(
            "sync_time",
            update_and_verify,
            read_only=False,
            verification=TIME_VERIFICATION,
        )
        self._store_device_info(info)

    async def async_reboot_interface(self) -> None:
        """Request an interface reboot after enforcing physical-state safety."""
        self._require_capability("reboot", "interface reboot")
        await self._async_prepare_mutation("interface reboot")
        await self._async_execute(
            "reboot_interface",
            self.client.reboot_interface,
            read_only=False,
            verification="change_acknowledgement",
        )
        await self.hass.async_add_executor_job(self.client.close)
        self._set_connection_state(CONNECTION_STATE_RECONNECTING)
        self.event_controller.mark_reconnecting()

"""Coordinator for Nice."""

from __future__ import annotations

import asyncio  # noqa: F401 - re-exported for compatibility with existing tests.
from functools import partial
from dataclasses import fields, replace
from datetime import UTC, datetime
import logging
import time

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import issue_registry as ir
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .calibration import NiceBidiCalibrationController
from .capabilities import NiceCapabilityService
from .calibration_constants import (  # noqa: F401 - re-exported for compatibility.
    CALIBRATION_COMMAND_PAUSE_SECONDS,
    CALIBRATION_ENDPOINT_TOLERANCE,
    CALIBRATION_MAX_ATTEMPTS,
    CALIBRATION_MOVEMENT_TIMEOUT_SECONDS,
    CALIBRATION_OUTLIER_ERROR_PERCENT,
    CALIBRATION_REPORT_LOG_CHUNK_SIZE,
    CALIBRATION_SETTLE_SECONDS,
    CALIBRATION_SETTLE_TIMEOUT_SECONDS,
    CALIBRATION_STABILITY_ATTEMPTS,
    CALIBRATION_STATE_CALIBRATED,
    CALIBRATION_STATE_CANCELLED,
    CALIBRATION_STATE_FAILED,
    CALIBRATION_STATE_NOT_CALIBRATED,
    CALIBRATION_STATE_RUNNING,
    CALIBRATION_STOPPED_ENDPOINT_GRACE_SECONDS,
    CALIBRATION_STOPPED_ENDPOINT_MIN_DURATION_RATIO,
    CALIBRATION_STORAGE_VERSION,
    CALIBRATION_TARGET_TOLERANCE_PERCENT,
    CALIBRATION_TARGETS,
)
from .client import (
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    NiceBidiAuthError,
    NiceBidiConnectionError,
    NiceBidiDeviceInfo,
    NiceBidiError,
    nice_bidi_error_code,
    NiceBidiStatus,
)
from .connection import (
    CONNECTION_STATE_AUTH_FAILED,
    CONNECTION_STATE_CONNECTED,
    CONNECTION_STATE_FAILED,
    CONNECTION_STATE_RECONNECTING,
    CONNECTION_STATE_UNKNOWN,
    NiceConnectionHealth,
    NiceConnectionRoute,
)
from .connection_router import NiceConnectionRouter
from .command_executor import CommandExecutor
from .models.status import STATE_OPENING, STATE_CLOSING
from .observations import ObservationStore
from .feature_runtime import FeatureRuntime
from .runtime_tasks import RuntimeTasks
from .scheduler import OperationExpired
from .parameters import ParameterRepository, SupportState
from .errors import NiceParameterError, NiceResponseError, is_request_rejection
from .coordinator_compat import CoordinatorCompatibility
from .models.runtime_state import ConnectionRuntimeState, DiagnosticRuntimeState
from .controllers.events import NiceEventController
from .controllers.administration import NiceAdministrationController
from .controllers.motion_trace import MotionTraceController
from .const import (
    DOMAIN,
    ERROR_UPDATE_INTERVAL,
    IDLE_UPDATE_INTERVAL,
    MOVING_UPDATE_INTERVAL,
)
from .protocol.nhk.status import NHK_STATUS_FIELD_KEYS
from .errors import (
    DMP_STATUS_COMMAND_ONLY_ERROR_CODES,
    DMP_STATUS_NHK_FALLBACK_ERROR_CODES,
    NiceReadPreemptedError,
)
from .position import (  # noqa: F401 - constants are re-exported for compatibility.
    POST_COMMAND_FAST_POLL_SECONDS,
    POST_COMMAND_REFRESH_DELAY_SECONDS,
    POSITION_SIMULATION_CALIBRATED_SPEED_FACTOR,
    POSITION_SIMULATION_FALLBACK_PERCENT_PER_SECOND,
    POSITION_SIMULATION_START_GRACE_SECONDS,
    POSITION_SIMULATION_TICK_SECONDS,
    POSITION_SIMULATION_TIMEOUT_PADDING_SECONDS,
    POSITION_TARGET_POLL_SECONDS,
    POSITION_TARGET_TOLERANCE,
    RECENT_STOP_STATUS_OVERRIDE_SECONDS,
    NiceBidiPositionController,
)
from .models.capabilities import NiceCapabilities
from .models.commands import (
    CommandAcknowledgement,
    CommandKind,
    NiceCommand,
    NiceCommandResult,
    CommandVerification,
)
from .models.config import NiceEntryConfig
from .protocol.t4.info import decode_version_entry
from .protocol.t4.settings import SETTINGS, SETTINGS_BY_REGISTER, DmpSetting
from .protocol.t4.status import STATUS_FIELD_REGISTERS
from .protocol.t4.actions import T4_ACTION_BY_KEY
from .write_policy import dmp_write_block_reason

_LOGGER = logging.getLogger(__name__)
# full INFO loads per session; a controller without some INFO stops asking after this
SETTING_INFO_ATTEMPTS = 3
MAX_CONFIGURATION_ENTRIES = 16

EXTENDED_STATUS_REFRESH_SECONDS = 300.0

DEP_MOVEMENT_ACTIONS = {
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
}
DEP_PARTIAL_OPEN_ACTIONS = {
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
}
CORE_STATUS_FIELD_NAMES = frozenset(
    {
        "state",
        "position",
        "current_position",
        "closed_position",
        "open_position",
        "registers",
        "observed_fields", "fresh_registers", "observation_times",
    }
)
EXTENDED_STATUS_FIELD_NAMES = tuple(
    field.name
    for field in fields(NiceBidiStatus)
    if field.name not in CORE_STATUS_FIELD_NAMES | {"obstacle"}
)


def _unknown_status() -> NiceBidiStatus:
    """Return an empty status for devices that support commands but not DMP status."""
    return NiceBidiStatus(
        state=None,
        position=None,
        current_position=None,
        closed_position=None,
        open_position=None,
        registers={},
    )


LIMITS_STORAGE_VERSION = 1
# normal open positions vary by ~5 % (5024-5271); the fault left 39, 66 and 3018 (-42 %)
LIMITS_DROP_RATIO = 0.75
# Read-only registers shown with the scale from their INFO (electric lock, suction cup, starting torque time).
SCALED_REGISTERS = ((0x04, 0x5A), (0x04, 0x5C), (0x04, 0x91))
# INFO of 04/D1 (installed inputs/outputs) and 04/D2 (available fields, percent scale)
DIAGNOSTIC_INFO_REGISTERS = ((0x04, 0xD1), (0x04, 0xD2))


def controller_limits_store(hass: HomeAssistant, entry_id: str) -> Store:
    """Return the per-entry store of the last good open position."""
    return Store(hass, LIMITS_STORAGE_VERSION, f"{DOMAIN}.controller_limits.{entry_id}")


class NiceBidiDataUpdateCoordinator(
    CoordinatorCompatibility,
    DataUpdateCoordinator[NiceBidiStatus],
):
    """DataUpdateCoordinator for one Nice interface."""

    config_entry: ConfigEntry
    _validate_position_bounds = staticmethod(
        NiceBidiPositionController._validate_position_bounds
    )
    _raw_for_percent = staticmethod(NiceBidiPositionController._raw_for_percent)
    _percent_for_raw = staticmethod(NiceBidiPositionController._percent_for_raw)
    _raw_reached = staticmethod(NiceBidiPositionController._raw_reached)
    _clamp_raw = staticmethod(NiceBidiPositionController._clamp_raw)
    _select_calibration_sample = staticmethod(
        NiceBidiCalibrationController._select_calibration_sample
    )
    _has_encoder_calibration_data = staticmethod(
        NiceBidiCalibrationController._has_encoder_calibration_data
    )
    _is_at_endpoint = staticmethod(NiceBidiCalibrationController._is_at_endpoint)

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Initialize the coordinator."""
        self.connection_runtime = ConnectionRuntimeState()
        self.diagnostic_state = DiagnosticRuntimeState()
        self.observations = ObservationStore()
        self.command_executor = CommandExecutor(lambda operation: self.hass.async_add_executor_job(operation))
        self.config_entry = entry
        self.entry_config = NiceEntryConfig.from_mapping(
            entry.data,
            title=entry.title,
        )
        self.connection_runtime.health = NiceConnectionHealth.from_policy(
            self.entry_config.connection
        )
        self.position_controller = NiceBidiPositionController(self)
        self.calibration_controller = NiceBidiCalibrationController(self)
        self.connection_runtime.state = CONNECTION_STATE_UNKNOWN
        self.device_info: NiceBidiDeviceInfo | None = None
        self.capabilities: NiceCapabilities | None = None
        self.status_polling_supported = True
        self._use_nhk_status = False
        self._rejected_t4_actions: set[str] = set()
        self.command_executor.state.last_command: str | None = None
        # LOCAL-DIAGNOSTIC(pr55): only the abnormal-stop code reads this; see controllers/events.py:_check_abnormal_stop.
        self.command_executor.state.last_command_monotonic: float | None = None
        # LOCAL-DIAGNOSTIC(pr55): abnormal-stop counter; see controllers/events.py:_check_abnormal_stop.
        # LOCAL-DIAGNOSTIC(pr55): last abnormal-stop data; see controllers/events.py:_check_abnormal_stop.
        # LOCAL-DIAGNOSTIC(pr55): abnormal-stop scan limit; see controllers/events.py:_check_abnormal_stop.
        self.command_executor.state.last_command_latency_ms: int | None = None
        self.command_executor.state.last_result: NiceCommandResult | None = None
        self.connection_runtime.last_error: str | None = None
        self.connection_runtime.last_successful_update: datetime | None = None
        self.position_controller._init_position_state(hass, entry)
        self._extended_status_cache: NiceBidiStatus | None = None
        # Value format (DMP INFO) of each writable setting, read once from the controller.
        self.parameters = ParameterRepository(self.entry_config.device_id)
        self.setting_info = self.parameters.setting_info
        # INFO of read-only registers whose value needs the controller's scale, keyed "GG/PP".
        self.register_info = self.parameters.register_info
        # 04/03 elements as the app joins them, e.g. "RBA3R10, RB600" (board, motor), read once.
        self.controller_configuration: str | None = None
        self._setting_info_task: asyncio.Task | None = None
        self._setting_info_attempts = 0
        self._extended_status_next_refresh_monotonic = 0.0
        self._extended_scan_requests = 0
        self._last_open_position: int | None = None
        self._limits_store = controller_limits_store(hass, entry.entry_id)
        self.diagnostic_state.limits_changed: dict | None = None
        # LOCAL-DIAGNOSTIC(pr55): stale maneuver-log baseline; see coordinator.py:_cache_extended_status.
        self.diagnostic_state.maneuver_baseline: tuple[tuple[str, ...], int, int] | None = None
        self.diagnostic_state.obstacle_probed = False
        # NHK STATUS module clock and event counters (raw text) and when they were read
        self.diagnostic_state.nhk_fields: dict[str, str] = {}
        self.diagnostic_state.nhk_read_at: datetime | None = None
        # LOCAL-DIAGNOSTIC(pr55): obstacle seed outcome; see coordinator.py:_set_obstacle_seed.
        self.diagnostic_state.obstacle_seed: str | None = None
        self.calibration_controller._init_calibration_state(hass, entry)
        self._capability_service = NiceCapabilityService(
            self.entry_config.device_id
        )
        self.client = NiceConnectionRouter(
            self.entry_config,
            self.connection_runtime.health,
            parameters=self.parameters,
        )
        super().__init__(
            hass,
            _LOGGER,
            name=DOMAIN,
            update_interval=IDLE_UPDATE_INTERVAL,
            config_entry=entry,
        )
        self.tasks = RuntimeTasks(hass)
        self.features = FeatureRuntime(self)
        self.event_controller = NiceEventController(self)
        self.administration_controller = NiceAdministrationController(self)
        self.motion_trace = MotionTraceController(self)


    @property
    def active_connection_route(self) -> str:
        """Return the route currently carrying protocol traffic."""
        return self.connection_runtime.health.active.value

    @property
    def local_connection_state(self) -> str:
        """Return current LAN route reachability."""
        return self.connection_runtime.health.local.value

    @property
    def cloud_connection_state(self) -> str:
        """Return current Nice relay route reachability."""
        return self.connection_runtime.health.cloud.value

    def _set_connection_state(
        self,
        state: str,
        *,
        route: NiceConnectionRoute | None = None,
    ) -> None:
        """Update legacy and per-route connection state together."""
        self.connection_runtime.state = state
        if route is None:
            route = getattr(
                self.client,
                "active_route",
                NiceConnectionRoute.LOCAL,
            )
        if route is not NiceConnectionRoute.NONE:
            self.connection_runtime.health.mark_overall_state(state, route=route)

    async def _async_update_data(self) -> NiceBidiStatus:
        """Fetch state from the BiDi."""
        if self.observations.closed:
            return self.data or _unknown_status()
        token = self.observations.begin_poll(self.data)
        self.event_controller.ensure_registered()
        try:
            if self.connection_runtime.state == CONNECTION_STATE_FAILED:
                self._set_connection_state(CONNECTION_STATE_RECONNECTING)
            status = await self.hass.async_add_executor_job(self._read_status_and_maybe_info)
        except NiceReadPreemptedError as err:
            # The command that preempted this poll schedules its own refresh.
            if self.data is None:
                raise UpdateFailed(str(err)) from err
            _LOGGER.debug("Nice status poll yielded to a pending command")
            return self.data
        except NiceBidiAuthError as err:
            await self.hass.async_add_executor_job(self.client.close)
            self.event_controller.mark_reconnecting()
            self._set_connection_state(CONNECTION_STATE_AUTH_FAILED)
            self.connection_runtime.last_error = str(err)
            self.position_controller._clear_position_simulation()
            raise ConfigEntryAuthFailed(str(err)) from err
        except (NiceBidiConnectionError, OSError) as err:
            await self.hass.async_add_executor_job(self.client.close)
            self.event_controller.mark_reconnecting()
            self._set_connection_state(CONNECTION_STATE_FAILED)
            self.connection_runtime.last_error = str(err)
            self.update_interval = ERROR_UPDATE_INTERVAL
            self.position_controller._clear_position_simulation()
            raise UpdateFailed(str(err)) from err

        if token.generation != self.observations.generation or self.observations.closed:
            return self.data or self.observations.snapshot()
        status = self.event_controller.reconcile_polled_obstacle(status)
        status = self.observations.apply_poll(status, token)
        self._track_controller_limits(status)
        status = self.position_controller._normalize_status_for_display(self.position_controller._apply_recent_stop_status_hint(status))
        self._store_successful_status(status)
        self.event_controller.mark_connected()
        if self._setting_info_task is None:
            self._setting_info_task = self.tasks.create(self._async_load_setting_info(), "nice-setting-info")
        return status

    async def _async_read_controller_configuration(self) -> str | None:
        """Read 04/03 like MyNice Pro: element i has index i + 1, INFO array size minus one elements."""
        info = await self.hass.async_add_executor_job(
            partial(self.client.read_dmp_info, 0x04, 0x03, preemptible=True)
        )
        if info is None or (info.array_size or 0) < 2:
            return None
        entries = []
        # the app shows a few boards and motors; a bad INFO must not start 255 reads
        for index in range(1, min(info.array_size, MAX_CONFIGURATION_ENTRIES + 1)):
            value = await self.hass.async_add_executor_job(
                partial(self.client.read_dmp_indexed, 0x04, 0x03, index, preemptible=True)
            )
            if (text := decode_version_entry(value, index_size=info.index_size)) is None:
                return None  # incomplete: try again later, never store a part
            entries.append(text)
        return ", ".join(entries) or None

    async def _async_load_setting_info(self) -> None:
        """Read the controller's range or option list of each writable setting once.

        The reads yield to gate commands: on a pending command the load stops and the next
        successful poll resumes it with the settings that are still missing. After an error
        or a missing reply the next poll tries again, at most SETTING_INFO_ATTEMPTS times.
        """
        targets = [
            (self.setting_info, setting.key, setting.group, setting.parameter)
            for setting in SETTINGS.values()
        ] + [
            (self.register_info, f"{group:02X}/{parameter:02X}", group, parameter)
            for group, parameter in SCALED_REGISTERS + DIAGNOSTIC_INFO_REGISTERS
        ]
        for store, key, group, parameter in targets:
            if key in store:
                continue
            try:
                info = await self.hass.async_add_executor_job(
                    partial(self.client.read_dmp_info, group, parameter, preemptible=True)
                )
            except NiceReadPreemptedError:
                _LOGGER.debug("Nice INFO read yielded to a command; resuming later")
                self._setting_info_task = None
                return
            except Exception as err:  # noqa: BLE001 - optional metadata, static limits stay in use
                _LOGGER.debug("Nice INFO not available: %r", err)
                break
            if info is not None:
                store[key] = info
                self.parameters.record_info(self.parameters.address(group, parameter), info)
        if self.controller_configuration is None:
            try:
                self.controller_configuration = await self._async_read_controller_configuration()
            except NiceReadPreemptedError:
                self._setting_info_task = None
                return
            except Exception as err:  # noqa: BLE001 - optional identity
                _LOGGER.debug("Nice 04/03 configuration not available: %r", err)
        loaded = len(self.setting_info) + len(self.register_info)
        _LOGGER.debug("Nice INFO read for %d of %d registers", loaded, len(targets))
        self._setting_info_attempts += 1
        if (loaded < len(targets) or self.controller_configuration is None) and self._setting_info_attempts < SETTING_INFO_ATTEMPTS:
            self._setting_info_task = None
        self.async_update_listeners()

    @property
    def _limits_issue_id(self) -> str:
        return f"controller_limits_changed_{self.config_entry.entry_id}"

    async def async_load_limits_baseline(self) -> None:
        """Load the stored open-position baseline before the first refresh."""
        try:
            stored = await self._limits_store.async_load()
        except Exception as err:  # noqa: BLE001 - a bad store only loses the baseline
            _LOGGER.warning("Nice controller limits could not be loaded: %s", err)
            return
        value = stored.get("open_position") if isinstance(stored, dict) else None
        if isinstance(value, int) and not isinstance(value, bool) and value > 0:
            self._last_open_position = value

    def _track_controller_limits(self, status: NiceBidiStatus) -> None:
        """Flag a sharp drop of the controller open position (runs on the loop)."""
        opened = status.open_position
        baseline = self._last_open_position
        if opened is None:
            return
        if baseline is not None and opened < LIMITS_DROP_RATIO * baseline:
            if self.diagnostic_state.limits_changed is None:
                ir.async_create_issue(
                    self.hass,
                    DOMAIN,
                    self._limits_issue_id,
                    is_fixable=True,
                    is_persistent=False,
                    data={"entry_id": self.config_entry.entry_id},
                    severity=ir.IssueSeverity.WARNING,
                    translation_key="controller_limits_changed",
                    translation_placeholders={
                        "previous": str(baseline),
                        "current": str(opened),
                    },
                )
                # a drop known before the restart must not fire the event and a scan again
                if self.data is not None:
                    # LOCAL-DIAGNOSTIC(pr55): limit change as abnormal event; see controllers/events.py:report_abnormal.
                    self.event_controller.report_abnormal(
                        "controller_limits_changed",
                        previous_open_position=baseline,
                        open_position=opened,
                    )
            self.diagnostic_state.limits_changed = {
                "previous_open_position": baseline,
                "open_position": opened,
            }
            return
        self._set_limits_baseline(opened)
        if self.diagnostic_state.limits_changed is not None:
            self.diagnostic_state.limits_changed = None
            ir.async_delete_issue(self.hass, DOMAIN, self._limits_issue_id)

    def _set_limits_baseline(self, value: int) -> None:
        """Keep the open-position baseline and save it when it changes."""
        if value == self._last_open_position:
            return
        self._last_open_position = value
        self.tasks.create(self._limits_store.async_save({"open_position": value}), "nice-limits-save")

    def async_accept_controller_limits(self) -> bool:
        """Make the current open position the baseline; return false when unknown."""
        opened = self.data.open_position if self.data else None
        if opened is None:
            return False
        self._set_limits_baseline(opened)
        self.diagnostic_state.limits_changed = None
        ir.async_delete_issue(self.hass, DOMAIN, self._limits_issue_id)
        return True

    def _read_status_and_maybe_info(self) -> NiceBidiStatus:
        """Read dynamic status and cache static device info."""
        if not self.status_polling_supported:
            if self.device_info is None:
                self._store_device_info(self.client.read_info())
            return _unknown_status()

        if self._use_nhk_status:
            if self.device_info is None:
                self._store_device_info(self.client.read_info())
            return self._store_nhk_status_fields(self.client.read_nhk_status(preemptible=True))

        try:
            include_extended = self._should_read_extended_status()
            scan_due = self._extended_scan_requests
            trace_registers = () if include_extended else self.motion_trace.extra_registers()
            status = self.client.read_status(
                include_extended=include_extended,
                preemptible=True,
                **({"extra_registers": trace_registers} if trace_registers else {}),
            )
            if trace_registers:
                self.motion_trace.record(status)
        except NiceBidiConnectionError as err:
            error_code = nice_bidi_error_code(err)
            if error_code not in DMP_STATUS_NHK_FALLBACK_ERROR_CODES:
                raise
            try:
                if self.device_info is None:
                    self._store_device_info(self.client.read_info())
            except NiceBidiError:
                raise err from None
            if self._supports_nhk_status():
                status = self._store_nhk_status_fields(self.client.read_nhk_status(preemptible=True))
                self._use_nhk_status = True
                _LOGGER.info(
                    "Nice DMP status polling is not supported by this device; "
                    "using NHK DoorStatus polling"
                )
                return status
            if not self._supports_high_level_actions():
                raise
            if error_code not in DMP_STATUS_COMMAND_ONLY_ERROR_CODES:
                raise err from None
            self.status_polling_supported = False
            _LOGGER.info(
                "Nice DMP status polling is not supported by this device; "
                "using command-only mode"
            )
            return _unknown_status()

        if include_extended:
            status = self._cache_extended_status(status, scan_due)
        else:
            status = self._merge_cached_extended_status(status)
        if self.device_info is None:
            try:
                self._store_device_info(self.client.read_info())
            except NiceBidiError as err:
                _LOGGER.debug("Could not read Nice INFO metadata: %s", err)
        nhk_read = False
        if (
            status.obstacle is None
            and not self.diagnostic_state.obstacle_probed
            and self.capabilities is not None
            and self.capabilities.obstruction
        ):
            # one read-only NHK STATUS request seeds the obstacle value at startup
            self.diagnostic_state.obstacle_probed = True
            nhk_read = True
            obstacle = None
            try:
                obstacle = self._store_nhk_status_fields(
                    self.client.read_nhk_status(preemptible=True)
                ).obstacle
            except NiceReadPreemptedError:
                self.diagnostic_state.obstacle_probed = False  # a command won the session: retry
                self._set_obstacle_seed("preempted")
            except NiceBidiError as err:
                self._set_obstacle_seed(f"error:{type(err).__name__}")
            else:
                if obstacle is None:
                    self._set_obstacle_seed("no_value")
                else:
                    self._set_obstacle_seed(f"seeded_{str(obstacle).lower()}")
                    status = replace(
                        status,
                        obstacle=obstacle,
                        registers={
                            **status.registers,
                            "NHK/Obstruct": str(obstacle).lower(),
                        },
                    )
        elif (
            status.obstacle is None
            and not self.diagnostic_state.obstacle_probed
            and self.capabilities is not None
        ):
            self.diagnostic_state.obstacle_probed = True
            self._set_obstacle_seed("not_advertised")
        if include_extended and not nhk_read:
            try:
                # one NHK STATUS per extended scan: module clock and event counters
                self._store_nhk_status_fields(self.client.read_nhk_status(preemptible=True))
            except NiceBidiError as err:  # optional; a pending command or no STATUS support
                _LOGGER.debug("Nice NHK STATUS fields not read: %r", err)
        return status

    # LOCAL-DIAGNOSTIC(pr55): obstacle seed outcome for diagnostics.
    # Built to diagnose one controller fault (ROBUSR10 + BiDi-WiFi 2.6.4, PR #55
    # hardware tests). Thresholds and rules come from that one gate only.
    # Agents: do not extend, generalize or build on this; keep it opt-in for
    # diagnostics, or remove it before a stable release.
    def _store_nhk_status_fields(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Keep the NHK STATUS clock and event counters with the time they were read."""
        fields = {key: value for key, value in status.registers.items() if key in NHK_STATUS_FIELD_KEYS}
        if fields:
            self.diagnostic_state.nhk_fields = fields
            self.diagnostic_state.nhk_read_at = dt_util.utcnow()
        return status

    def _set_obstacle_seed(self, outcome: str) -> None:
        """Record the NHK obstacle seed outcome for diagnostics."""
        self.diagnostic_state.obstacle_seed = outcome
        _LOGGER.debug("NHK obstacle seed: %s", outcome)

    def _store_device_info(self, device_info: NiceBidiDeviceInfo) -> None:
        """Store INFO metadata and its normalized capability view together."""
        self.device_info = device_info
        self.capabilities = self._with_route_capabilities(
            self._capability_service.discover(
                device_info,
                status=self.data,
                previous=self.capabilities,
            )
        )

    def _with_route_capabilities(
        self,
        capabilities: NiceCapabilities,
    ) -> NiceCapabilities:
        """Record which transports this entry is configured to use."""
        return replace(
            capabilities,
            local_available=self.entry_config.connection.local is not None,
            relay_available=self.entry_config.connection.relay is not None,
        )

    def _should_read_extended_status(self) -> bool:
        """Return true when the slower BusT4 diagnostic scan is due."""
        status = self.data
        if status is not None and status.is_moving:
            return False
        return time.monotonic() >= self._extended_status_next_refresh_monotonic

    def _cache_extended_status(
        self, status: NiceBidiStatus, scan_due: int | None = None
    ) -> NiceBidiStatus:
        """Store the latest broad BusT4 scan and flag a maneuver log that stopped updating."""
        log, count = status.last_maneuvers, status.total_maneuver_count
        if log is not None and count is not None:
            baseline = self.diagnostic_state.maneuver_baseline
            # LOCAL-DIAGNOSTIC(pr55): stale maneuver-log rule (unchanged log plus a failure sign).
            if baseline is None or baseline[0] != log:
                self.diagnostic_state.maneuver_baseline = (log, count, self.event_controller.abnormal_stop_count)
                stale = False
            else:
                # a healthy gate keeps an all-ok log, so it needs a sign of failure
                stale = count - baseline[1] >= 2 and (
                    any(name != "ok" for name in log)
                    or self.event_controller.abnormal_stop_count > baseline[2]
                )
            status = replace(status, last_maneuvers_stale=stale)
        self._extended_status_cache = status
        # a scan request that came while this scan ran stays due
        if scan_due is None or self._extended_scan_requests == scan_due:
            self._extended_status_next_refresh_monotonic = time.monotonic() + EXTENDED_STATUS_REFRESH_SECONDS
        return status

    def _merge_cached_extended_status(self, status: NiceBidiStatus) -> NiceBidiStatus:
        """Merge cached diagnostic fields into a fresh core status read."""
        cached = self._extended_status_cache
        if cached is None:
            return status
        registers = dict(cached.registers)
        registers.update(status.registers)
        values = {
            name: getattr(status if STATUS_FIELD_REGISTERS.get(name) in status.registers else cached, name)
            for name in EXTENDED_STATUS_FIELD_NAMES
        }
        return replace(status, registers=registers, **values)

    def _supports_high_level_actions(self) -> bool:
        """Return true when INFO advertises writable DoorAction support."""
        return bool(self.capabilities and self.capabilities.high_level_actions)

    def _supports_nhk_status(self) -> bool:
        """Return true when INFO advertises readable NHK status properties."""
        return bool(self.capabilities and self.capabilities.readable_status)

    def _store_successful_status(self, status: NiceBidiStatus) -> None:
        """Store successful status read metadata."""
        if self.device_info is not None:
            self.capabilities = self._with_route_capabilities(
                self._capability_service.discover(
                    self.device_info,
                    status=status,
                    previous=self.capabilities,
                )
            )
        self._set_connection_state(CONNECTION_STATE_CONNECTED)
        self.connection_runtime.last_error = None
        self.connection_runtime.last_successful_update = datetime.now(UTC)
        if status.position is not None and (status.observed_fields is None or "position" in status.observed_fields):
            self.position_controller.estimator.reporting_observed = True
            self.position_controller.estimator.last_known_position = status.position
        self.update_interval = self.position_controller._update_interval_for_status(status)
        self.position_controller._sync_position_simulation_from_status(status)

    async def _async_cancel_background_tasks(
        self,
        *,
        calibration_reason: str,
        stop_calibration: bool = True,
    ) -> None:
        """Cancel background tasks owned by this coordinator."""
        await self.position_controller._async_cancel_position_target()
        await self.position_controller._async_cancel_post_command_refresh()
        await self.position_controller._async_cancel_position_simulation()
        await self.calibration_controller._async_cancel_calibration(
            reason=calibration_reason,
            stop=stop_calibration,
        )

    async def async_execute_feature(self, key: str, **payload):
        """Execute a reviewed feature, rejecting unresolved work before side effects."""
        return await self.features.execute(key, **payload)

    @property
    def capability_report(self) -> dict:
        return self.features.report()

    @property
    def reported_moving(self) -> bool:
        """Use measurements for write safety, independent of a local stop display."""
        reported = self.observations.observation("state")
        if reported is not None:
            return reported.value in {STATE_OPENING, STATE_CLOSING}
        return bool(self.data and self.data.is_moving)

    async def async_send_action(self, action: str) -> None:
        """Send an open, close, or stop command."""
        await self.position_controller._async_cancel_position_target()
        await self.calibration_controller._async_cancel_calibration(
            reason=f"cover_action:{action}",
            stop=action != "stop",
        )
        await self._async_send_action(action)

    async def async_send_dep_action(self, action: str) -> None:
        """Send a low-level DEP action command."""
        if not self.t4_action_supported(action):
            raise HomeAssistantError(
                f"Nice T4 action {action!r} is not advertised by this device"
            )
        await self.position_controller._async_cancel_position_target()
        await self.calibration_controller._async_cancel_calibration(reason=f"dep_action:{action}")
        await self._async_send_dep_action(action)

    @property
    def t4_actions_via_nhk(self) -> bool:
        """Return true when T4 actions go as the NHK `T4Action` service instead of a T4_REQUEST DEP frame.

        IT4WIFI 0.4.43 answers every T4_REQUEST with Code 5 (#27, #74), so status falls back to NHK; MyNice Welcome
        drives these modules with CHANGE <T4Action>. Only modules that advertise T4_allowed and a writable Device T4Action service get this route.
        """
        caps = self.capabilities
        return self._use_nhk_status and bool(caps and caps.t4_allowed.advertised and caps.t4_action_service)

    def t4_action_supported(self, action: str) -> bool:
        """Return whether a reviewed action is safe to offer and execute."""
        if action in getattr(self, "_rejected_t4_actions", ()):
            return False
        definition = T4_ACTION_BY_KEY.get(action)
        if definition is None:
            return False
        if self.capabilities is None:
            return definition.compatibility_entity
        advertised = self.capabilities.supports_t4_action(definition.code)
        if advertised is None:
            return definition.compatibility_entity
        return advertised

    @property
    def rejected_t4_actions(self) -> tuple[str, ...]:
        """Return actions rejected as unsupported during this runtime session."""
        return tuple(sorted(self._rejected_t4_actions))

    async def async_write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
    ) -> None:
        """Write a BusT4/DMP register and refresh extended status."""
        setting = SETTINGS_BY_REGISTER.get((group, parameter))
        if setting is None:
            raise HomeAssistantError("Nice DMP write is not in the reviewed parameter catalog")
        block_reason = dmp_write_block_reason(self.device_info, group, parameter)
        if block_reason is not None:
            raise HomeAssistantError(f"Nice DMP write is disabled for this controller: {block_reason}")
        if self.reported_moving:
            raise HomeAssistantError("Nice DMP writes are blocked while the gate is moving")
        if self.calibration_controller.calibration_state == CALIBRATION_STATE_RUNNING:
            raise HomeAssistantError("Nice DMP writes are blocked during calibration")
        if reason := self.setting_write_block_reason(setting):
            raise HomeAssistantError(f"Nice setting {setting.key} cannot be written: {reason}")
        try:
            self._prepare_parameter_write(setting, value)
        except NiceParameterError as err:
            raise HomeAssistantError(f"Nice setting {setting.key} cannot be written: {err}") from err
        if setting.key not in self.setting_info:
            try:
                info = await self.hass.async_add_executor_job(partial(
                    self.client.read_dmp_info, group, parameter, preemptible=True,
                ))
            except NiceResponseError as err:
                if not err.unsupported:
                    raise HomeAssistantError(f"Nice parameter metadata failed: {err}") from err
                self.parameters.record_info(self.parameters.address(group, parameter), None, state=SupportState.UNSUPPORTED)
            except NiceReadPreemptedError as err:
                raise HomeAssistantError("Nice metadata read yielded to a pending command; retry the setting") from err
            except NiceBidiError as err:
                raise HomeAssistantError(f"Nice parameter metadata failed: {err}") from err
            else:
                if info is not None:
                    self.parameters.record_info(self.parameters.address(group, parameter), info)
        try:
            self._prepare_parameter_write(setting, value)
        except NiceParameterError as err:
            raise HomeAssistantError(f"Nice setting {setting.key} cannot be written: {err}") from err
        await self.position_controller._async_cancel_position_target()
        await self.calibration_controller._async_cancel_calibration(
            reason=f"config_write:{group:02X}/{parameter:02X}"
        )
        await self._async_write_dmp_register(group, parameter, value, size=size)

    async def async_write_setting(
        self,
        setting: DmpSetting,
        value: int,
    ) -> None:
        """Write one reviewed semantic DMP setting."""
        if SETTINGS_BY_REGISTER.get((setting.group, setting.parameter)) != setting:
            raise HomeAssistantError("Nice setting is not in the reviewed parameter catalog")
        if reason := self.setting_write_block_reason(setting):
            raise HomeAssistantError(f"Nice setting {setting.key} cannot be written: {reason}")
        await self.async_write_dmp_register(
            setting.group,
            setting.parameter,
            value,
            size=setting.size,
        )

    def setting_write_block_reason(
        self,
        setting: DmpSetting,
    ) -> str | None:
        """Return a device-specific block reason for a reviewed setting."""
        self.parameters.setting_info = self.setting_info
        return self.parameters.block_reason(setting, self.device_info)

    def _prepare_parameter_write(self, setting: DmpSetting, value: int):
        """Validate before side effects and again after asynchronous boundaries."""
        if self.reported_moving:
            raise NiceParameterError("configuration write blocked: gate started moving")
        if self.calibration_controller.calibration_state == CALIBRATION_STATE_RUNNING:
            raise NiceParameterError("configuration write blocked during calibration")
        self.parameters.setting_info = self.setting_info
        dynamic_max = None
        if setting.dynamic_max_field and self.data is not None:
            dynamic_max = getattr(self.data, setting.dynamic_max_field) or self.data.open_position
        return self.parameters.prepare_write(setting, value, device=self.device_info, dynamic_max=dynamic_max)

    async def _async_execute_command(self, command: NiceCommand, operation, *, label="command") -> NiceCommandResult:
        """Shared dispatch, failure classification and connection bookkeeping."""
        started = time.monotonic()
        try:
            result = await self.command_executor.execute(command, operation)
        except (NiceBidiError, OSError, ValueError) as err:
            self._store_failed_command(command, started, err)
            if command.kind is CommandKind.T4_ACTION and nice_bidi_error_code(err) == "5":
                self._rejected_t4_actions.add(command.key)
                self.async_update_listeners()
            if isinstance(err, NiceBidiAuthError):
                await self.hass.async_add_executor_job(self.client.close)
                self._set_connection_state(CONNECTION_STATE_AUTH_FAILED)
                message = "authentication"
            elif isinstance(err, OperationExpired):
                message = label
            elif is_request_rejection(err) or isinstance(err, ValueError):
                self._set_connection_state(CONNECTION_STATE_CONNECTED)
                message = label
            else:
                await self.hass.async_add_executor_job(self.client.close)
                self._set_connection_state(CONNECTION_STATE_FAILED)
                message = label
            self.connection_runtime.last_error = str(err)
            if not isinstance(err, OperationExpired):
                self.position_controller._clear_position_simulation()
            raise HomeAssistantError(f"Nice {message} failed: {err}") from err
        self._set_connection_state(CONNECTION_STATE_CONNECTED)
        self._store_accepted_command(command, started, result=result)
        self.connection_runtime.last_error = None
        self.position_controller._extend_post_command_fast_poll_window()
        return result

    async def _async_send_action(
        self,
        action: str,
        *,
        refresh: bool = True,
        simulate: bool = True,
        simulation_target_position: float | None = None,
    ) -> None:
        """Send an open, close, or stop command without touching target watchers."""
        command = NiceCommand(key=action, kind=CommandKind.DOOR_ACTION)
        stop_started_from_motion = action == "stop" and (
            self.position_controller.animation.action is not None
            or (self.data is not None and self.data.is_moving)
        )
        stop_display_position = self.position_controller.display_position if stop_started_from_motion else None
        await self._async_execute_command(command, partial(self.client.send_action, action))
        if action == "stop":
            self.position_controller.control.recent_stop = time.monotonic()
            self.position_controller.control.stop_started_moving = stop_started_from_motion
        else:
            self.position_controller.control.recent_stop = None
            self.position_controller.control.stop_started_moving = False
        self.update_interval = MOVING_UPDATE_INTERVAL
        if action in {"open", "close"} and simulate:
            self.position_controller._start_position_simulation(action, target_position=simulation_target_position)
        elif action == "stop":
            if stop_display_position is not None:
                self.position_controller.estimator.last_known_position = max(0.0, min(100.0, stop_display_position))
            self.position_controller._clear_position_simulation()
        if refresh:
            self.position_controller._schedule_post_command_refresh()
            self._request_refresh_in_background()

    async def _async_send_dep_action(self, action: str, *, refresh: bool = True) -> None:
        """Send a low-level DEP action command."""
        if not self.t4_action_supported(action):
            raise HomeAssistantError(
                f"Nice T4 action {action!r} is not advertised by this device"
            )
        command = NiceCommand(key=action, kind=CommandKind.T4_ACTION)
        send = self.client.send_t4_action if self.t4_actions_via_nhk else self.client.send_dep_action
        await self._async_execute_command(command, partial(send, action))
        if action in DEP_MOVEMENT_ACTIONS:
            self.position_controller.control.recent_stop = None
            self.position_controller.control.stop_started_moving = False
        self.update_interval = MOVING_UPDATE_INTERVAL
        if action in DEP_PARTIAL_OPEN_ACTIONS:
            self.position_controller._start_position_simulation(
                "open",
                target_position=self._partial_open_target_position(action),
            )
        else:
            self.position_controller._clear_position_simulation()
        if refresh:
            self.position_controller._schedule_post_command_refresh()
            self._request_refresh_in_background()

    def _request_refresh_in_background(self) -> None:
        """Refresh after an acknowledged command without delaying the caller."""
        self.tasks.create(self.async_request_refresh(), "nice-post-command-refresh")

    def _partial_open_target_position(self, action: str) -> float | None:
        """Return a configured partial-open target as percent when available."""
        status = self.data
        if status is None:
            return None
        raw_by_action = {
            DEP_ACTION_PARTIAL_OPEN_1: status.partial_open_1_position,
            DEP_ACTION_PARTIAL_OPEN_2: status.partial_open_2_position,
            DEP_ACTION_PARTIAL_OPEN_3: status.partial_open_3_position,
        }
        raw = raw_by_action.get(action)
        if raw is None or status.closed_position is None or status.open_position is None:
            return None
        if status.closed_position == status.open_position:
            return None
        return self.position_controller._percent_for_raw(status, raw)

    async def _async_write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
        refresh: bool = True,
    ) -> None:
        """Validate a reviewed parameter, write once and verify only that value."""
        setting = SETTINGS_BY_REGISTER.get((group, parameter))
        if setting is None:
            raise HomeAssistantError("Nice DMP write is not in the reviewed parameter catalog")
        try:
            prepared = self._prepare_parameter_write(setting, value)
        except NiceParameterError as err:
            raise HomeAssistantError(f"Nice setting {setting.key} cannot be written: {err}") from err
        command_name = f"dmp_{group:02X}_{parameter:02X}_set"
        size = len(prepared.payload)
        command = NiceCommand(
            key=command_name,
            kind=CommandKind.DMP_WRITE,
            arguments=(
                ("group", group),
                ("parameter", parameter),
                ("value", value),
                ("size", size),
            ),
        )
        def before_dispatch():
            current = self._prepare_parameter_write(setting, value)
            if current.payload != prepared.payload or current.byte_order != prepared.byte_order:
                raise NiceParameterError("controller metadata changed before dispatch; retry the setting")
        result = await self._async_execute_command(command,
            partial(self.client.write_parameter, prepared, before_dispatch=before_dispatch), label="DMP write")
        # Verification is a read, not a retry of the potentially completed write.
        raw = None
        verification_error = None
        readback_failure = None
        try:
            raw = await self.hass.async_add_executor_job(partial(
                self.client.read_dmp_value, group, parameter, preemptible=True,
            ))
        except (NiceBidiError, OSError) as err:
            verification_error = type(err).__name__
            readback_failure = err
        # Another command may have completed while this readback was waiting.
        if self.command_executor.state.last_result is not result:
            return
        if readback_failure is not None and not isinstance(readback_failure, (NiceReadPreemptedError, NiceResponseError)):
            self._set_connection_state(
                CONNECTION_STATE_AUTH_FAILED if isinstance(readback_failure, NiceBidiAuthError) else CONNECTION_STATE_FAILED
            )
            self.connection_runtime.last_error = f"Setting sent; readback unavailable: {readback_failure}"
        observed = self.parameters.observe(self.parameters.address(group, parameter), raw, source="readback")
        verification = (
            CommandVerification.UNAVAILABLE if observed.raw_value is None
            else CommandVerification.VERIFIED if observed.raw_value == value
            else CommandVerification.MISMATCH
        )
        details = (*result.details, ("requested_value", value), ("readback_value", observed.raw_value))
        if verification_error:
            details += (("readback_error", verification_error),)
        verified_result = replace(result, verification=verification, details=details)
        if self.command_executor.state.last_result is result:
            self.command_executor.state.last_result = verified_result
        key = f"{group:02X}/{parameter:02X}"
        self.observations.apply_update({setting.key: observed.raw_value}, {key: raw.hex(" ") if raw else ""},
                                       source="readback", baseline=self.data)
        for attribute in ("data", "_extended_status_cache"):
            snapshot = getattr(self, attribute)
            if snapshot is None:
                continue
            registers = dict(snapshot.registers)
            if raw is not None:
                registers[key] = raw.hex(" ")
            else:
                registers.pop(key, None)
            status_value = bool(observed.raw_value) if setting.kind == "boolean" and observed.raw_value is not None else observed.raw_value
            updated = replace(snapshot, registers=registers, **{setting.key: status_value})
            if attribute == "data":
                self.async_set_updated_data(updated)
            else:
                self._extended_status_cache = updated
        if verification is CommandVerification.MISMATCH:
            self.connection_runtime.last_error = f"{setting.key} read back as {observed.raw_value}, requested {value}"
            raise HomeAssistantError(f"Nice setting verification failed: {self.connection_runtime.last_error}")

    def _store_accepted_command(
        self,
        command: NiceCommand,
        started: float,
        *, result: NiceCommandResult | None = None,
    ) -> None:
        """Store a successful normalized acknowledgement and compatibility fields."""
        result = result or NiceCommandResult(
            command=command,
            acknowledgement=CommandAcknowledgement.ACCEPTED,
            latency_ms=round((time.monotonic() - started) * 1000),
        )
        self.command_executor.state.last_result = result
        self.command_executor.state.last_command = command.key
        self.command_executor.state.last_command_monotonic = time.monotonic()
        self.command_executor.state.last_command_latency_ms = result.latency_ms
        self.motion_trace.handle_command(command.key)

    def _store_failed_command(
        self,
        command: NiceCommand,
        started: float,
        error: Exception,
    ) -> None:
        """Store a rejected normalized acknowledgement."""
        self.command_executor.state.last_result = self.command_executor.failed_result(command, started, error)

    async def async_reconnect(self) -> None:
        """Force the current NHK/TLS session to be recreated."""
        if self.observations.closed:
            return
        self.observations.invalidate()
        await self._async_cancel_background_tasks(calibration_reason="reconnect")
        self.event_controller.mark_reconnecting()
        self._set_connection_state(CONNECTION_STATE_RECONNECTING)
        await self.hass.async_add_executor_job(self.client.close)
        await self.tasks.cancel()
        self._setting_info_task = None
        await self.async_request_refresh()

    async def async_shutdown(self) -> None:
        """Close the persistent connection."""
        self.observations.invalidate(close=True)
        # a still-valid issue is recreated by the first poll after setup
        ir.async_delete_issue(self.hass, DOMAIN, self._limits_issue_id)
        await self._async_cancel_background_tasks(calibration_reason="shutdown")
        await self.position_controller.async_save_movement_profile()
        if self._setting_info_task is not None:
            # it must not open a new session on the closed client
            self._setting_info_task.cancel()
        self.event_controller.shutdown()
        await self.hass.async_add_executor_job(self.client.shutdown)
        await self.tasks.cancel(close=True)

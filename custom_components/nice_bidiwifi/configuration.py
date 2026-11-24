"""Shared configuration views, exact engineering values and selective reads."""

from __future__ import annotations

import asyncio
from collections import Counter
from dataclasses import dataclass
from fractions import Fraction
from functools import partial
import time

from homeassistant.exceptions import HomeAssistantError

from .controller_catalog import ENUM_FUNCTIONS, PARAMETERS_BY_KEY, ControllerParameter
from .errors import NiceBidiError, NiceParameterError, NiceReadPreemptedError
from .parameters import ParameterAddress, SupportState
from .protocol.t4.info import DmpInfo, scaled_value

MAX_INDEXED_ELEMENTS = 16
CONFIGURATION_REFRESH_SECONDS = 300


@dataclass(frozen=True, slots=True)
class ConfigurationView:
    state: str
    reason: str | None
    value: int | float | bool | str | None
    raw_value: int | None
    unit: str | None
    options: tuple[tuple[str, int], ...]
    minimum: float | None
    maximum: float | None
    step: float | None
    writable: bool
    gap: str | None
    index: int | None = None


def scalar_info_valid(info: DmpInfo) -> bool:
    return (
        1 <= info.size <= 8
        and info.data_format in {0, 0x25}
        and (info.values is None or info.scalar_options is not None)
        and info.byte_order in {"big", "little"}
        and (info.flags & 0x40 or info.flags & 0x30 != 0x30)
        and (not info.indexed or 1 <= info.index_size <= 3)
    )


def engineering(value: int, info: DmpInfo) -> Fraction:
    if not info.flags & 0x40 and info.flags & 0x30:
        if not info.scale:
            raise NiceParameterError("Controller scale is missing")
        return (
            Fraction(value, info.scale)
            if info.flags & 0x10
            else Fraction(value * info.scale)
        )
    return Fraction(value)


def legacy_parameter_value(coordinator, setting, fallback):
    """Existing adapters keep raw units but share the newest scoped observation."""
    repository = coordinator.parameters
    observation = repository.observation_for(
        repository.address(setting.group, setting.parameter)
    )
    if observation is None:
        return fallback
    raw = observation.raw_value
    return bool(raw) if setting.kind == "boolean" and raw is not None else raw


class ControllerConfiguration:
    """Configuration is independent of movement measurement and display animation."""

    def __init__(self, coordinator):
        self._coordinator = coordinator
        self._watched: Counter[str] = Counter()
        self._task: asyncio.Task | None = None
        self._last_attempt: dict[str, float] = {}
        self._generation = 0
        self._client_epoch = None

    def sync_session(self):
        epoch = getattr(self._coordinator.client, "reconnect_count", None)
        if self._client_epoch is not None and epoch != self._client_epoch:
            self.invalidate()
        self._client_epoch = epoch

    def definition(self, key: str) -> ControllerParameter:
        try:
            return PARAMETERS_BY_KEY[key]
        except KeyError as err:
            raise NiceParameterError("Unknown controller parameter") from err

    def address(self, definition: ControllerParameter, index=None) -> ParameterAddress:
        if not definition.safe_read or definition.binding is None:
            raise NiceParameterError(f"Parameter is blocked: {definition.gap}")
        return self._coordinator.parameters.address(*definition.binding, index=index)

    def watch(self, key: str):
        self.address(
            self.definition(key)
        )  # preflight before task or bookkeeping changes
        self._watched[key] += 1
        self.schedule_refresh()

        def remove():
            self._watched[key] -= 1
            if self._watched[key] <= 0:
                del self._watched[key]

        return remove

    def schedule_refresh(self):
        owner = self._coordinator
        if (
            not self._watched
            or owner.observations.closed
            or owner.reported_moving
            or owner.calibration_controller.calibration_state == "running"
        ):
            return
        if self._task is None or self._task.done():
            self._task = owner.tasks.create(
                self._refresh_watched(), "nice-controller-configuration"
            )

    async def _refresh_watched(self):
        try:
            # A small batch bounds background work; the session scheduler can
            # yield before each INFO/GET. Later status polls continue the scan.
            due = [
                key
                for key in self._watched
                if time.monotonic() - self._last_attempt.get(key, float("-inf"))
                >= CONFIGURATION_REFRESH_SECONDS
            ]
            # Unread first, then least recently attempted. Insertion order can
            # starve later entities when a full sweep exceeds the refresh period.
            due.sort(key=lambda key: self._last_attempt.get(key, float("-inf")))
            for key in due[:3]:
                if self._coordinator.reported_moving:
                    break
                try:
                    await self.async_refresh_parameter(key)
                except NiceReadPreemptedError:
                    break
                except (NiceBidiError, OSError):
                    self._last_attempt[key] = time.monotonic()
        finally:
            self._task = None
            self._coordinator.async_update_listeners()

    async def async_refresh_parameter(self, key: str):
        definition = self.definition(key)
        address = self.address(definition)
        owner = self._coordinator
        if owner.observations.closed:
            raise NiceParameterError("Integration is shutting down")
        if (
            owner.reported_moving
            or owner.calibration_controller.calibration_state == "running"
        ):
            raise NiceReadPreemptedError("Configuration read waits until movement ends")
        generation = self._generation
        repository = owner.parameters
        info = repository.info_for(address)
        if info is None:
            info = await owner.hass.async_add_executor_job(
                partial(
                    owner.client.read_dmp_info, *definition.binding, preemptible=True
                )
            )
            if generation != self._generation or owner.observations.closed:
                return
            if info is not None:
                repository.record_info(address, info)
            elif (
                repository.support_for(address, "info") is not SupportState.UNSUPPORTED
            ):
                repository.record_info(address, None, state=SupportState.UNAVAILABLE)
        if info is not None and scalar_info_valid(info):
            if info.indexed:
                if (
                    info.array_size is not None
                    and 0 < info.array_size <= MAX_INDEXED_ELEMENTS
                ):
                    for index in range(info.array_size):
                        if (
                            owner.reported_moving
                            or generation != self._generation
                            or owner.observations.closed
                        ):
                            return
                        raw = await owner.hass.async_add_executor_job(
                            partial(
                                owner.client.read_dmp_indexed,
                                *definition.binding,
                                index,
                                preemptible=True,
                                background=True,
                            )
                        )
                        if generation != self._generation or owner.observations.closed:
                            return
                        repository.observe(
                            self.address(definition, index),
                            raw,
                            source="configuration_get",
                        )
            else:
                raw = await owner.hass.async_add_executor_job(
                    partial(
                        owner.client.read_dmp_value,
                        *definition.binding,
                        preemptible=True,
                        background=True,
                    )
                )
                if generation != self._generation or owner.observations.closed:
                    return
                repository.observe(address, raw, source="configuration_get")
        self._last_attempt[key] = time.monotonic()
        owner.async_update_listeners()

    def invalidate(self):
        self._generation += 1
        self._coordinator.parameters.invalidate_menu_permissions()
        self._last_attempt.clear()

    def view(self, key: str, *, index=None) -> ConfigurationView:
        self.sync_session()
        definition = self.definition(key)

        def blocked(state, reason, gap=None):
            return ConfigurationView(
                state,
                reason,
                None,
                None,
                definition.unit,
                (),
                None,
                None,
                None,
                False,
                gap,
                index,
            )

        if not definition.safe_read:
            return blocked("blocked", "No reviewed read binding", definition.gap)
        owner = self._coordinator
        repository = owner.parameters
        address = self.address(definition, index)
        base = self.address(definition)
        info = repository.info_for(base)
        if (
            repository.support_for(base, "info") is SupportState.UNSUPPORTED
            or repository.support_for(address, "get") is SupportState.UNSUPPORTED
        ):
            return blocked("unsupported", "Controller does not support this operation")
        if info is None:
            return blocked("unavailable", "Controller metadata has not been read")
        if not scalar_info_valid(info):
            return blocked(
                "blocked",
                "The value format is not verified",
                "controller.parameter_format",
            )
        if info.indexed and index is None:
            return blocked(
                "indexed",
                "Indexed elements are separate; motor identities are not assumed",
                "controller.index_identity",
            )
        if index is not None and (
            not info.indexed
            or info.array_size is None
            or not 0 <= index < min(info.array_size, MAX_INDEXED_ELEMENTS)
        ):
            return blocked(
                "blocked", "Unverified element index", "controller.index_identity"
            )
        if repository.support_for(address, "get") is SupportState.UNAVAILABLE:
            return blocked("unavailable", "The latest value read did not complete")
        observation = repository.observation_for(address)
        raw = observation.raw_value if observation else None
        options = info.scalar_options or ()
        names = (
            {0: "off", 1: "on"}
            if info.function == 1 and options == (0, 1)
            else ENUM_FUNCTIONS.get(info.function, {})
        )
        labels = tuple(
            (
                f"{code}: {names.get(code, 'code_' + str(code)).replace('_', ' ').capitalize()}",
                code,
            )
            for code in options
        )
        value = scaled_value(raw, info)
        expected_function = {"%": 0x0A, "s": 0x11, "pulses": 0x14}.get(definition.unit)
        unit = definition.unit if expected_function == info.function else None
        try:
            minimum = (
                float(engineering(info.minimum, info))
                if info.minimum is not None
                else None
            )
            maximum = (
                float(engineering(info.maximum, info))
                if info.maximum is not None
                else None
            )
            step = (
                float(engineering(info.step, info)) if info.step is not None else None
            )
            if options:
                minimum = float(engineering(min(options), info))
                maximum = float(engineering(max(options), info))
        except NiceParameterError:
            return blocked(
                "blocked", "The scale is incomplete", "controller.parameter_format"
            )
        if labels and raw is not None:
            value = next(
                (label for label, code in labels if raw == code),
                f"{raw}: Unlisted code",
            )
        setting = definition.setting
        if setting is not None and not info.indexed:
            dynamic_max = None
            if setting.dynamic_max_field and owner.data is not None:
                dynamic_max = (
                    getattr(owner.data, setting.dynamic_max_field)
                    or owner.data.open_position
                )
            try:
                low, high = repository.limits(setting, dynamic_max=dynamic_max)
                minimum, maximum = (
                    float(engineering(low, info)),
                    float(engineering(high, info)),
                )
            except NiceParameterError:
                pass  # the write policy below explains the contradiction
        reason = (
            owner.setting_write_block_reason(setting)
            if setting is not None and index is None
            else "New writes require verified menu permissions and a reviewed binding"
        )
        gap = (
            "controller.menu_wire"
            if setting is None or setting.requires_menu_permission
            else None
        )
        if info.indexed:
            reason, gap = (
                "Indexed writes require verified element identities and layout",
                "controller.index_identity",
            )
        if reason is None and hasattr(owner,"access") and str(owner.access.role)!="admin":
            reason="Verified administrator permission is required"
        if owner.reported_moving:
            reason = "Configuration writes are blocked while moving"
        elif owner.calibration_controller.calibration_state == "running":
            reason = "Configuration writes are blocked during calibration"
        elif owner.observations.closed:
            reason = "Integration is shutting down"
        return ConfigurationView(
            "available" if value is not None else "unavailable",
            reason,
            value,
            raw,
            unit,
            labels,
            minimum,
            maximum,
            step,
            reason is None,
            gap,
            index,
        )

    async def async_write(self, key: str, value, *, option=False, index=None):
        definition = self.definition(key)
        view = self.view(key, index=index)
        if not view.writable or definition.setting is None:
            raise HomeAssistantError(view.reason or "Parameter is not writable")
        if option:
            values = dict(view.options)
            if value not in values:
                raise HomeAssistantError("Option is not advertised by this controller")
            raw = values[value]
        else:
            prepared = self._coordinator.parameters.prepare_native_write(
                definition.setting, value
            )
            raw = prepared.value
        await self._coordinator.async_write_setting(definition.setting, raw)

    def report(self):
        result = {}
        for key, definition in PARAMETERS_BY_KEY.items():
            if definition.existing_read:
                result[key] = {
                    "state": "implemented",
                    "operation": definition.existing_read,
                    "writable": False,
                    "app_id": definition.app_id,
                    "gap": None,
                }
                continue
            if definition.reviewed_action is not None:
                supported = self._coordinator.t4_action_supported(
                    definition.reviewed_action
                )
                result[key] = {
                    "state": "implemented" if supported else "unsupported",
                    "reason": None
                    if supported
                    else "Action is not advertised by this controller",
                    "operation": "t4." + definition.reviewed_action,
                    "app_id": definition.app_id,
                    "writable": supported,
                    "gap": None,
                }
                continue
            view = self.view(key)
            result[key] = {
                "state": view.state,
                "reason": view.reason,
                "writable": view.writable,
                "gap": view.gap or definition.gap,
                "write_gap": "controller.menu_wire"
                if definition.setting is None
                or definition.setting.requires_menu_permission
                else None,
                "binding": definition.binding,
                "app_id": definition.app_id,
            }
        return result

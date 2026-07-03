"""Shared fixtures for Nice integration tests."""

from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from homeassistant.const import (
    CONF_HOST,
    CONF_NAME,
    CONF_PASSWORD,
    CONF_PORT,
    CONF_USERNAME,
)

from custom_components.nice_bidiwifi.client import (
    NiceBidiDeviceInfo,
    NiceBidiServiceCapability,
    NiceBidiStatus,
)
from custom_components.nice_bidiwifi.const import (
    CONF_DEVICE_ID,
    CONF_SOURCE_ID,
    CONF_T4_TIMEOUT_MS,
    CONF_TARGET_MAC,
)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations for all tests."""
    yield


def config_entry_data(**overrides: Any) -> dict[str, Any]:
    """Return valid config entry data with optional overrides."""
    data: dict[str, Any] = {
        CONF_NAME: "Parking Gate",
        CONF_HOST: "192.0.2.10",
        CONF_PORT: 443,
        CONF_TARGET_MAC: "AA:BB:CC:DD:EE:FF",
        CONF_USERNAME: "user",
        CONF_PASSWORD: "AA" * 32,
        CONF_SOURCE_ID: "source",
        CONF_DEVICE_ID: 1,
        CONF_T4_TIMEOUT_MS: 200,
    }
    data.update(overrides)
    return data


def config_entry(entry_id: str = "entry-1", **overrides: Any) -> SimpleNamespace:
    """Return a minimal config entry-like object for direct entity tests."""
    return SimpleNamespace(entry_id=entry_id, data=config_entry_data(**overrides), runtime_data=None)


def make_status(
    *,
    state: str | None = "opening",
    position: float | None = 42.4,
    current_position: int | None = 424,
    closed_position: int | None = 0,
    open_position: int | None = 1000,
) -> NiceBidiStatus:
    """Create a Nice status object."""
    return NiceBidiStatus(
        state=state,
        position=position,
        current_position=current_position,
        closed_position=closed_position,
        open_position=open_position,
        registers={"04/01": "02"},
    )


def make_device_info() -> NiceBidiDeviceInfo:
    """Create static device metadata."""
    return NiceBidiDeviceInfo(
        interface_hw_version="HW1",
        interface_fw_version="FW1",
        interface_manufacturer="NICE",
        interface_product="BiDi-WiFi",
        interface_serial="IFACE123",
        device_type="gate",
        device_manufacturer="NICE",
        device_product="NewRobus",
        device_description="NewRobus",
        device_hw_version="HW2",
        device_fw_version="FG01h",
        device_serial="0E6809FF",
        device_product_detail="detail",
        services=(
            NiceBidiServiceCapability(
                owner="Device",
                owner_id="1",
                name="DoorAction",
                path='Response/Devices/Device[@id="1"]/Services/DoorAction',
                value_type="string",
                permission="w",
                values_raw="open, stop, close",
                values=("open", "stop", "close"),
            ),
        ),
    )


class FakeClient:
    """Client fake for coordinator and entity tests."""

    reconnect_count = 3

    def __init__(self) -> None:
        self.actions: list[str] = []
        self.dep_actions: list[str] = []
        self.closed = False
        self.read_status_result = make_status(state="open", position=100.0, current_position=1000)
        self.read_info_result = make_device_info()
        self.read_status_error: Exception | None = None
        self.read_info_error: Exception | None = None
        self.send_action_error: Exception | None = None
        self.send_dep_action_error: Exception | None = None
        self.info_reads = 0

    def read_status(self) -> NiceBidiStatus:
        """Return status or raise a configured error."""
        if self.read_status_error is not None:
            raise self.read_status_error
        return self.read_status_result

    def read_info(self) -> NiceBidiDeviceInfo:
        """Return device info or raise a configured error."""
        self.info_reads += 1
        if self.read_info_error is not None:
            raise self.read_info_error
        return self.read_info_result

    def send_action(self, action: str) -> None:
        """Record a command or raise a configured error."""
        if self.send_action_error is not None:
            raise self.send_action_error
        self.actions.append(action)

    def send_dep_action(self, action: str) -> None:
        """Record a DEP command or raise a configured error."""
        if self.send_dep_action_error is not None:
            raise self.send_dep_action_error
        self.dep_actions.append(action)

    def close(self) -> None:
        """Record that the client was closed."""
        self.closed = True


class FakeCoordinator:
    """Minimal coordinator fake for entity unit tests."""

    def __init__(self) -> None:
        self.data = make_status()
        self.device_info = make_device_info()
        self.status_polling_supported = True
        self.last_update_success = True
        self.connection_state = "connected"
        self.last_successful_update = datetime(2026, 5, 28, tzinfo=UTC)
        self.last_error = None
        self.client = FakeClient()
        self.last_command = "open"
        self.last_command_latency_ms = 123
        self.calibration_state = "calibrated"
        self.calibration_updated_at = datetime(2026, 5, 27, tzinfo=UTC)
        self.calibration_last_error = None
        self.calibration_quality = "good"
        self.calibration_report_summary = "good: 8/8 repeatable targets"
        self.calibration_report_attributes = {"quality": "good", "point_count": 8}
        self.calls: list[tuple[str, object | None]] = []

    @property
    def display_position(self) -> float | None:
        """Return the displayed cover position."""
        return self.data.position if self.data and self.data.position is not None else None

    @property
    def display_position_estimated(self) -> bool:
        """Return whether the displayed position is estimated."""
        return False

    @property
    def position_simulation_action(self) -> str | None:
        """Return active simulated movement direction."""
        return None

    @property
    def position_simulation_speed_percent_per_second(self) -> float | None:
        """Return active simulated movement speed."""
        return None

    async def async_send_action(self, action: str) -> None:
        """Record a cover action."""
        self.calls.append(("action", action))

    async def async_send_dep_action(self, action: str) -> None:
        """Record a DEP action."""
        self.calls.append(("dep_action", action))

    async def async_set_position(self, position: int) -> None:
        """Record a set-position request."""
        self.calls.append(("position", position))

    async def async_request_refresh(self) -> None:
        """Record a refresh request."""
        self.calls.append(("refresh", None))

    async def async_reconnect(self) -> None:
        """Record a reconnect request."""
        self.calls.append(("reconnect", None))

    async def async_start_position_calibration(self) -> None:
        """Record a calibration request."""
        self.calls.append(("calibrate", None))

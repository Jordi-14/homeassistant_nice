"""Capability discovery service."""

from __future__ import annotations

from dataclasses import replace

from .models.capabilities import NiceCapabilities
from .models.device import NiceDeviceInfo
from .models.status import NiceStatus
from .models.profiles import profile_for_family


class NiceCapabilityService:
    """Build and enrich normalized capabilities for one automation."""

    def __init__(self, device_id: int) -> None:
        self.device_id = device_id

    def discover(
        self,
        device_info: NiceDeviceInfo,
        *,
        status: NiceStatus | None = None,
        previous: NiceCapabilities | None = None,
    ) -> NiceCapabilities:
        """Discover capabilities from INFO and observed normalized status."""
        capabilities = NiceCapabilities.from_device_info(
            device_info,
            self.device_id,
        )
        observed_registers = (
            frozenset(status.registers) if status is not None else frozenset()
        )
        status_sources = set(capabilities.status_sources)
        position_sources: set[str] = set()
        if "04/01" in observed_registers:
            status_sources.add("dmp_04_01")
        if "NHK/T4Status" in observed_registers:
            status_sources.add("t4_live_status")
        if status is not None and status.current_position is not None:
            position_sources.add("dmp_encoder")
        if "NHK/T4InstantPosition" in observed_registers:
            position_sources.add("t4_live_position")
        return replace(
            capabilities,
            profile_key=profile_for_family(capabilities.family).key,
            observed_dmp_registers=observed_registers,
            status_sources=frozenset(status_sources),
            position_sources=frozenset(position_sources),
            local_events=(
                True
                if previous is not None and previous.local_events is True
                else capabilities.local_events
            ),
            diagnostic_events=(
                True
                if previous is not None and previous.diagnostic_events is True
                else capabilities.diagnostic_events
            ),
        )

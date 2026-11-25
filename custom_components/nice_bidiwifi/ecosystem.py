"""Read-only vendor inspection and native-HA ownership boundaries."""

from __future__ import annotations

import asyncio
from dataclasses import asdict
from datetime import UTC, datetime

from homeassistant.exceptions import HomeAssistantError

from .cloud.audit_client import NiceCloudAutomationInspector
from .cloud.session import ephemeral_cloud_session
from .ecosystem_catalog import DISPOSITIONS, category_report, require_inspection


class EcosystemRuntime:
    def __init__(self, owner):
        self.owner = owner
        self.snapshot = None
        self.core_snapshot = None
        self.last_attempt_at = None
        self.last_error_type = None
        self._task = None
        self._closed = False

    async def async_inspect(self, login, *, confirmed=False, operation="inspection"):
        try:
            require_inspection(operation)
            if confirmed is not True:
                raise HomeAssistantError(
                    "Explicit read-only cloud inspection consent is required"
                )
            if self._closed or self.owner.observations.closed:
                raise HomeAssistantError("Integration is shutting down")
            if self._task is not None:
                raise HomeAssistantError("A cloud inspection is already running")
            generation = self.owner.observations.generation
            target = self.owner.entry_config.credentials.target_mac
            self.last_attempt_at = datetime.now(UTC).isoformat()
            with ephemeral_cloud_session(self.owner.hass) as session:
                client = NiceCloudAutomationInspector(session)
                task = self._task = self.owner.hass.async_create_task(
                    client.async_inspect(login, target),
                    name="nice-read-only-cloud-inspection",
                )
                try:
                    snapshot = await task
                    if (
                        self._closed
                        or self.owner.observations.closed
                        or generation != self.owner.observations.generation
                        or target != self.owner.entry_config.credentials.target_mac
                    ):
                        raise HomeAssistantError(
                            "Cloud inspection belongs to an obsolete entry"
                        )
                    if snapshot.state != "failed" or self.snapshot is None:
                        self.snapshot = snapshot
                    self.last_error_type = (
                        "CollectionsUnavailable"
                        if snapshot.state == "failed"
                        else "IncompleteCollections"
                        if snapshot.state == "partial"
                        else None
                    )
                    return snapshot
                except asyncio.CancelledError:
                    self.last_error_type = "Cancelled"
                    raise
                except Exception as error:
                    self.last_error_type = type(error).__name__
                    raise
                finally:
                    self._task = None
                    if not self._closed:
                        self.owner.async_update_listeners()
        finally:
            login.clear()

    async def async_inspect_core(self, *, confirmed=False):
        from .models.capabilities import ProductFamily
        if confirmed is not True or self._closed or self.owner.observations.closed:
            raise HomeAssistantError("Explicit read-only CORE inspection consent is required")
        if self.owner.capabilities is None or self.owner.capabilities.family is not ProductFamily.CORE:
            raise HomeAssistantError("This configured endpoint is not CORE; the standalone CORE reader requires its own explicit credentials")
        generation = self.owner.observations.generation
        snapshot = await self.owner.hass.async_add_executor_job(self.owner.client.read_core_tables)
        if self._closed or generation != self.owner.observations.generation:
            raise HomeAssistantError("CORE inspection belongs to an obsolete entry")
        self.core_snapshot = snapshot.export()
        self.owner.async_update_listeners()
        return self.core_snapshot

    def controller_behavior(self):
        records = {}
        for name, parameter in (
            ("automatic_close", 0x80),
            ("close_after_photocell", 0x84),
            ("always_close", 0x88),
        ):
            observation = self.owner.parameters.observation_for(
                self.owner.parameters.address(4, parameter)
            )
            value = observation.raw_value if observation else None
            records[name] = {
                "enabled_reported": bool(value) if value in {0, 1} else None,
                "observed_at": observation.observed_at.isoformat()
                if observation
                else None,
                "source": observation.source if observation else None,
            }
        return {
            "execution_owner": "Nice controller",
            "settings": records,
            "new_reads_performed": False,
            "settings_modified": False,
            "ha_automations_compared": False,
            "note": "Reported controller modes can act independently of HA; this is not a confirmed conflict.",
        }

    def diagnostics(self):
        return {
            "controller_behavior": self.controller_behavior(),
            "core_table_inspection": self.core_snapshot,
            "last_inspection": self.snapshot.summary() if self.snapshot else None,
            "last_attempt_at": self.last_attempt_at,
            "last_error_type": self.last_error_type,
            "inspection_running": self._task is not None,
            "native_ha_boundaries": [asdict(item) for item in DISPOSITIONS],
            "configured_category": category_report(self.owner.device_info),
            "automatic_cloud_polling": False,
            "resource_synchronization": False,
            "resource_execution": False,
            "account_secrets_persisted": False,
        }

    async def async_close(self):
        self._closed = True
        task = self._task
        if task is not None:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.snapshot = None
        self.core_snapshot = None

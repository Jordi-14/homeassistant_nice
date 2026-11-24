"""Atomic durable enrollment identities; never setup codes or passwords."""

from __future__ import annotations

import asyncio

from homeassistant.helpers.storage import Store

from .enrollment import EnrollmentIdentity
from .errors import NiceParameterError
from .models.discovery import normalize_device_id


class PairingIntents:
    def __init__(self, hass):
        self._store = Store(hass, 1, "nice_bidiwifi.pairing_intents")
        self._lock = hass.data.setdefault(
            "nice_bidiwifi.pairing_intents_lock", asyncio.Lock()
        )

    async def _load(self):
        data = await self._store.async_load()
        if data is not None and (
            not isinstance(data, dict) or not isinstance(data.get("intents"), dict)
        ):
            raise NiceParameterError(
                "Pairing identity storage is malformed; do not create another identity"
            )
        return data or {"intents": {}}

    def _target(self, target):
        target = normalize_device_id(target)
        if target is None:
            raise NiceParameterError("Invalid pairing target")
        return target

    def _record(self, data, target):
        record = data["intents"].get(target)
        if record is not None and (
            not isinstance(record, dict)
            or set(record) != {"username", "source_id", "state", "kind"}
            or record["state"] not in {"created", "dispatched", "rejected", "approved"}
            or record["kind"] not in {"first_user", "request_access"}
        ):
            raise NiceParameterError("Invalid saved pairing identity")
        return record

    async def get(self, target, kind="request_access"):
        target = self._target(target)
        if kind not in {"first_user", "request_access"}:
            raise NiceParameterError("Invalid enrollment kind")
        async with self._lock:
            data = await self._load()
            record = self._record(data, target)
            if record is None:
                identity = EnrollmentIdentity.new(target)
                record = {
                    "username": identity.username,
                    "source_id": identity.source_id,
                    "state": "created",
                    "kind": kind,
                }
                data["intents"][target] = record
                await self._store.async_save(data)
            elif record["kind"] != kind and record["state"] in {"created", "rejected"}:
                record["kind"] = kind
                await self._store.async_save(data)
            return (
                EnrollmentIdentity(record["username"], record["source_id"], target),
                record["state"],
                record["kind"],
            )

    async def claim(self, identity):
        target = self._target(identity.target_mac)
        async with self._lock:
            data = await self._load()
            record = self._record(data, target)
            if (
                record is None
                or record["username"] != identity.username
                or record["source_id"] != identity.source_id
            ):
                raise NiceParameterError("Enrollment identity changed")
            if record["state"] not in {"created", "rejected"}:
                return False
            record["state"] = "dispatched"
            await self._store.async_save(data)
            return True

    async def set_state(self, target, state):
        if state not in {"rejected", "approved"}:
            raise NiceParameterError("Invalid enrollment transition")
        target = self._target(target)
        async with self._lock:
            data = await self._load()
            record = self._record(data, target)
            if record is None:
                raise NiceParameterError("No saved enrollment identity")
            record["state"] = state
            await self._store.async_save(data)

    async def complete_if_bound(self, credentials):
        target = self._target(credentials.target_mac)
        async with self._lock:
            data = await self._load()
            record = self._record(data, target)
            if (
                record is not None
                and record["username"] == credentials.username
                and record["source_id"] == credentials.source
            ):
                data["intents"].pop(target)
                await self._store.async_save(data)

    async def complete(self, target):
        target = self._target(target)
        async with self._lock:
            data = await self._load()
            data["intents"].pop(target, None)
            await self._store.async_save(data)

"""Bounded, source-labelled evidence. Correlation never establishes physical cause."""

from __future__ import annotations

from collections import deque
from datetime import UTC, datetime
import json
import math
import time

from .redaction import configured_secrets, redact_text

MAX_TIMELINE_RECORDS = 256
MAX_EVIDENCE_FIELDS = 24
VALUE_FIELDS = frozenset(
    {
        "register",
        "state",
        "position",
        "encoder",
        "command",
        "acknowledgement",
        "verification",
        "error_code",
        "payload_index",
        "frame_payloads_truncated",
        "payload_kind",
        "event_class",
        "event_description",
        "raw_position",
        "position_scale",
        "manual_command_code",
        "channel_code",
        "protocol_id",
        "protocol_time",
        "category",
        "change_state",
        "cause",
        "basic_diagnostic",
        "advanced_diagnostic",
        "obstruction",
        "force_limiter_state",
        "torque_m1",
        "torque_m2",
        "speed_m1",
        "speed_m2",
        "voltage_m1",
        "voltage_m2",
        "work_time_left",
        "battery_device_type",
        "battery_level_code",
        "reset_cause",
        "maintenance_count",
        "maintenance_threshold",
        "recent_ha_command",
        "relationship",
        "reason",
        "confidence",
        "physical_cause",
        "actor",
        "device_scope",
        "history_id",
    }
)


class IncidentTimeline:
    def __init__(self, configuration, *, clock=time.monotonic):
        self._clock = clock
        self._secrets = configured_secrets(configuration)
        self.records = deque(maxlen=MAX_TIMELINE_RECORDS)
        self.sequence = 0
        self.session = 0
        self.duplicates = 0
        self._last_wire = None
        self._last_wire_time = 0.0
        self._state = None
        self._obstruction = None
        self._last_command = None

    def _clean(self, values):
        result = {}
        for key, value in values.items():
            if (
                key not in VALUE_FIELDS
                or len(result) >= MAX_EVIDENCE_FIELDS
                or value is None
            ):
                continue
            if isinstance(value, float) and not math.isfinite(value):
                continue
            if isinstance(value, str):
                value = redact_text(value, self._secrets)[:128]
            if isinstance(value, (str, int, float, bool)):
                result[key] = value
        return result

    def add(
        self, kind, source, values, *, received_at=None, timestamp_basis="ha_receipt"
    ):
        if kind not in {
            "command",
            "event",
            "measurement",
            "load",
            "history",
            "interface_log",
            "incident",
            "connection",
            "verification",
        }:
            raise ValueError("Unknown evidence kind")
        if source not in {
            "ha",
            "nhk",
            "t4",
            "poll",
            "t4_logs",
            "interface_logs",
            "motion_trace",
        }:
            raise ValueError("Unknown evidence source")
        self.sequence += 1
        self.records.append(
            {
                "sequence": self.sequence,
                "session": self.session,
                "kind": kind,
                "source": source,
                "received_at": received_at or datetime.now(UTC).isoformat(),
                "timestamp_basis": timestamp_basis,
                "observed_monotonic": self._clock(),
                "values": self._clean(values),
            }
        )

    def new_session(self):
        self.session += 1
        self._last_wire = None
        self._obstruction = None
        self._state = None
        self._last_command = None
        self.add("connection", "ha", {"state": "reconnecting"})

    def duplicate(self, event):
        # Consecutive retransmissions only. Do not collapse distinct cycles,
        # infer identities from timestamps, or deduplicate anonymous pulses.
        if event.event_id is None or event.protocol_timestamp is None:
            return False
        values = event.as_diagnostics()
        values.pop("received_at", None)
        key = json.dumps(values, sort_keys=True)
        now = self._clock()
        duplicate = key == self._last_wire and now - self._last_wire_time < 2
        self._last_wire, self._last_wire_time = key, now
        if duplicate:
            self.duplicates += 1
        return duplicate

    def command(self, command, result):
        self._last_command = (command.key, self._clock())
        self.add(
            "command",
            "ha",
            {
                "command": command.key,
                "acknowledgement": str(result.acknowledgement),
                "verification": str(result.verification),
                "error_code": result.error_code,
            },
        )

    def verification(self, command, result):
        values = {
            "command": command.key,
            "acknowledgement": str(result.acknowledgement),
            "verification": str(result.verification),
        }
        self.add("verification", "ha", values)

    def event(self, event):
        source = "t4" if event.t4_payload_kind else "nhk"
        values = {
            "state": event.state,
            "position": event.position,
            "protocol_id": event.event_id,
            "frame_payloads_truncated": event.t4_evidence_truncated,
            "protocol_time": event.protocol_timestamp,
            "category": str(event.category),
            "change_state": event.t4_change_state,
            "event_description": event.t4_event_description,
            "cause": event.t4_cause or event.cause_code,
            "basic_diagnostic": event.t4_basic_diagnostic
            or event.basic_diagnostic_code,
            "advanced_diagnostic": event.t4_advanced_diagnostic
            or event.advanced_diagnostic_code,
            "obstruction": event.obstruction,
            "battery_device_type": event.battery_device_type,
            "battery_level_code": event.battery_level_code,
            "reset_cause": event.reset_cause,
            "maintenance_count": event.manoeuvre_count,
            "maintenance_threshold": event.manoeuvre_threshold,
            "confidence": "reported",
            "physical_cause": "unknown",
            "actor": "unknown",
        }
        values.update(dict(event.t4_details or ()))
        parts = [values]
        if event.t4_evidence:
            # Keep the legacy merged view out of per-payload causal analysis.
            parts = [
                {
                    **{
                        key: values.get(key)
                        for key in (
                            "protocol_id",
                            "frame_payloads_truncated",
                            "protocol_time",
                            "confidence",
                            "physical_cause",
                            "actor",
                        )
                    },
                    **dict(part),
                }
                for part in event.t4_evidence
            ]
            if event.raw_state is not None or event.obstruction_source == "nhk":
                self.add(
                    "event",
                    "nhk",
                    {
                        "state": event.raw_state,
                        "obstruction": event.obstruction
                        if event.obstruction_source == "nhk"
                        else None,
                        "protocol_id": event.event_id,
                        "protocol_time": event.protocol_timestamp,
                    },
                    received_at=event.received_at.isoformat(),
                )
        for part in parts:
            self.add("event", source, part, received_at=event.received_at.isoformat())
            reason = None
            if part.get("change_state") in {
                "force_limiter_intervent",
                "fototest_failed",
                "memory_err",
                "hw_err",
                "double_cmd_err",
                "lockedmotor_err",
            }:
                reason = "controller_reported_" + part["change_state"]
            elif part.get("obstruction") is True and (
                self._obstruction is False or event.obstruction_evidence == "new_edge"
            ):
                reason = "controller_obstruction_flag_rose"
            elif part.get("change_state") == "stopped_for_command_arrival":
                reason = "command_arrival"
            if part.get("obstruction") is not None:
                self._obstruction = part["obstruction"]
            if reason:
                incident = {
                    **part,
                    "reason": reason,
                    "physical_cause": "unknown",
                    "actor": "unknown",
                }
                if self._last_command and self._clock() - self._last_command[1] <= 5:
                    incident["recent_ha_command"] = self._last_command[0]
                    incident["relationship"] = "temporal_only"
                self.add(
                    "incident",
                    source,
                    incident,
                    received_at=event.received_at.isoformat(),
                )

    def measurement(self, status):
        fresh = status.observed_fields
        if (fresh is None or "obstacle" in fresh) and status.obstacle is not None:
            self._obstruction = status.obstacle
        values = {}
        for field, key in [
            ("state", "state"),
            ("position", "position"),
            ("current_position", "encoder"),
        ]:
            if fresh is None or field in fresh:
                values[key] = getattr(status, field)
        if not values:
            return
        current = values.get("state")
        if {self._state, current} == {"opening", "closing"}:
            self.add(
                "incident",
                "poll",
                {
                    "reason": "direction_reversal",
                    "confidence": "reported_state_transition",
                    "physical_cause": "unknown",
                    "actor": "unknown",
                    "state": current,
                },
            )
        if current is not None:
            self._state = current
        # A stationary repeated poll need not consume evidence capacity.
        cleaned = self._clean(values)
        if (
            self.records
            and self.records[-1]["kind"] == "measurement"
            and self.records[-1]["values"] == cleaned
        ):
            return
        self.add("measurement", "poll", cleaned)
        self.records[-1]["field_observation_monotonic"] = {
            key: stamp
            for name, key in (
                ("state", "state"),
                ("position", "position"),
                ("current_position", "encoder"),
            )
            if (stamp := (status.observation_times or {}).get(name)) is not None
            and key in cleaned
        }

    def history(self, rows):
        for row in rows:
            values = {
                "history_id": row.id,
                "event_description": row.event_description,
                "protocol_time": row.time,
                "change_state": row.change_state,
                "cause": row.cause,
                "basic_diagnostic": row.basic_diagnostic,
                "advanced_diagnostic": row.advanced_diagnostic,
                "command": row.command,
                "confidence": "reported",
                "physical_cause": "unknown",
                "actor": "unknown",
            }
            values.update(dict(row.details))
            cleaned = self._clean(values)
            if any(
                r["kind"] == "history"
                and r["session"] == self.session
                and r["values"] == cleaned
                for r in self.records
            ):
                continue
            self.add(
                "history",
                "t4_logs",
                cleaned,
                timestamp_basis="controller_time_unverified",
            )

    def interface_logs(self, snapshot):
        for event in snapshot.events:
            values = {
                "state": event.door_status,
                "command": event.door_action or event.t4_action,
                "protocol_time": event.time,
                "reason": event.event_type,
                "actor": "unknown",
                "confidence": "reported",
                "physical_cause": "unknown",
            }
            cleaned = self._clean(values)
            if any(
                r["kind"] == "interface_log"
                and r["session"] == self.session
                and r["values"] == cleaned
                for r in self.records
            ):
                continue
            self.add(
                "interface_log",
                "interface_logs",
                cleaned,
                timestamp_basis="module_time_unverified",
            )

    def export(self):
        now = self._clock()
        return {
            "limit": MAX_TIMELINE_RECORDS,
            "duplicates_suppressed": self.duplicates,
            "clock_policy": "HA receipt orders observations; unzoned module/controller times are not normalized",
            "physical_cause_policy": "unknown unless independently established; command proximity is temporal only",
            "records": [
                {
                    **{
                        k: v
                        for k, v in row.items()
                        if k
                        not in {"observed_monotonic", "field_observation_monotonic"}
                    },
                    "age_seconds": round(max(0, now - row["observed_monotonic"]), 3),
                    "field_age_seconds": {
                        key: round(max(0, now - stamp), 3)
                        for key, stamp in row.get(
                            "field_observation_monotonic", {}
                        ).items()
                    },
                }
                for row in self.records
            ],
        }

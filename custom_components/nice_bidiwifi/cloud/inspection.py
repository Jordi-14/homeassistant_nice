"""Private, scoped Nice automation records. No synchronization or execution."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
import re

from ..errors import NiceCloudAccessError, NiceCloudSchemaError
from ..models.discovery import normalize_device_id

MAX_RECORDS = 256


def _mac(value):
    return normalize_device_id(value) if isinstance(value, str) else None


def _object(value):
    if not isinstance(value, Mapping):
        raise NiceCloudSchemaError("Unsupported cloud object")
    return value


def _list(value):
    if not isinstance(value, list) or len(value) > MAX_RECORDS:
        raise NiceCloudSchemaError("Missing or oversized cloud collection")
    return value


def _id(value):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, str))
        or not re.fullmatch(r"[0-9]{1,18}", str(value))
        or int(value) <= 0
    ):
        raise NiceCloudSchemaError("Unsupported cloud resource identity")
    return int(value)


def _text(value, limit=128):
    if value is None:
        return None
    if (
        not isinstance(value, str)
        or len(value) > limit
        or any(ord(c) < 32 for c in value)
    ):
        raise NiceCloudSchemaError("Unsupported cloud text field")
    return value


def successful(payload, *, require_result=True):
    root = _object(payload)
    result = root.get("result")
    if (
        require_result
        and result is not True
        or result is not None
        and result is not True
        or root.get("errorCode") is not None
        and root.get("errorCode") != ""
    ):
        raise NiceCloudSchemaError("The cloud service did not report success")
    return root


KNOWN_ERROR_IDS = frozenset(
    {
        "ERROR_GENERIC",
        "INVALID_ARGUMENT",
        "UNAUTHENTICATED",
        "PERMISSION_DENIED",
        "NOT_FOUND",
        "ABORTED",
        "TARGET_RESOURCE_IS_NO_LONGER_AVAILABLE",
        "INTERNAL_SERVER_ERROR",
        "RECORD_NOT_FOUND",
        "ACCESSORY_NOT_FOUND",
        "NO_ACCESSORIES_ONLINE",
        "ERROR_COMMAND_NHK_TIMEOUT",
        "HOME_PERMISSION_ACCESS_DENY",
        "COMMAND_ERROR",
        "OFFLINE",
        "ACCESSORY_MAC_ADDRESS_BUSY_ACTIVITY",
        "400",
        "401",
        "403",
        "404",
        "409",
        "410",
        "500",
    }
)


def recognized_error(value):
    # These fixed identifiers are declared by the supplied SDK, not arbitrary
    # response text or a regex accepting user IDs/password-like strings.
    if isinstance(value, bool):
        return "unrecognized"
    if isinstance(value, int):
        value = str(value)
    return (
        value if isinstance(value, str) and value in KNOWN_ERROR_IDS else "unrecognized"
    )


def cloud_identifier(value, target):
    value = _text(value, 64)
    if value is None or value.strip() != value or _mac(value) != target:
        raise NiceCloudSchemaError("Invalid cloud accessory identifier")
    return value


def collection_shape(payload):
    """Retain only fixed field types, never arbitrary keys or payload values."""

    def kind(value):
        if value is None:
            return "null"
        if isinstance(value, list):
            return "list"
        if isinstance(value, Mapping):
            return "object"
        if isinstance(value, bool):
            return "boolean"
        if isinstance(value, str):
            return "string"
        if isinstance(value, int | float):
            return "number"
        return "other"

    if not isinstance(payload, Mapping):
        return {"root": kind(payload)}
    shape = {
        key: kind(payload[key]) if key in payload else "missing"
        for key in ("scenes", "rules", "data")
    }
    result = payload.get("result")
    shape["result"] = (
        "true"
        if result is True
        else "false"
        if result is False
        else "null"
        if "result" in payload and result is None
        else "missing"
        if "result" not in payload
        else "other"
    )
    shape["error_code_present"] = (
        payload.get("errorCode") is not None and payload.get("errorCode") != ""
    )
    if shape["error_code_present"]:
        shape["recognized_error_code"] = recognized_error(payload.get("errorCode"))
    if payload.get("message") is not None:
        shape["recognized_error_description"] = recognized_error(payload.get("message"))
    if isinstance(payload.get("scenes"), list):
        shape["scenes_length"] = (
            len(payload["scenes"])
            if len(payload["scenes"]) <= MAX_RECORDS
            else "oversized"
        )
    if isinstance(payload.get("rules"), Mapping):
        shape.update(
            {
                "rules." + key: kind(payload["rules"][key])
                if key in payload["rules"]
                else "missing"
                for key in ("rule", "timers")
            }
        )
        for key in ("rule", "timers"):
            value = payload["rules"].get(key)
            if isinstance(value, list):
                shape["rules." + key + "_length"] = (
                    len(value) if len(value) <= MAX_RECORDS else "oversized"
                )
    return shape


def empty_lookup_reply(root, collection):
    """Only the SDK's specific RECORD_NOT_FOUND plus explicit empty lists."""
    if not isinstance(root, Mapping) or root.get("result") is not False:
        return False
    code = recognized_error(root.get("errorCode"))
    description = recognized_error(root.get("message"))
    if description not in {"RECORD_NOT_FOUND", "unrecognized"}:
        return False
    if code != "RECORD_NOT_FOUND" and not (
        code in {"404", "NOT_FOUND", "ERROR_GENERIC"}
        and description == "RECORD_NOT_FOUND"
    ):
        return False
    if collection == "scenes":
        return root.get("scenes") == []
    groups = root.get("rules")
    return (
        isinstance(groups, Mapping)
        and groups.get("rule") == []
        and groups.get("timers") == []
    )


def reported_list(root, key):
    # Welcome SceneRepositoryImpl maps a successful nullable collection to an
    # empty list. Require explicit success and an explicit null, not omission.
    if root.get("result") is True and key in root and root[key] is None:
        return []
    return root.get(key)


@dataclass(frozen=True, slots=True, repr=False)
class InspectionScope:
    account_id: int
    home_id: int | None
    target_mac: str
    cloud_role: str
    cloud_accessory_id: str | None = None

    @property
    def request_identifier(self):
        return (
            self.cloud_accessory_id
            if self.cloud_accessory_id is not None
            else self.target_mac
        )

    @property
    def identifier_format(self):
        value = self.request_identifier
        return (
            "compact"
            if len(value) == 12
            else "colon_separated"
            if ":" in value
            else "hyphen_separated"
            if "-" in value
            else "other_verified"
        )


@dataclass(frozen=True, slots=True, repr=False)
class AutomationRecord:
    kind: str
    resource_id: int
    name: str | None
    reported_status: str | None
    reported_time: str | None = None
    reported_repeat: str | None = None
    reported_day_time: str | None = None
    parent_rule_id: int | None = None


@dataclass(frozen=True, slots=True, repr=False)
class RecordCollection:
    kind: str
    state: str
    items: tuple[AutomationRecord, ...] = ()
    error_type: str | None = None
    response_shape: dict | None = None

    def summary(self):
        return {
            "state": self.state,
            "count": len(self.items) if self.state == "reported" else None,
            "error_type": self.error_type,
            "response_shape": self.response_shape,
        }


@dataclass(frozen=True, slots=True, repr=False)
class AutomationInspection:
    scope: InspectionScope
    scenes: RecordCollection
    rules: RecordCollection
    timers: RecordCollection
    captured_at: str

    @property
    def state(self):
        collections = (self.scenes, self.rules, self.timers)
        if all(section.state != "reported" for section in collections):
            return "failed"
        if any(section.state != "reported" for section in collections):
            return "partial"
        return (
            "records_found"
            if any(section.items for section in collections)
            else "no_records_reported"
        )

    def summary(self):
        return {
            "state": self.state,
            "captured_at": self.captured_at,
            "source": "Nice cloud records for the configured accessory/gateway",
            "account_accessory_scope_verified": True,
            "logical_device_mapping_verified": False,
            "home_scope_reported": self.scope.home_id is not None,
            "cloud_identifier_format": self.scope.identifier_format,
            "cloud_role_reported": self.scope.cloud_role,
            "scenes": self.scenes.summary(),
            "rules": self.rules.summary(),
            "timers": self.timers.summary(),
            "device_execution_confirmed": False,
            "ha_automations_compared": False,
            "potential_overlap": True
            if any(section.items for section in (self.rules, self.timers))
            else None
            if any(section.state != "reported" for section in (self.rules, self.timers))
            else False,
            "confirmed_conflicts": None,
            "ha_resources_created": False,
            "vendor_resources_modified": False,
            "private_record_details_exported": False,
        }

    def private_lines(self):
        # Only local authenticated options screens use these values. Never put
        # names, IDs, exact times/repetitions or member details into diagnostics.
        lines = []
        for section in (self.scenes, self.rules, self.timers):
            if section.state != "reported":
                lines.append(f"{section.kind}: unavailable; absence is not established")
                continue
            lines.append(f"{section.kind}: {len(section.items)} reported record(s)")
            for index, item in enumerate(section.items, 1):
                parts = [
                    f"{index}. {item.name or item.kind}",
                    f"Nice-reported status: {item.reported_status or 'not reported'}",
                ]
                if item.kind == "timer":
                    parts += [
                        f"Nice-reported time: {item.reported_time or 'not reported'}",
                        f"repeat: {item.reported_repeat or 'not reported'}",
                        f"dayTime: {item.reported_day_time or 'not reported'}",
                    ]
                lines.append("; ".join(parts))
        # Escape Markdown so remote names cannot inject links or formatting.
        return "\n\n".join(
            re.sub(r"([\\`*_{}\[\]()<>#!|~])", r"\\\1", line) for line in lines
        )


def bind_scope(payload, target_mac):
    root = successful(payload)
    data = _object(root.get("data"))
    account_id = _id(_object(data.get("user")).get("id"))
    target = _mac(target_mac)
    if target is None:
        raise NiceCloudSchemaError("Configured accessory identity is invalid")
    homes = _list(data["homes"]) if data.get("homes") is not None else []
    matches, seen = [], set()
    for raw_home in homes:
        home = _object(raw_home)
        home_id = _id(home.get("id"))
        if home_id in seen:
            raise NiceCloudSchemaError("Duplicate cloud home identity")
        seen.add(home_id)
        accessories = home.get("accessories")
        if accessories is None:
            continue
        identifiers = {
            cloud_identifier(_object(a).get("accessoryMacAddress"), target)
            for a in _list(accessories)
            if _mac(_object(a).get("accessoryMacAddress")) == target
        }
        if len(identifiers) > 1:
            raise NiceCloudSchemaError(
                "Ambiguous cloud accessory identifier representation"
            )
        if identifiers:
            matches.append((home, next(iter(identifiers))))
    if len(matches) > 1:
        raise NiceCloudSchemaError(
            "Configured accessory has an ambiguous cloud home scope"
        )
    if matches:
        home, request_identifier = matches[0]
        roles = [
            u.get("userRoleHome")
            for u in _list(home.get("users") or [])
            if isinstance(u, Mapping)
            and not isinstance(u.get("id"), bool)
            and u.get("id") == account_id
        ]
        role = (
            roles[0]
            if len(roles) == 1
            and isinstance(roles[0], str)
            and roles[0] in {"ADMIN", "USER", "DISABLE", "GUEST"}
            else "UNKNOWN"
        )
        if role == "DISABLE":
            raise NiceCloudAccessError("The cloud home reports disabled access")
        return InspectionScope(
            account_id, _id(home["id"]), target, role, request_identifier
        )
    # A per-accessory credential record also proves the account/device binding.
    # Never parse, retain or replace its password or NHK account fields here.
    for smart in _list(data.get("smartDevices") or []):
        for credential in _list(_object(smart).get("accessoryCredentials") or []):
            if _mac(_object(credential).get("accessoryMacAddress")) == target:
                return InspectionScope(
                    account_id,
                    None,
                    target,
                    "UNKNOWN",
                    cloud_identifier(credential.get("accessoryMacAddress"), target),
                )
    raise NiceCloudAccessError(
        "The configured accessory is not associated with this cloud account"
    )


def _scoped_rows(rows, scope, kind):
    records, seen = [], set()
    for value in _list(rows):
        row = _object(value)
        if _mac(row.get("accessoryMacAddress")) != scope.target_mac:
            raise NiceCloudSchemaError(
                "A cloud collection returned a different accessory scope"
            )
        resource_id = _id(row.get("id"))
        if resource_id in seen:
            raise NiceCloudSchemaError("Duplicate cloud resource identity")
        seen.add(resource_id)
        records.append(
            AutomationRecord(
                kind,
                resource_id,
                _text(row.get("sceneName" if kind == "scene" else "ruleName")),
                _text(row.get("status" if kind == "scene" else "ruleStatus"), 64),
            )
        )
    return tuple(records)


def parse_scenes(payload, scope):
    shape = collection_shape(payload)
    if empty_lookup_reply(payload, "scenes"):
        shape["empty_reply_basis"] = "vendor_record_not_found"
        return RecordCollection("scenes", "reported", response_shape=shape)
    try:
        root = successful(payload, require_result=False)
        items = _scoped_rows(reported_list(root, "scenes"), scope, "scene")
        return RecordCollection("scenes", "reported", items, response_shape=shape)
    except NiceCloudSchemaError:
        return RecordCollection(
            "scenes",
            "unavailable",
            error_type="UnsupportedOrUnscopedResponse",
            response_shape=shape,
        )


def parse_rules(payload, scope):
    shape = collection_shape(payload)
    if empty_lookup_reply(payload, "rules"):
        shape["empty_reply_basis"] = "vendor_record_not_found"
        return (
            RecordCollection("rules", "reported", response_shape=shape),
            RecordCollection("timers", "reported", response_shape=shape),
        )
    try:
        root = successful(payload, require_result=False)
        if root.get("result") is True and "rules" in root and root["rules"] is None:
            return (
                RecordCollection("rules", "reported", response_shape=shape),
                RecordCollection("timers", "reported", response_shape=shape),
            )
        groups = _object(root.get("rules"))
    except NiceCloudSchemaError:
        return (
            RecordCollection(
                "rules",
                "unavailable",
                error_type="UnsupportedResponse",
                response_shape=shape,
            ),
            RecordCollection(
                "timers",
                "unavailable",
                error_type="UnsupportedResponse",
                response_shape=shape,
            ),
        )
    try:
        rows = (
            []
            if root.get("result") is True
            and "rule" in groups
            and groups["rule"] is None
            else groups.get("rule")
        )
        items = _scoped_rows(rows, scope, "rule")
        rules = RecordCollection("rules", "reported", items, response_shape=shape)
    except NiceCloudSchemaError:
        rules = RecordCollection(
            "rules",
            "unavailable",
            error_type="UnsupportedOrUnscopedResponse",
            response_shape=shape,
        )
    try:
        timers, seen = [], set()
        parent_names = {item.resource_id: item.name for item in rules.items}
        rows = (
            []
            if root.get("result") is True
            and "timers" in groups
            and groups["timers"] is None
            else groups.get("timers")
        )
        for value in _list(rows):
            row = _object(value)
            resource_id, parent_id = _id(row.get("id")), _id(row.get("ruleId"))
            if resource_id in seen or parent_id not in parent_names:
                raise NiceCloudSchemaError("Unbound or duplicate timer")
            seen.add(resource_id)
            timers.append(
                AutomationRecord(
                    "timer",
                    resource_id,
                    parent_names[parent_id],
                    _text(row.get("timerStatus"), 64),
                    _text(row.get("scheduledTime"), 64),
                    _text(row.get("timerRepeat"), 64),
                    _text(row.get("dayTime"), 64),
                    parent_id,
                )
            )
        timer_collection = RecordCollection(
            "timers", "reported", tuple(timers), response_shape=shape
        )
    except NiceCloudSchemaError:
        timer_collection = RecordCollection(
            "timers",
            "unavailable",
            error_type="UnsupportedOrUnboundResponse",
            response_shape=shape,
        )
    return rules, timer_collection


def inspection_now(scope, scenes, rules, timers):
    return AutomationInspection(
        scope, scenes, rules, timers, datetime.now(UTC).isoformat()
    )


def inspect_macro_inventory(payload, scope):
    root = successful(payload)
    if scope.home_id is None:
        sections = [
            RecordCollection(kind, "unavailable", error_type="NoBoundHomeInventory")
            for kind in ("scenes", "rules", "timers")
        ]
        return inspection_now(scope, *sections)
    homes = _list(_object(root.get("data")).get("homes"))
    matches = [
        home
        for home in homes
        if isinstance(home, Mapping) and _id(home.get("id")) == scope.home_id
    ]
    if scope.home_id is None or len(matches) != 1:
        sections = [
            RecordCollection(kind, "unavailable", error_type="NoBoundHomeInventory")
            for kind in ("scenes", "rules", "timers")
        ]
        return inspection_now(scope, *sections)
    home = matches[0]
    shape = collection_shape(
        {
            "result": True,
            **{key: home[key] for key in ("scenes", "rules") if key in home},
        }
    )
    shape["inventory_source"] = "macro_user_home"
    # A home's unfiltered list length can include other gateways. Expose only
    # this accessory's parsed counts, not unrelated home-resource counts.
    shape = {key: value for key, value in shape.items() if not key.endswith("_length")}

    def selected(rows):
        output = []
        for value in _list(rows):
            row = _object(value)
            mac = _mac(row.get("accessoryMacAddress"))
            if mac is None:
                raise NiceCloudSchemaError("A home record has no accessory scope")
            if mac == scope.target_mac:
                output.append(row)
        return output

    try:
        if "scenes" not in home:
            raise NiceCloudSchemaError("Home scene inventory was not reported")
        rows = [] if home["scenes"] is None else home["scenes"]
        scenes = parse_scenes({"result": True, "scenes": selected(rows)}, scope)
    except NiceCloudSchemaError:
        scenes = RecordCollection(
            "scenes", "unavailable", error_type="MissingOrUnscopedHomeInventory"
        )
    try:
        if "rules" not in home:
            raise NiceCloudSchemaError("Home rule inventory was not reported")
        groups = [] if home["rules"] is None else _list(home["rules"])
        all_rules, all_timers = [], []
        for value in groups:
            group = _object(value)
            if "rule" not in group or "timers" not in group:
                raise NiceCloudSchemaError("Home rule group is incomplete")
            all_rules += [] if group["rule"] is None else _list(group["rule"])
            all_timers += [] if group["timers"] is None else _list(group["timers"])
            if len(all_rules) > MAX_RECORDS or len(all_timers) > MAX_RECORDS:
                raise NiceCloudSchemaError("Home inventory exceeds retention limit")
        parent_scopes = {}
        for value in all_rules:
            row = _object(value)
            identity = _id(row.get("id"))
            mac = _mac(row.get("accessoryMacAddress"))
            if identity in parent_scopes or mac is None:
                raise NiceCloudSchemaError("Ambiguous home rule identity")
            parent_scopes[identity] = mac
        timers = []
        for value in all_timers:
            row = _object(value)
            parent = _id(row.get("ruleId"))
            if parent not in parent_scopes:
                raise NiceCloudSchemaError("Home timer has no authoritative parent")
            if parent_scopes[parent] == scope.target_mac:
                timers.append(row)
        rules, timer_section = parse_rules(
            {"result": True, "rules": {"rule": selected(all_rules), "timers": timers}},
            scope,
        )
    except NiceCloudSchemaError:
        rules = RecordCollection(
            "rules", "unavailable", error_type="MissingOrUnscopedHomeInventory"
        )
        timer_section = RecordCollection(
            "timers", "unavailable", error_type="MissingOrUnboundHomeInventory"
        )
    from dataclasses import replace

    sections = [
        replace(section, response_shape=shape)
        for section in (scenes, rules, timer_section)
    ]
    return inspection_now(scope, *sections)

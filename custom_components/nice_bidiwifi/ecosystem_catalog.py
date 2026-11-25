"""HA owns organization and execution; vendor resources are not mirrored."""

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class EcosystemDisposition:
    key: str
    title: str
    disposition: str
    owner: str
    explanation: str
    gap: str | None = None


DISPOSITIONS = (
    EcosystemDisposition(
        "inspection",
        "Read-only Nice automation inspection",
        "implemented",
        "Nice cloud record; execution unverified",
        "Explicit one-time account/device-scoped reads. No resource is imported, changed or executed.",
    ),
    EcosystemDisposition(
        "areas",
        "Homes, rooms and areas",
        "ha_native",
        "Home Assistant",
        "Use HA areas and floors to organize entities; Nice home/room resources are not synchronized.",
    ),
    EcosystemDisposition(
        "dashboards",
        "Dashboard and favorites organization",
        "ha_native",
        "Home Assistant",
        "Use HA dashboards; the integration creates no dashboard or favorites mirror.",
    ),
    EcosystemDisposition(
        "groups",
        "Groups of HA entities",
        "ha_native",
        "Home Assistant",
        "Use native HA groups/helpers. Existing Nice module-group observations do not create HA groups.",
    ),
    EcosystemDisposition(
        "scenes",
        "Scenes and macros",
        "ha_native",
        "Home Assistant",
        "Use native HA scenes/scripts; existing Nice scenes remain external records and are never replayed or mirrored.",
    ),
    EcosystemDisposition(
        "schedules",
        "Schedules and rules",
        "ha_native",
        "Home Assistant",
        "Use HA schedule helpers and automations. There is no second scheduler in the integration.",
    ),
    EcosystemDisposition(
        "notifications",
        "Notifications",
        "ha_native",
        "Home Assistant",
        "Use native HA automations/notification actions with existing Nice entities and protocol events. The integration sends no notification.",
    ),
    EcosystemDisposition(
        "permissions",
        "HA users and permissions",
        "ha_native",
        "Home Assistant",
        "HA users, Nice home membership and module permissions are different authorization scopes.",
    ),
    EcosystemDisposition(
        "sharing",
        "Nice account ownership and sharing",
        "manufacturer_app",
        "Nice account",
        "Manage Nice ownership, invitations and cloud sharing in the manufacturer app; module admin is not cloud ownership.",
    ),
    EcosystemDisposition(
        "vendor_resources",
        "Manage existing vendor automations",
        "manufacturer_app",
        "Nice/CORE",
        "Use the manufacturer app to edit or disable existing Nice scenes/rules/timers. HA never edits them or silently creates equivalents.",
    ),
    EcosystemDisposition(
        "service_linking",
        "Vendor assistant/service linking",
        "manufacturer_app",
        "Nice/vendor service",
        "Nice account linking and vendor-managed assistant services remain in the manufacturer app. HA integrations are configured natively in HA.",
    ),
    EcosystemDisposition(
        "core_execution",
        "Verify actual CORE automation execution",
        "blocked",
        "Nice CORE",
        "Cloud records do not establish current module table contents, execution state or schedule timezone.",
        "ecosystem.core_execution",
    ),
    EcosystemDisposition(
        "device_adapters",
        "Additional Nice device categories",
        "blocked",
        "Nice device",
        "Domain contracts are declared; missing control/state/transport codecs do not create fake light, relay, lock or blind entities.",
        "ecosystem.device_adapter",
    ),
)
DISPOSITIONS_BY_KEY = {item.key: item for item in DISPOSITIONS}


def require_inspection(operation):
    from .feature_catalog import FeatureBlocked

    if operation != "inspection":
        item = DISPOSITIONS_BY_KEY.get(operation)
        raise FeatureBlocked(
            item.explanation
            if item
            else "No vendor mutation or resource-execution operation is available"
        )


@dataclass(frozen=True, slots=True)
class CategoryAdapter:
    key: str
    ha_domain: str | None
    device_class: str | None
    gate_adapter: bool
    gap: str | None = None


GATE = CategoryAdapter("gate", "cover", "gate", True)
DOOR = CategoryAdapter("door", "cover", "door", True)
GARAGE = CategoryAdapter("garage_door", "cover", "garage", True)
LIGHT = CategoryAdapter("light", "light", None, False, "ecosystem.device_adapter")
RELAY = CategoryAdapter("relay", "switch", None, False, "ecosystem.device_adapter")
LOCK = CategoryAdapter(
    "electrical_lock", "lock", None, False, "ecosystem.device_adapter"
)
SCREEN = CategoryAdapter("screen", "cover", None, False, "ecosystem.device_adapter")
BLIND = CategoryAdapter("blind", "cover", "blind", False, "ecosystem.device_adapter")
SHUTTER = CategoryAdapter(
    "shutter", "cover", "shutter", False, "ecosystem.device_adapter"
)
UNKNOWN = CategoryAdapter("unknown", None, None, False, "ecosystem.device_adapter")

CATEGORY_TYPES = {
    **dict.fromkeys(
        [
            "GATE.DEFAULT",
            "GATE.SWING",
            "GATE.SLIDING",
            "GATE.PEDESTRIAN",
            "GATE.PARKING",
            "GATE",
            "SLIDING",
            "SWING",
            "BARRIER",
        ],
        GATE,
    ),
    **dict.fromkeys(
        [
            "DOOR.DEFAULT",
            "DOOR.SECTIONAL",
            "DOOR.ROLL_UP",
            "DOOR.SIDE_TO_SIDE",
            "DOOR.SIDE_HINGED",
            "DOOR.TILT_UP",
            "SECTIONAL",
            "UP_AND_OVER",
            "UP AND OVER",
            "GARAGE",
        ],
        DOOR,
    ),
    **dict.fromkeys(["DIMMER.LIGHTS", "RELAY.LIGHTS", "LIGHT", "DIMMER"], LIGHT),
    **dict.fromkeys(["RELAY.DEFAULT", "RELAY.POWERLOAD", "RELAY"], RELAY),
    **dict.fromkeys(["RELAY.ELECTRICAL_LOCK", "ELECTRICAL_LOCK", "LOCK"], LOCK),
    **dict.fromkeys(
        [
            "SCREEN.DEFAULT",
            "SCREEN.MONO",
            "SCREEN.BLIND",
            "SCREEN.VENETIAN",
            "SCREEN.SHUTTER",
            "SCREEN.PATIO",
            "SCREEN",
            "BLIND",
        ],
        SCREEN,
    ),
    **dict.fromkeys(
        [
            "DOOR.SECTIONAL",
            "DOOR.TILT_UP",
            "SECTIONAL",
            "UP_AND_OVER",
            "UP AND OVER",
            "GARAGE",
        ],
        GARAGE,
    ),
    **dict.fromkeys(["SCREEN.BLIND", "SCREEN.VENETIAN", "BLIND"], BLIND),
    "SCREEN.SHUTTER": SHUTTER,
}


def category_for(value):
    return (
        CATEGORY_TYPES.get(value.strip().upper(), UNKNOWN)
        if isinstance(value, str)
        else UNKNOWN
    )


def blocks_gate_adapter(info):
    category = category_for(getattr(info, "device_type", None))
    return category is not UNKNOWN and not category.gate_adapter


def require_gate_adapter(info):
    if blocks_gate_adapter(info):
        from .errors import NiceParameterError

        raise NiceParameterError(
            "The reported device category needs its own verified control/state adapter; gate commands are unavailable"
        )


def category_report(info):
    category = category_for(getattr(info, "device_type", None))
    return {
        "category": category.key,
        "ha_domain": category.ha_domain,
        "device_class": category.device_class,
        "adapter": "existing_cover" if category.gate_adapter else "unverified",
        "gap": category.gap,
        "unknown_keeps_existing_compatibility": category is UNKNOWN,
    }


class CoreAutomationInspectionAdapter:
    """Read-only table adapter. Full actuator/session integration stays separate."""

    gap = "ecosystem.core_execution"

    def __init__(self, reader=None):
        self.reader = reader

    async def read(self, target):
        if self.reader is None:
            from .errors import NiceUnsupportedError

            raise NiceUnsupportedError(
                "A scoped CORE reader is required; use the explicit standalone inspection tool"
            )
        from .models.discovery import normalize_device_id
        from .errors import NiceParameterError

        expected = self.reader.client.credentials.target_mac
        if not isinstance(target, str) or normalize_device_id(
            target
        ) != normalize_device_id(expected):
            raise NiceParameterError(
                "The CORE reader does not match the requested target scope"
            )
        import asyncio

        return await asyncio.to_thread(self.reader.read)

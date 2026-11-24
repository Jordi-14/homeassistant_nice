"""Reviewed writable DMP setting catalog."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class DmpWriteSafety(StrEnum):
    """Safety class for a reviewed DMP setting."""

    CONFIGURATION = "configuration"
    MODEL_RESTRICTED = "model_restricted"


@dataclass(frozen=True, slots=True)
class DmpSetting:
    """One named writable DMP register."""

    key: str
    group: int
    parameter: int
    size: int = 1
    safety: DmpWriteSafety = DmpWriteSafety.CONFIGURATION
    minimum: int = 0
    maximum: int = 255
    unit: str | None = None
    dynamic_max_field: str | None = None
    kind: str = "integer"
    requires_menu_permission: bool = False
    legacy_status: bool = True


AUTO_CLOSE = DmpSetting("auto_close", 0x04, 0x80, maximum=1, kind="boolean")
PHOTO_CLOSE = DmpSetting("photo_close", 0x04, 0x84, maximum=1, kind="boolean")
ALWAYS_CLOSE = DmpSetting("always_close", 0x04, 0x88, maximum=1, kind="boolean")
STANDBY = DmpSetting("standby", 0x04, 0x8C, maximum=1, kind="boolean")
PRE_FLASH = DmpSetting("pre_flash", 0x04, 0x94, maximum=1, kind="boolean")
KEY_LOCK = DmpSetting("key_lock", 0x04, 0x9C, maximum=1, kind="boolean")

PAUSE_TIME = DmpSetting("pause_time", 0x04, 0x81, maximum=250, unit="s")
OPENING_FORCE = DmpSetting(
    "opening_force", 0x04, 0x4A, minimum=1, maximum=100, unit="%"
)
CLOSING_FORCE = DmpSetting(
    "closing_force", 0x04, 0x4B, minimum=1, maximum=100, unit="%"
)
OPENING_SPEED = DmpSetting(
    "opening_speed",
    0x04,
    0x42,
    safety=DmpWriteSafety.MODEL_RESTRICTED,
    minimum=1,
    maximum=100,
    unit="%",
)
CLOSING_SPEED = DmpSetting(
    "closing_speed",
    0x04,
    0x43,
    safety=DmpWriteSafety.MODEL_RESTRICTED,
    minimum=1,
    maximum=100,
    unit="%",
)
PHOTO_CLOSE_TIME = DmpSetting("photo_close_time", 0x04, 0x85, maximum=250, unit="s")
PHOTO_CLOSE_MODE = DmpSetting("photo_close_mode", 0x04, 0x86)
ALWAYS_CLOSE_TIME = DmpSetting("always_close_time", 0x04, 0x89, maximum=250, unit="s")
ALWAYS_CLOSE_MODE = DmpSetting("always_close_mode", 0x04, 0x8A)
PARTIAL_OPEN_1_POSITION = DmpSetting(
    "partial_open_1_position",
    0x04,
    0x21,
    size=2,
    maximum=65535,
    unit="pulses",
    dynamic_max_field="max_open_position",
)
PARTIAL_OPEN_2_POSITION = DmpSetting(
    "partial_open_2_position",
    0x04,
    0x22,
    size=2,
    maximum=65535,
    unit="pulses",
    dynamic_max_field="max_open_position",
)
PARTIAL_OPEN_3_POSITION = DmpSetting(
    "partial_open_3_position",
    0x04,
    0x23,
    size=2,
    maximum=65535,
    unit="pulses",
    dynamic_max_field="max_open_position",
)
# 04/B1 is a 32-bit register: GET returns 4 bytes and INFO reports size 4.
MAINTENANCE_THRESHOLD = DmpSetting(
    "maintenance_threshold",
    0x04,
    0xB1,
    size=4,
    maximum=0xFFFFFFFE,
    unit="count",
)

SETTINGS = {
    setting.key: setting
    for setting in (
        AUTO_CLOSE,
        PHOTO_CLOSE,
        ALWAYS_CLOSE,
        STANDBY,
        PRE_FLASH,
        KEY_LOCK,
        PAUSE_TIME,
        OPENING_FORCE,
        CLOSING_FORCE,
        OPENING_SPEED,
        CLOSING_SPEED,
        PHOTO_CLOSE_TIME,
        PHOTO_CLOSE_MODE,
        ALWAYS_CLOSE_TIME,
        ALWAYS_CLOSE_MODE,
        PARTIAL_OPEN_1_POSITION,
        PARTIAL_OPEN_2_POSITION,
        PARTIAL_OPEN_3_POSITION,
        MAINTENANCE_THRESHOLD,
    )
}

SETTINGS_BY_REGISTER = {
    (setting.group, setting.parameter): setting for setting in SETTINGS.values()
}

# These scalar SET codecs use controller INFO and require a verified menu/role
# observation. The native menu adapter is unresolved, so they remain read-only
# in shipped installations. No name or range is treated as permission to write.
ADDITIONAL_SETTINGS = tuple(
    DmpSetting(
        key,
        4,
        parameter,
        size=size,
        maximum=maximum,
        unit=unit,
        requires_menu_permission=True,
        legacy_status=False,
        kind=kind,
    )
    for key, parameter, size, maximum, unit, kind in (
        ("config_opening_slowdown_speed", 0x45, 1, 100, "%", "integer"),
        ("config_closing_slowdown_speed", 0x46, 1, 100, "%", "integer"),
        ("config_electric_lock_time", 0x5A, 1, 255, "s", "integer"),
        ("config_courtesy_light_time", 0x5B, 1, 255, "s", "integer"),
        ("config_suction_cup_time", 0x5C, 1, 255, "s", "integer"),
        ("config_standby_time", 0x8D, 1, 255, "s", "integer"),
        ("config_starting_torque_time", 0x91, 1, 255, "s", "integer"),
        ("config_pre_flash_open_time", 0x95, 1, 255, "s", "integer"),
        ("config_pre_flash_close_time", 0x99, 1, 255, "s", "integer"),
        ("config_output_1_function", 0x51, 1, 255, None, "enum"),
        ("config_output_2_function", 0x52, 1, 255, None, "enum"),
        ("config_step_step_mode", 0x61, 1, 255, None, "enum"),
        ("config_partial_open_mode", 0x62, 1, 255, None, "enum"),
        ("config_open_mode", 0x63, 1, 255, None, "enum"),
        ("config_close_mode", 0x64, 1, 255, None, "enum"),
        ("config_stop_mode", 0x65, 1, 255, None, "enum"),
        ("config_input_1_function", 0x71, 1, 255, None, "enum"),
        ("config_input_2_function", 0x72, 1, 255, None, "enum"),
        ("config_input_3_function", 0x73, 1, 255, None, "enum"),
        ("config_input_4_function", 0x74, 1, 255, None, "enum"),
        ("config_standby_mode", 0x8E, 1, 255, None, "enum"),
        ("config_maintenance_mode", 0xB0, 1, 255, None, "enum"),
    )
)
SETTINGS.update((setting.key, setting) for setting in ADDITIONAL_SETTINGS)
SETTINGS_BY_REGISTER.update(
    ((setting.group, setting.parameter), setting) for setting in ADDITIONAL_SETTINGS
)

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


AUTO_CLOSE = DmpSetting("auto_close", 0x04, 0x80)
PHOTO_CLOSE = DmpSetting("photo_close", 0x04, 0x84)
ALWAYS_CLOSE = DmpSetting("always_close", 0x04, 0x88)
STANDBY = DmpSetting("standby", 0x04, 0x8C)
PRE_FLASH = DmpSetting("pre_flash", 0x04, 0x94)
KEY_LOCK = DmpSetting("key_lock", 0x04, 0x9C)

PAUSE_TIME = DmpSetting("pause_time", 0x04, 0x81)
OPENING_FORCE = DmpSetting("opening_force", 0x04, 0x4A)
CLOSING_FORCE = DmpSetting("closing_force", 0x04, 0x4B)
OPENING_SPEED = DmpSetting(
    "opening_speed",
    0x04,
    0x42,
    safety=DmpWriteSafety.MODEL_RESTRICTED,
)
CLOSING_SPEED = DmpSetting(
    "closing_speed",
    0x04,
    0x43,
    safety=DmpWriteSafety.MODEL_RESTRICTED,
)
PHOTO_CLOSE_TIME = DmpSetting("photo_close_time", 0x04, 0x85)
PHOTO_CLOSE_MODE = DmpSetting("photo_close_mode", 0x04, 0x86)
ALWAYS_CLOSE_TIME = DmpSetting("always_close_time", 0x04, 0x89)
ALWAYS_CLOSE_MODE = DmpSetting("always_close_mode", 0x04, 0x8A)
PARTIAL_OPEN_1_POSITION = DmpSetting(
    "partial_open_1_position",
    0x04,
    0x21,
    size=2,
)
PARTIAL_OPEN_2_POSITION = DmpSetting(
    "partial_open_2_position",
    0x04,
    0x22,
    size=2,
)
PARTIAL_OPEN_3_POSITION = DmpSetting(
    "partial_open_3_position",
    0x04,
    0x23,
    size=2,
)
MAINTENANCE_THRESHOLD = DmpSetting(
    "maintenance_threshold",
    0x04,
    0xB1,
    size=2,
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

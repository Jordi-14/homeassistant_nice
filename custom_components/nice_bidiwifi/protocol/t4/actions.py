"""Reviewed T4 action catalog and DEP frame encoding."""

from __future__ import annotations

from dataclasses import dataclass

from .codec import dmp_checksum

DEP_ACTION_PARTIAL_OPEN_1 = "partial_open_1"
DEP_ACTION_PARTIAL_OPEN_2 = "partial_open_2"
DEP_ACTION_PARTIAL_OPEN_3 = "partial_open_3"
DEP_ACTION_STEP_STEP = "step_step"
DEP_ACTION_COURTESY_LIGHT = "courtesy_light"
DEP_ACTION_COURTESY_LIGHT_TIMER = "courtesy_light_timer"
DEP_ACTION_LOCK = "lock"
DEP_ACTION_UNLOCK = "unlock"


@dataclass(frozen=True, slots=True)
class T4Action:
    """One reviewed T4 command."""

    key: str
    code: int
    name: str
    dangerous: bool = False
    redundant: bool = False
    compatibility_entity: bool = False

    @property
    def enabled_by_default(self) -> bool:
        """Return the safe entity-registry default."""
        return not self.dangerous and not self.redundant


T4_ACTIONS = (
    T4Action(
        DEP_ACTION_STEP_STEP,
        0x01,
        "Step-step",
        compatibility_entity=True,
    ),
    T4Action("stop_remote", 0x02, "Stop as remote", redundant=True),
    T4Action("open_remote", 0x03, "Open as remote", redundant=True),
    T4Action("close_remote", 0x04, "Close as remote", redundant=True),
    T4Action(
        DEP_ACTION_PARTIAL_OPEN_1,
        0x05,
        "Partial open 1",
        compatibility_entity=True,
    ),
    T4Action(
        DEP_ACTION_PARTIAL_OPEN_2,
        0x06,
        "Partial open 2",
        compatibility_entity=True,
    ),
    T4Action(
        DEP_ACTION_PARTIAL_OPEN_3,
        0x07,
        "Partial open 3",
        compatibility_entity=True,
    ),
    T4Action("apartment_step_step", 0x0B, "Apartment step-step"),
    T4Action("step_step_hp", 0x0C, "Step-step high priority"),
    T4Action(
        "open_and_block",
        0x0D,
        "Open and block",
        dangerous=True,
    ),
    T4Action(
        "close_and_block",
        0x0E,
        "Close and block",
        dangerous=True,
    ),
    T4Action(
        DEP_ACTION_LOCK,
        0x0F,
        "Lock",
        dangerous=True,
        compatibility_entity=True,
    ),
    T4Action(
        DEP_ACTION_UNLOCK,
        0x10,
        "Unlock",
        dangerous=True,
        compatibility_entity=True,
    ),
    T4Action(
        DEP_ACTION_COURTESY_LIGHT_TIMER,
        0x11,
        "Courtesy light timer",
        compatibility_entity=True,
    ),
    T4Action(
        DEP_ACTION_COURTESY_LIGHT,
        0x12,
        "Courtesy light",
        compatibility_entity=True,
    ),
    T4Action("master_step_step", 0x13, "Master door step-step"),
    T4Action("master_open", 0x14, "Open master door"),
    T4Action("master_close", 0x15, "Close master door"),
    T4Action("slave_step_step", 0x16, "Slave door step-step"),
    T4Action("slave_open", 0x17, "Open slave door"),
    T4Action("slave_close", 0x18, "Close slave door"),
    T4Action(
        "release_and_open",
        0x19,
        "Release and open",
        dangerous=True,
    ),
    T4Action(
        "release_and_close",
        0x1A,
        "Release and close",
        dangerous=True,
    ),
    T4Action(
        "enable_bluebus_inputs",
        0x1B,
        "Enable BlueBUS inputs",
        dangerous=True,
    ),
    T4Action(
        "disable_bluebus_inputs",
        0x1C,
        "Disable BlueBUS inputs",
        dangerous=True,
    ),
)

T4_ACTION_BY_KEY = {action.key: action for action in T4_ACTIONS}
T4_ACTION_BY_CODE = {action.code: action for action in T4_ACTIONS}
DEP_ACTION_COMMANDS = {
    action.key: action.code
    for action in T4_ACTIONS
}


def build_dep_action_frame(
    command: int,
    *,
    daddr: int = 0x00,
    dendpoint: int = 0x03,
) -> bytes:
    """Build a DEP action frame."""
    if not 0 <= command <= 0xFF:
        raise ValueError("command must be a byte")
    args = [0x01, 0x82, command, 0x64]
    checksum = dmp_checksum(*args)
    body = [
        daddr,
        dendpoint,
        0x50,
        0x91,
        0x01,
        0x05,
        0xC6,
        *args,
        checksum,
    ]
    length = len(body)
    return bytes([0x55, length, *body, length])

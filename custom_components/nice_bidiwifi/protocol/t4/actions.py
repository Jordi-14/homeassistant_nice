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
    dangerous: bool = False
    redundant: bool = False


T4_ACTIONS = (
    T4Action(DEP_ACTION_STEP_STEP, 0x01),
    T4Action("stop_remote", 0x02, redundant=True),
    T4Action("open_remote", 0x03, redundant=True),
    T4Action("close_remote", 0x04, redundant=True),
    T4Action(DEP_ACTION_PARTIAL_OPEN_1, 0x05),
    T4Action(DEP_ACTION_PARTIAL_OPEN_2, 0x06),
    T4Action(DEP_ACTION_PARTIAL_OPEN_3, 0x07),
    T4Action("apartment_step_step", 0x0B),
    T4Action("step_step_hp", 0x0C),
    T4Action("open_and_block", 0x0D, dangerous=True),
    T4Action("close_and_block", 0x0E, dangerous=True),
    T4Action(DEP_ACTION_LOCK, 0x0F),
    T4Action(DEP_ACTION_UNLOCK, 0x10),
    T4Action(DEP_ACTION_COURTESY_LIGHT_TIMER, 0x11),
    T4Action(DEP_ACTION_COURTESY_LIGHT, 0x12),
    T4Action("master_step_step", 0x13),
    T4Action("master_open", 0x14),
    T4Action("master_close", 0x15),
    T4Action("slave_step_step", 0x16),
    T4Action("slave_open", 0x17),
    T4Action("slave_close", 0x18),
    T4Action("release_and_open", 0x19, dangerous=True),
    T4Action("release_and_close", 0x1A, dangerous=True),
    T4Action("enable_bluebus_inputs", 0x1B, dangerous=True),
    T4Action("disable_bluebus_inputs", 0x1C, dangerous=True),
)

DEP_ACTION_COMMANDS = {
    action.key: action.code
    for action in T4_ACTIONS
    if action.key
    in {
        DEP_ACTION_STEP_STEP,
        DEP_ACTION_PARTIAL_OPEN_1,
        DEP_ACTION_PARTIAL_OPEN_2,
        DEP_ACTION_PARTIAL_OPEN_3,
        DEP_ACTION_LOCK,
        DEP_ACTION_UNLOCK,
        DEP_ACTION_COURTESY_LIGHT_TIMER,
        DEP_ACTION_COURTESY_LIGHT,
    }
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

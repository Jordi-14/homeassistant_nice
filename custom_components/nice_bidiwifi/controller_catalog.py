"""Controller configuration definitions with explicit read and write evidence."""

from __future__ import annotations

from dataclasses import dataclass
from functools import cache

from .controller_catalog_data import CONTROLLER_SOURCE_ROWS
from .protocol.t4.options import (
    COMMAND_MODES,
    INPUT_FUNCTIONS,
    OUTPUT_FUNCTIONS,
    STANDBY_MODES,
)
from .protocol.t4.settings import SETTINGS_BY_REGISTER, DmpSetting

# Reviewed configuration GET candidates. No menu, credential, radio memory,
# reset, relearning, execute, installer or unidentified register is probed.
READ_PARAMETERS = frozenset(
    {
        0x00,
        0x02,
        0x06,
        0x0D,
        0x10,
        0x12,
        0x13,
        0x16,
        0x17,
        *range(0x1B, 0x1F),
        *range(0x21, 0x27),
        *range(0x28, 0x2F),
        *range(0x30, 0x38),
        *range(0x39, 0x3F),
        *range(0x41, 0x47),
        *range(0x48, 0x50),
        *range(0x51, 0x57),
        *range(0x58, 0x60),
        *range(0x61, 0x70),
        *range(0x71, 0x7E),
        0x7F,
        0x80,
        0x81,
        0x82,
        0x84,
        0x85,
        0x86,
        0x88,
        0x89,
        0x8A,
        0x8C,
        0x8D,
        0x8E,
        0x90,
        0x91,
        0x92,
        *range(0x94, 0xAA),
        *range(0xAC, 0xB4),
        0xB5,
        0xB6,
        0xB9,
        *range(0xE0, 0xEC),
    }
)

FUNCTION_MODES = {
    0: "off",
    1: "on",
    4: "manual",
    5: "automatic",
    6: "semi_automatic_1",
    7: "semi_automatic_2",
    8: "maneuvers",
    16: "open_fully",
    17: "open_until_disengagement",
    18: "stop",
    19: "open_fully_2",
    32: "always_close",
    33: "save_closing",
    **STANDBY_MODES,
    51: "automatic_standby",
    52: "automatic_standby_2",
    53: "photo_test",
    54: "light",
    55: "heavy",
    56: "standby_internal_wifi_on",
    64: "all",
    65: "loop",
    66: "photo",
    67: "command",
    68: "standby_encoder_and_13v_on",
}
# Functions are wire IDs established by captured INFO + app enum translation.
ENUM_FUNCTIONS = {
    0xF2: INPUT_FUNCTIONS,
    0xF3: COMMAND_MODES,
    0xF4: OUTPUT_FUNCTIONS,
    0xF5: FUNCTION_MODES,
}
# Unit assignments are parameter-specific; native enum ordinals are not wire IDs.
UNITS = {
    **{p: "%" for p in (0x42, 0x43, 0x45, 0x46, 0x4A, 0x4B)},
    **{p: "s" for p in (0x5A, 0x5B, 0x5C, 0x81, 0x85, 0x89, 0x8D, 0x91, 0x95, 0x99)},
    **{p: "pulses" for p in (0x10, 0x12, 0x13, 0x21, 0x22, 0x23, 0x24, 0x25)},
}


ACTION_BINDINGS = {
    "CTRL_STEP_STEP": "step_step",
    "CTRL_STOP": "stop_remote",
    "CTRL_OPEN": "open_remote",
    "CTRL_CLOSE": "close_remote",
    "CTRL_OPEN_PARTIAL_1": "partial_open_1",
    "CTRL_OPEN_PARTIAL_2": "partial_open_2",
    "CTRL_OPEN_PARTIAL_3": "partial_open_3",
    "CTRL_STEP_STEP_CONDOMINIUM": "apartment_step_step",
    "CTRL_STEP_STEP_HIGH_PRIORITY": "step_step_hp",
    "CTRL_OPEN_BLOCK": "open_and_block",
    "CTRL_CLOSE_BLOCK": "close_and_block",
    "CTRL_BLOCK": "lock",
    "CTRL_UNBLOCK": "unlock",
    "CTRL_TIMER_COURTESY_LIGHT": "courtesy_light_timer",
    "CTRL_ON_OFF_COURTESY_LIGHT": "courtesy_light",
    "CTRL_STEP_STEP_MASTER_DOOR": "master_step_step",
    "CTRL_OPEN_MASTER_DOOR": "master_open",
    "CTRL_CLOSE_MASTER_DOOR": "master_close",
    "CTRL_STEP_STEP_SLAVE_DOOR": "slave_step_step",
    "CTRL_OPEN_SLAVE_DOOR": "slave_open",
    "CTRL_CLOSE_SLAVE_DOOR": "slave_close",
    "CTRL_UNBLOCK_AND_OPEN": "release_and_open",
    "CTRL_UNBLOCK_AND_CLOSE": "release_and_close",
    "CTRL_OPNC_ON": "enable_bluebus_inputs",
    "CTRL_OPNC_OFF": "disable_bluebus_inputs",
}


@dataclass(frozen=True, slots=True)
class ControllerParameter:
    key: str
    symbol: str
    title: str
    app_id: int | None
    binding: tuple[int, int] | None
    protocol: str
    evidence: str

    @property
    def existing_read(self) -> str | None:
        return {
            (4, 1): "automation.status",
            (4, 0x11): "automation.position",
            (4, 0x18): "automation.position",
            (4, 0x19): "automation.position",
            (4, 3): "controller_configuration",
        }.get(self.binding)

    @property
    def reviewed_action(self) -> str | None:
        return ACTION_BINDINGS.get(self.symbol)

    @property
    def safe_read(self) -> bool:
        return (
            self.protocol == "DMP"
            and self.binding is not None
            and self.binding[0] == 4
            and self.binding[1] in READ_PARAMETERS
            and not any(
                token in self.symbol
                for token in (
                    "SEARCH",
                    "DELETE",
                    "RESET",
                    "CALIBRATION",
                    "TEST",
                    "CMD_",
                    "ACTIVATE",
                    "RESERVED",
                    "PASSWORD",
                )
            )
        )

    @property
    def setting(self) -> DmpSetting | None:
        return SETTINGS_BY_REGISTER.get(self.binding) if self.binding else None

    @property
    def unit(self) -> str | None:
        return (
            UNITS.get(self.binding[1])
            if self.binding and self.binding[0] == 4
            else None
        )

    @property
    def gap(self) -> str | None:
        if self.binding is None:
            return (
                "controller.action_binding"
                if self.protocol == "DEP"
                else "controller.binding_map"
            )
        if self.protocol == "DEP":
            return "controller.action_binding"
        if not self.safe_read:
            return (
                "controller.menu_wire"
                if self.symbol == "STD_MENU"
                or (self.binding[0] == 4 and self.binding[1] >= 0xF0)
                else "controller.parameter_format"
            )
        return None


PARAMETERS = tuple(
    ControllerParameter(
        symbol.lower(), symbol, title, app_id, binding, protocol, evidence
    )
    for symbol, title, app_id, binding, protocol, evidence in CONTROLLER_SOURCE_ROWS
)
PARAMETERS_BY_KEY = {row.key: row for row in PARAMETERS}
PARAMETERS_BY_REGISTER = {row.binding: row for row in PARAMETERS if row.safe_read}


@cache
def parameter_for_setting(key: str) -> ControllerParameter:
    return next(
        row for row in PARAMETERS if row.setting is not None and row.setting.key == key
    )

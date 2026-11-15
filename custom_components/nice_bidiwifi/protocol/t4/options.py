"""Names of enum setting values, from the MyNice Pro enums ListOut, ListCommand and ListFunctionsMode."""

from __future__ import annotations

# 04/51, 04/52 (ListOut wire values).
OUTPUT_FUNCTIONS = {
    0: "none",
    1: "gate_open_indicator",
    2: "gate_open",
    3: "gate_closed",
    4: "maintenance_light",
    5: "flashing_light",
    6: "courtesy_light",
    7: "electric_lock_1",
    8: "electric_lock_2",
    9: "electric_block_1",
    10: "electric_block_2",
    11: "suction_cup_1",
    12: "suction_cup_2",
    13: "traffic_light_red",
    14: "traffic_light_green",
    15: "radio_channel_1",
    16: "radio_channel_2",
    17: "radio_channel_3",
    18: "radio_channel_4",
    19: "flashing_light_1",
    20: "gate_open_indicator_1",
    21: "gate_open_indicator_2",
    22: "always_on",
    23: "flashing_light_24v",
}

# 04/61 step-by-step mode (ListCommand wire values 1-7).
STEP_STEP_MODES = {
    1: "open_stop_close_stop",
    2: "open_stop_close_open",
    3: "open_close_open_close",
    4: "condominium_1",
    5: "condominium_2",
    6: "step_step_2",
    7: "hold_to_run",
}

# 04/8E standby mode (ListFunctionsMode wire values).
STANDBY_MODES = {48: "bluebus", 49: "safety_devices", 50: "all"}

# 04/B0 maintenance management (ListFunctionsMode wire values).
MAINTENANCE_MODES = {4: "manual", 5: "automatic"}


def option_name(names: dict[int, str], value: int | None) -> str | None:
    """Return the option name, or code_<n> for a value the app enum does not list."""
    if value is None:
        return None
    return names.get(value, f"code_{value}")

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

# 04/71-04/74 input functions: INFO function byte 0xF2 = ListDeviceCommand; wire code -> command through
# the table in T4Value_init (libnative-lib.so @0xe678, table @0x7e0c, default device profile).
# This is the input command list, not the EvtMovCommand event list (that one alternates
# open/close partial 1-3); both orders are from the app.
INPUT_FUNCTIONS = {
    0x00: "none",  # RST: no command
    0x01: "step_by_step",
    0x02: "stop",
    0x03: "open",
    0x04: "close",
    0x05: "partial_open_1",
    0x06: "partial_open_2",
    0x07: "partial_open_3",
    0x08: "partial_close_1",
    0x09: "partial_close_2",
    0x0A: "partial_close_3",
    0x0B: "step_by_step_condominium",
    0x0C: "step_by_step_high_priority",
    0x0D: "open_and_block",
    0x0E: "close_and_block",
    0x0F: "block",
    0x10: "unblock",
    0x11: "courtesy_light_timer",
    0x12: "courtesy_light_on_off",
    0x13: "master_step_by_step",
    0x14: "master_open",
    0x15: "master_close",
    0x16: "slave_step_by_step",
    0x17: "slave_open",
    0x18: "slave_close",
    0x19: "unblock_and_open",
    0x1A: "unblock_and_close",
    # ACND_ON/OFF: enable/disable BlueBus photo commands and inputs set as "condominium open"
    0x1B: "condominium_open_on",
    0x1C: "condominium_open_off",
    0x1D: "loop_on",
    0x1E: "loop_off",
    0x21: "alt",
    0x22: "photo_open",
    0x23: "photo",
    0x24: "photo_1",
    0x25: "photo_2",
    0x26: "photo_3",
    0x27: "emergency_stop",
    0x28: "emergency_command",
    0x29: "interlocking_stop",
    0x2A: "sba_sensor",
    0x2B: "emergency_open",
    0x2C: "emergency_close",
    0x30: "testing_step_by_step",
}

# 04/61-04/6C, 04/78, 04/79 command modes (ListCommand wire values; T4Value_init table @0x7ee4).
COMMAND_MODES = {
    0: "none",
    1: "open_stop_close_stop",
    2: "open_stop_close_open",
    3: "open_close_open_close",
    4: "condominium_1",
    5: "condominium_2",
    6: "step_step_2",
    7: "hold_to_run",
    8: "open_semi_automatic_close_hold_to_run",
    9: "open_stop_open",
    10: "open_condominium_1",
    11: "open_condominium_2",
    12: "open_2",
    13: "open_hold_to_run",
    14: "close_stop_close",
    15: "close_condominium_1",
    16: "close_condominium_2",
    17: "close_2",
    18: "close_hold_to_run",
    19: "reverse",
    20: "stop_movement",
    21: "stop",
    22: "stop_and_short_reverse",
    23: "alt",
    24: "alt_and_short_reverse",
    25: "alt_and_reverse",
    26: "intervene_closing_and_opening",
    27: "intervene_closing",
    28: "stop_and_reverse_when_closing",
    29: "condominium_block",
}
STEP_STEP_MODES = COMMAND_MODES

# 04/8E standby mode (ListFunctionsMode wire values).
STANDBY_MODES = {48: "bluebus", 49: "safety_devices", 50: "all"}

# 04/B0 maintenance management (ListFunctionsMode wire values).
MAINTENANCE_MODES = {4: "manual", 5: "automatic"}


def option_name(names: dict[int, str], value: int | None) -> str | None:
    """Return the option name, or code_<n> for a value the app enum does not list."""
    if value is None:
        return None
    return names.get(value, f"code_{value}")

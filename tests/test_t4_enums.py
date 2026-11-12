"""Tests for T4 event enum tables.

Values checked against EvtChangeState.java / EvtStateCause.java (myNICE Pro
v2.18). Decompiled constants such as Opcodes.I2B are JVM opcode numbers
(I2B = 145). Hardware-verified so far: EvtChangeState 4, 5, 131, 132, 136;
EvtStateCause 0, 25.
"""

from __future__ import annotations

from custom_components.nice_bidiwifi.protocol.t4.enums import (
    EVT_CHANGE_STATE,
    EVT_STATE_CAUSE,
)

CHANGE_STATE_EXPECTED = {
    0: "unknown",
    1: "stopped",
    2: "opening_in_progress",
    3: "closing_in_progress",
    4: "stopped_in_opened",
    5: "stopped_in_closed",
    6: "active_preflashing",
    7: "stopped_in_pause_time",
    8: "searching_devices",
    9: "searching_positions",
    10: "research_devices_finished",
    11: "research_positions_finished",
    12: "research_devices_error",
    13: "research_positions_error",
    16: "stopped_in_partial_1",
    17: "stopped_in_partial_2",
    18: "stopped_in_partial_3",
    112: "slave_not_present",
    113: "slave_stopped",
    114: "slave_opening",
    115: "slave_closing",
    116: "slave_stopped_in_opened",
    117: "slave_stopped_in_closed",
    118: "slave_preflashing_active",
    119: "ack_from_slave",
    120: "answer_from_slave",
    129: "opening_for_photo_intervent",
    130: "closing_for_photo_intervent",
    131: "opening_from_command",
    132: "closing_from_command",
    133: "closing_from_automatic_closing",
    134: "blocked_for_command_stop",
    135: "blocked_for_input_alt",
    136: "stopped_for_command_arrival",
    145: "force_limiter_intervent",
    146: "fototest_failed",
    147: "alt_intervention",
    148: "memory_err",
    149: "hw_err",
    150: "double_cmd_err",
    151: "lockedmotor_err",
    152: "low_battery",
    153: "maneuver_nr_exceeded_threshold",
    154: "wear_automation",
    155: "battery_supply_mode",
    156: "line_supply_mode",
    157: "realignment_next_manoeuvre",
    158: "burglary_in_progress",
    159: "reset_board",
    160: "class_b_reset",
    161: "sliding_manually_moved",
    162: "testing",
    163: "board_replacement",
    164: "motor_extra_run",
    165: "cu_entering_in_standby",
}

STATE_CAUSE_EXPECTED = {
    0: "none",
    1: "radio",
    2: "key_1",
    3: "key_2",
    4: "key_3",
    5: "key_4",
    6: "sensitive_edge",
    7: "input_1",
    8: "input_2",
    9: "input_3",
    10: "photo",
    17: "photo_open",
    25: "oview",
    101: "radio_command_1",
    102: "radio_command_2",
    103: "radio_command_3",
    104: "radio_command_4",
    105: "radio_command_5",
}


def test_evt_change_state_values() -> None:
    for code, name in CHANGE_STATE_EXPECTED.items():
        assert EVT_CHANGE_STATE[code] == name


def test_evt_state_cause_values() -> None:
    for code, name in STATE_CAUSE_EXPECTED.items():
        assert EVT_STATE_CAUSE[code] == name

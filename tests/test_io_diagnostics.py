"""04/D1 and 04/D2 decoding with replies read from a ROBUSR10 (FG01h) on 2026-11-14/15."""

from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.io import (
    decode_hardware_diagnostic,
    decode_io_diagnostic,
)

D1_INFO = decode_dmp_info(bytes.fromhex("0181e1410e0f0743000dffffffff3c1fff0100"))
D2_INFO = decode_dmp_info(
    bytes.fromhex("0281e2410e80008000800080648000806400008000000000008064000080000000")
)


def test_info_lists_keep_their_values() -> None:
    assert D1_INFO.values[:5] == (0x0F, 0x07, 0x43, 0x00, 0x0D)
    assert D2_INFO.values[3] == 0x8064 and D2_INFO.values[6] == 0


def test_io_at_rest_open_shows_only_installed_items() -> None:
    io = decode_io_diagnostic(bytes.fromhex("0100400008040000000000020100"), D1_INFO)
    assert io["alt_ok"] is True
    assert io["output_2"] is True  # gate-open light while open
    assert io["motor_m1"] is False and io["output_1"] is False
    assert io["direction"] == "left"
    assert io["force_limiter_state"] == "threshold_2"
    # not installed on this controller (INFO): loops, second motor, encoder items, fan
    for name in ("loop_1", "motor_m2", "encoder_m1_fault", "fan", "selected_motor"):
        assert name not in io


def test_io_while_closing() -> None:
    io = decode_io_diagnostic(bytes.fromhex("0100400005040000000000020100"), D1_INFO)
    assert io["motor_m1"] is True and io["output_1"] is True and io["output_2"] is False


def test_hardware_fields_while_closing() -> None:
    hw = decode_hardware_diagnostic(
        bytes.fromhex(
            "00 ee 00 00 00 00 00 0b 00 20 00 1e 00 00 00 1c 00 00 00 00 00 4c 00 00 12 d4 00 00"
        ), D2_INFO
    )
    assert hw == {
        "work_time_left": 238,
        "pause_time_left": 0,
        "courtesy_light_time_left": 0,
        "bus_current": 11,
        "service_voltage": 32,
        "torque_m1": 30,
        "temperature_raw": 28,
        "speed_m1": 76,
    }


def test_hardware_photocell_arms_the_pause_timer() -> None:
    hw = decode_hardware_diagnostic(
        bytes.fromhex(
            "00 e1 00 1e 00 3c 00 0b 00 22 00 06 00 00 00 1c 00 00 00 00 00 00 00 00 07 a7 00 00"
        ), D2_INFO
    )
    assert hw["pause_time_left"] == 30 and hw["courtesy_light_time_left"] == 60


def test_without_info_everything_is_shown_but_no_percent() -> None:
    io = decode_io_diagnostic(bytes.fromhex("0100400008040000000000020100"), None)
    assert "loop_1" in io
    hw = decode_hardware_diagnostic(bytes(24), None)
    assert "torque_m1" not in hw and hw["work_time_left"] == 0


def test_short_or_empty_replies() -> None:
    assert decode_io_diagnostic(bytes(5), D1_INFO) is None
    assert decode_io_diagnostic(b"\xff" * 14, D1_INFO) is None
    assert decode_hardware_diagnostic(bytes(10), D2_INFO) is None

"""Tests for the public entity reference table."""

from __future__ import annotations

from pathlib import Path

from custom_components.nice_bidiwifi.binary_sensor import (
    BINARY_SENSORS,
    EVENT_BINARY_SENSORS,
)
from custom_components.nice_bidiwifi.button import BUTTONS
from custom_components.nice_bidiwifi.number import NUMBERS
from custom_components.nice_bidiwifi.sensor import EVENT_SENSORS, SENSORS
from custom_components.nice_bidiwifi.switch import CONFIG_SWITCHES

REFERENCE_PATH = Path(__file__).parents[1] / "entity_reference.md"
TABLE_HEADER = (
    "| Platform | Entity | Key | Purpose | Visibility default | "
    "Enabled default | Notes |"
)


def _reference_defaults() -> dict[str, tuple[bool, bool]]:
    rows: dict[str, tuple[bool, bool]] = {}
    in_table = False
    for line in REFERENCE_PATH.read_text(encoding="utf-8").splitlines():
        if line == TABLE_HEADER:
            in_table = True
            continue
        if not in_table:
            continue
        if not line.startswith("| "):
            break
        if line.startswith("| ---"):
            continue

        columns = [column.strip() for column in line.strip().strip("|").split("|")]
        key = columns[2].strip("`")
        rows[key] = (columns[4] == "Visible", columns[5] == "Enabled")

    return rows


def _code_defaults() -> dict[str, tuple[bool, bool]]:
    defaults = {
        "cover": (True, True),
        "cover_switch": (True, True),
        "protocol_event": (True, True),
    }
    for description in (
        *CONFIG_SWITCHES,
        *BUTTONS,
        *BINARY_SENSORS,
        *EVENT_BINARY_SENSORS,
        *NUMBERS,
        *SENSORS,
        *EVENT_SENSORS,
    ):
        defaults[description.key] = (
            description.entity_registry_visible_default,
            description.entity_registry_enabled_default,
        )
    return defaults


def test_entity_reference_defaults_match_code() -> None:
    """Test that documented entity registry defaults match entity descriptions."""
    assert _reference_defaults() == _code_defaults()

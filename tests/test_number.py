"""Tests for Nice number entities."""

from __future__ import annotations

import pytest
from homeassistant.const import PERCENTAGE, UnitOfTime
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.number import (
    NUMBERS,
    NiceBidiNumber,
    async_setup_entry,
)
from custom_components.nice_bidiwifi.protocol.t4.info import decode_dmp_info
from tests.conftest import FakeCoordinator, config_entry, make_device_info, make_status


def _description(key: str):
    return next(description for description in NUMBERS if description.key == key)


class TestNiceBidiNumberProperties:
    """Test BusT4 configuration number properties."""

    def test_number_descriptions_have_unique_keys(self) -> None:
        keys = [description.key for description in NUMBERS]
        assert len(keys) == len(set(keys))
        assert "bus_t4_opening_speed" in keys
        assert "bus_t4_partial_open_1_position" in keys
        assert "bus_t4_maintenance_threshold" in keys

    def test_number_reads_status(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.unique_id == "aabbccddeeff_1_bus_t4_opening_speed"
        assert entity.entity_description.entity_registry_enabled_default is True
        assert entity.entity_description.entity_registry_visible_default is True
        assert entity.native_value == 60
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.native_min_value == 1
        assert entity.native_max_value == 100
        assert entity.available is True

    def test_pause_time_uses_seconds(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_pause_time"))

        assert entity.native_value == 30
        assert entity.native_unit_of_measurement == UnitOfTime.SECONDS

    def test_partial_open_position_uses_dynamic_max(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", max_open_position=4043, open_position=4000)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_partial_open_1_position"),
        )

        assert entity.native_value == 250
        assert entity.native_max_value == 4043

    def test_mode_registers_allow_full_raw_byte_range(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", always_close_mode=32)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_always_close_mode"),
        )

        assert entity.native_value == 32
        assert entity.native_max_value == 255

    def test_unavailable_when_value_unknown(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="open", opening_speed=None)
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.native_value is None
        assert entity.available is False

    def test_unavailable_while_gate_is_moving(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(state="opening")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.native_value == 60
        assert entity.available is False

    def test_unavailable_while_position_calibration_is_running(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.calibration_state = "running"
        coordinator.data = make_status(state="stopped")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        assert entity.native_value == 60
        assert entity.available is False

    def test_speed_settings_unavailable_for_aria_clbox(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.device_info = make_device_info(
            device_product="ARIA200S",
            device_description="CLBOX",
        )
        coordinator.data = make_status(state="open")

        opening_speed = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))
        closing_speed = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_closing_speed"))
        opening_force = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_force"))

        assert opening_speed.native_value == 60
        assert opening_speed.available is False
        assert closing_speed.native_value == 55
        assert closing_speed.available is False
        assert opening_force.native_value == 70
        assert opening_force.available is True

    @pytest.mark.parametrize(
        ("product", "description"),
        [("ARIA200S", "Control unit"), ("Generic", "CLBOX")],
    )
    def test_speed_policy_accepts_each_confirmed_unsafe_identity(
        self,
        product: str,
        description: str,
    ) -> None:
        """Test either identity reported by real controllers disables writes."""
        coordinator = FakeCoordinator()
        coordinator.device_info = make_device_info(
            device_product=product,
            device_description=description,
            device_product_detail=None,
        )
        coordinator.data = make_status(state="open")

        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_opening_speed"),
        )

        assert entity.available is False

    def test_speed_policy_leaves_unrelated_controller_available(self) -> None:
        """Test speed writes remain available without a confirmed unsafe identity."""
        coordinator = FakeCoordinator()
        coordinator.device_info = make_device_info(
            device_product="ROBUS",
            device_description="RBA3",
            device_product_detail=None,
        )
        coordinator.data = make_status(state="open")

        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_opening_speed"),
        )

        assert entity.available is True

    async def test_number_writes_single_byte_register(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        await entity.async_set_native_value(75)

        assert coordinator.calls == [("dmp_write", (0x04, 0x42, 75, 1))]

    async def test_number_writes_two_byte_register(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(max_open_position=4043)
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_partial_open_1_position"),
        )

        await entity.async_set_native_value(1500)

        assert coordinator.calls == [("dmp_write", (0x04, 0x21, 1500, 2))]

    async def test_number_uses_controller_option_list(self) -> None:
        """Test INFO options (04/86 on a ROBUSR10: 16, 17) narrow a 0-255 mode number."""
        coordinator = FakeCoordinator()
        coordinator.setting_info = {
            "photo_close_mode": decode_dmp_info(bytes.fromhex("0100f561021011"))
        }
        entity = NiceBidiNumber(
            coordinator, config_entry(), _description("bus_t4_photo_close_mode")
        )

        assert (entity.native_min_value, entity.native_max_value) == (16, 17)
        assert entity.extra_state_attributes == {"allowed_values": [16, 17]}
        with pytest.raises(ValueError, match="between 16 and 17"):
            await entity.async_set_native_value(0)
        await entity.async_set_native_value(17)
        assert coordinator.calls == [("dmp_write", (0x04, 0x86, 17, 1))]

    async def test_number_rejects_code_missing_from_option_list(self) -> None:
        """Test a value inside the range but not in the controller's list."""
        coordinator = FakeCoordinator()
        coordinator.setting_info["always_close_mode"] = decode_dmp_info(
            bytes.fromhex("0100f56103202224")
        )
        entity = NiceBidiNumber(
            coordinator, config_entry(), _description("bus_t4_always_close_mode")
        )

        with pytest.raises(ValueError, match="one of"):
            await entity.async_set_native_value(0x23)

    async def test_number_never_widens_static_limits(self) -> None:
        """Test INFO 0-100 keeps the static minimum 1 of a force setting."""
        coordinator = FakeCoordinator()
        coordinator.setting_info = {
            "opening_force": decode_dmp_info(bytes.fromhex("01000a01006401")),
            "pause_time": decode_dmp_info(bytes.fromhex("01001101006401")),
        }
        force = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_force"))
        pause = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_pause_time"))

        assert (force.native_min_value, force.native_max_value) == (1, 100)
        assert (pause.native_min_value, pause.native_max_value) == (0, 100)

    async def test_number_ignores_info_that_empties_the_range(self) -> None:
        """Test INFO 112-128 against a static 1-100 force keeps 1-100, not min > max."""
        coordinator = FakeCoordinator()
        coordinator.setting_info = {
            "opening_force": decode_dmp_info(bytes.fromhex("01000a01708001")),
        }
        force = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_force"))

        assert (force.native_min_value, force.native_max_value) == (1, 100)

    async def test_number_refuses_a_per_motor_array_setting(self) -> None:
        """MC824H (#38): force is per motor. With INFO marking an array, no one-value write is sent.

        The INFO bytes are constructed (array bit, array size 2, range 1-8 from the MC824H manual);
        no MC824H INFO capture exists yet.
        """
        coordinator = FakeCoordinator()
        coordinator.setting_info = {
            "opening_force": decode_dmp_info(bytes.fromhex("01000a8102010801"))
        }
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_force"))

        assert entity.available is False
        with pytest.raises(HomeAssistantError, match="per-motor array"):
            await entity.async_set_native_value(4)
        assert coordinator.calls == []

    async def test_number_keeps_plain_settings_writable(self) -> None:
        """ROBUSR10 04/4A INFO (plain, 0-100) keeps the force number writable."""
        coordinator = FakeCoordinator()
        coordinator.setting_info = {"opening_force": decode_dmp_info(bytes.fromhex("01000a01006401"))}
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_force"))

        await entity.async_set_native_value(50)
        assert coordinator.calls == [("dmp_write", (0x04, 0x4A, 50, 1))]

    async def test_number_rejects_out_of_range_value(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        with pytest.raises(ValueError, match="between 1 and 100"):
            await entity.async_set_native_value(101)

    async def test_number_writes_maintenance_threshold_as_four_bytes(self) -> None:
        """Test 04/B1 is written with its real 32-bit size."""
        coordinator = FakeCoordinator()
        coordinator.data = make_status()
        entity = NiceBidiNumber(
            coordinator,
            config_entry(),
            _description("bus_t4_maintenance_threshold"),
        )
        await entity.async_set_native_value(70000)

        assert coordinator.calls == [("dmp_write", (0x04, 0xB1, 70000, 4))]

    async def test_speed_settings_reject_writes_for_aria_clbox(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.device_info = make_device_info(device_product_detail="ARIA 200S CLBOX")
        entity = NiceBidiNumber(coordinator, config_entry(), _description("bus_t4_opening_speed"))

        with pytest.raises(HomeAssistantError, match="disabled for this controller"):
            await entity.async_set_native_value(75)

        assert coordinator.calls == []


async def test_async_setup_entry_adds_all_numbers() -> None:
    """Test number platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == len(NUMBERS)
    assert all(isinstance(entity, NiceBidiNumber) for entity in created)

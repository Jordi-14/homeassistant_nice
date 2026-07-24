"""Tests for Nice sensor entities."""

from __future__ import annotations

from homeassistant.components.sensor import SensorDeviceClass, SensorStateClass
from homeassistant.const import PERCENTAGE, UnitOfElectricPotential, UnitOfTemperature

from custom_components.nice_bidiwifi.sensor import (
    EVENT_SENSORS,
    SENSORS,
    NiceBidiSensor,
    async_setup_entry,
)
from tests.conftest import FakeCoordinator, config_entry, make_status


def _description(key: str):
    return next(description for description in SENSORS if description.key == key)


def _event_description(key: str):
    return next(description for description in EVENT_SENSORS if description.key == key)


class TestNiceBidiSensorProperties:
    """Test sensor entity properties."""

    def test_sensor_descriptions_have_unique_keys(self) -> None:
        keys = [description.key for description in SENSORS]
        assert len(keys) == len(set(keys))
        assert "connection_state" in keys
        assert "gate_position" in keys
        assert "position_calibration_report" in keys
        assert "control_unit_serial" in keys
        assert "opening_speed" in keys
        assert "maintenance_count" in keys
        assert "last_stop_reason" in keys
        assert "motor_temperature" in keys
        assert "service_voltage" in keys
        assert "oxi_product" in keys
        assert not any(key.startswith("beta_") for key in keys)
        assert not any(description.name.startswith("BETA") for description in SENSORS)

    def test_event_sensor_descriptions_are_additive_and_unprotected(self) -> None:
        keys = [description.key for description in EVENT_SENSORS]
        assert len(keys) == len(set(keys))
        assert set(keys).isdisjoint(description.key for description in SENSORS)
        assert all(not description.protected for description in EVENT_SENSORS)

        coordinator = FakeCoordinator()
        coordinator.last_event_cause = "C02"
        entity = NiceBidiSensor(
            coordinator,
            config_entry(),
            _event_description("last_event_cause"),
        )
        assert entity.unique_id == "aabbccddeeff_1_last_event_cause"
        assert entity.native_value == "C02"

    def test_connection_state_sensor(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("connection_state")
        )
        assert entity.unique_id == "aabbccddeeff_1_connection_state"
        assert entity.native_value == "connected"
        assert entity.available is True
        assert entity.extra_state_attributes is None

    def test_last_error_sensor_has_none_fallback(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.last_error = None
        entity = NiceBidiSensor(coordinator, config_entry(), _description("last_error"))
        assert entity.native_value == "none"

    def test_reconnect_count_sensor_reads_client(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("reconnect_count")
        )
        assert entity.native_value == 3

    def test_encoder_position_sensor_reads_status(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("current_encoder_position")
        )
        assert entity.native_value == 424

    def test_extended_bus_t4_sensors_read_status(self) -> None:
        coordinator = FakeCoordinator()

        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("opening_speed")
            ).native_value
            == 60
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("opening_force")
            ).native_value
            == 70
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("maintenance_count")
            ).native_value
            == 12
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("total_maneuver_count")
            ).native_value
            == 345
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("last_stop_reason")
            ).native_value
            == "obstacle_by_encoder"
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("oxi_product")
            ).native_value
            == "OXI"
        )

    def test_issue_15_final_diagnostics_read_d2_payload(self) -> None:
        coordinator = FakeCoordinator()

        motor_temperature = NiceBidiSensor(
            coordinator, config_entry(), _description("motor_temperature")
        )
        assert motor_temperature.native_value == 42
        assert (
            motor_temperature.entity_description.device_class
            == SensorDeviceClass.TEMPERATURE
        )
        assert (
            motor_temperature.entity_description.native_unit_of_measurement
            == UnitOfTemperature.CELSIUS
        )
        assert (
            motor_temperature.entity_description.state_class
            == SensorStateClass.MEASUREMENT
        )
        assert (
            motor_temperature.entity_description.entity_registry_enabled_default is True
        )
        assert (
            motor_temperature.entity_description.entity_registry_visible_default
            is False
        )

        service_voltage = NiceBidiSensor(
            coordinator, config_entry(), _description("service_voltage")
        )
        assert service_voltage.native_value == 32
        assert (
            service_voltage.entity_description.device_class == SensorDeviceClass.VOLTAGE
        )
        assert (
            service_voltage.entity_description.native_unit_of_measurement
            == UnitOfElectricPotential.VOLT
        )
        assert (
            service_voltage.entity_description.state_class
            == SensorStateClass.MEASUREMENT
        )
        assert (
            service_voltage.entity_description.entity_registry_enabled_default is False
        )
        assert (
            service_voltage.entity_description.entity_registry_visible_default is False
        )

    def test_issue_15_diagnostics_are_unavailable_without_d2_payload(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(diagnostics_parameters="00 00 00 00")

        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("motor_temperature")
            ).native_value
            is None
        )
        assert (
            NiceBidiSensor(
                coordinator, config_entry(), _description("service_voltage")
            ).native_value
            is None
        )

    def test_gate_position_sensor_reads_display_position(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.data = make_status(position=42.4)

        class SimulatedCoordinator(FakeCoordinator):
            @property
            def display_position(self) -> float | None:
                return 75.9

            @property
            def display_position_estimated(self) -> bool:
                return True

        simulated_coordinator = SimulatedCoordinator()
        simulated_coordinator.data = coordinator.data
        entity = NiceBidiSensor(
            simulated_coordinator, config_entry(), _description("gate_position")
        )

        assert entity.native_value == 75.9
        assert entity.native_unit_of_measurement == PERCENTAGE
        assert entity.entity_description.entity_registry_enabled_default is True
        assert entity.entity_description.entity_registry_visible_default is True

    def test_device_info_sensor_reads_info_metadata(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("control_unit_serial")
        )
        assert entity.native_value == "0E6809FF"
        assert entity.device_info["serial_number"] == "0E6809FF"

    def test_report_sensor_exposes_extra_attributes(self) -> None:
        coordinator = FakeCoordinator()
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("position_calibration_report")
        )
        assert entity.native_value == "good: 8/8 repeatable targets"
        assert entity.extra_state_attributes == {"quality": "good", "point_count": 8}

    def test_unavailable_when_value_is_none(self) -> None:
        coordinator = FakeCoordinator()
        coordinator.device_info = None
        entity = NiceBidiSensor(
            coordinator, config_entry(), _description("interface_serial")
        )
        assert entity.native_value is None
        assert entity.available is False


async def test_async_setup_entry_adds_all_sensors() -> None:
    """Test platform setup."""
    coordinator = FakeCoordinator()
    entry = config_entry()
    entry.runtime_data = coordinator
    created = []

    def add_entities(entities):
        created.extend(list(entities))

    await async_setup_entry(None, entry, add_entities)

    assert len(created) == len(SENSORS) + len(EVENT_SENSORS)
    assert all(isinstance(entity, NiceBidiSensor) for entity in created)

"""Device registry metadata from the BiDi-WiFi INFO."""

from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.nice_bidiwifi.const import DOMAIN
from custom_components.nice_bidiwifi.entity import bidi_device_info
from tests.conftest import config_entry_data, make_device_info


def test_device_info_shows_description_as_model_and_product_as_model_id() -> None:
    """ROBUSR10 INFO: description NewRobus, product ROBUSR10."""
    entry = MockConfigEntry(domain=DOMAIN, data=config_entry_data())
    info = bidi_device_info(
        entry, make_device_info(device_product="ROBUSR10", device_description="NewRobus")
    )

    assert info["model"] == "NewRobus"
    assert info["model_id"] == "ROBUSR10"
    assert bidi_device_info(entry)["model_id"] is None

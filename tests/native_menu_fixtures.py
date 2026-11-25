"""Synthetic menu and authenticated module facts for Pro-control tests."""

from custom_components.nice_bidiwifi.controller_menu import CONF_PRO_CONTROLS
from custom_components.nice_bidiwifi.protocol.t4.menu import (
    ControllerMenuSnapshot,
    decode_menu_records,
)
from tests.conftest import make_device_info


def set_pro_controls(owner, enabled):
    owner.hass.config_entries.async_update_entry(
        owner.config_entry,
        options=dict(owner.config_entry.options) | {CONF_PRO_CONTROLS: enabled},
    )


def install_menu_fixture(owner, payload=b"\x70\x5a"):
    owner.device_info = owner.device_info or make_device_info()
    set_pro_controls(owner, True)

    def read_menu(**kwargs):
        return ControllerMenuSnapshot(
            decode_menu_records(payload, group=4),
            owner.client.access_session_scope,
            1,
            owner.parameters.device_id,
        )

    owner.client.read_controller_menu = read_menu

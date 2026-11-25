"""Synthetic role adapter for exercising guarded writes; no hardware claim."""

import time

from custom_components.nice_bidiwifi.controller_menu import ControllerRoleProof
from custom_components.nice_bidiwifi.protocol.t4.menu import (
    ControllerMenuSnapshot,
    MenuRole,
    decode_menu_records,
)
from tests.conftest import make_device_info


def install_menu_fixture(owner, payload=b"\x70\x5a", *, role=MenuRole.INSTALLER):
    owner.device_info = owner.device_info or make_device_info()

    def read_menu(**kwargs):
        return ControllerMenuSnapshot(
            decode_menu_records(payload, group=4),
            owner.client.access_session_scope,
            1,
            owner.parameters.device_id,
        )

    owner.client.read_controller_menu = read_menu

    class RoleAdapter:
        def read_verified_role(self, scope, identity):
            return ControllerRoleProof(
                role,
                scope,
                identity,
                time.monotonic(),
                "verified_controller_role_adapter",
            )

    owner.controller_menu.role_adapter = RoleAdapter()

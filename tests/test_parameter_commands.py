"""Addressed reply validation, exactly-once commands and targeted verification."""

import threading
import time
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.command_executor import CommandExecutor
from custom_components.nice_bidiwifi.errors import (
    NiceBidiConnectionError,
    NiceProtocolError,
    NiceReadPreemptedError,
    NiceResponseError,
    ProtocolLayer,
)
from custom_components.nice_bidiwifi.models.commands import (
    CommandAcknowledgement,
    CommandKind,
    NiceCommand,
)
from custom_components.nice_bidiwifi.parameters import SupportState
from custom_components.nice_bidiwifi.protocol.t4.codec import dmp_checksum
from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo, decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.replies import checked_dmp_reply
from custom_components.nice_bidiwifi.protocol.t4.settings import PAUSE_TIME
from tests.conftest import FakeClient, make_device_info, make_status
from tests.test_client import _client
from tests.test_connection_router import _router
from tests.test_coordinator import _coordinator


def reply(
    group=4, parameter=0x81, value=b"\x1e", *, operation=0x19, status=0, target=(0, 3)
):
    header = [0x50, 0x91, *target, 8, len(value) + 6]
    payload = [group, parameter, operation, len(value), status, *value]
    body = [*header, dmp_checksum(*header), *payload, dmp_checksum(*payload)]
    return bytes([0x55, len(body), *body, len(body)])


@pytest.mark.parametrize(
    "change",
    [{"parameter": 0x85}, {"group": 10}, {"target": (0, 10)}, {"operation": 0x09}],
)
def test_wrong_parameter_target_or_operation_does_not_match(change) -> None:
    assert (
        checked_dmp_reply(
            reply(**change), target=(0, 3), group=4, parameter=0x81, operation=0x19
        )
        is None
    )


@pytest.mark.parametrize("offset", [1, 7, 8, 12, -2, -1])
def test_matching_but_corrupt_reply_is_not_accepted(offset) -> None:
    plain = bytearray(reply())
    plain[offset] ^= 1
    with pytest.raises(NiceProtocolError):
        checked_dmp_reply(
            bytes(plain), target=(0, 3), group=4, parameter=0x81, operation=0x19
        )


@pytest.mark.parametrize("status", [0xF0, 0xF1, 0xF2, 0xF9, 0xFD, 0xFF])
def test_controller_errors_are_structured(status) -> None:
    with pytest.raises(NiceResponseError) as caught:
        checked_dmp_reply(
            reply(status=status), target=(0, 3), group=4, parameter=0x81, operation=0x19
        )
    error = caught.value
    assert (
        error.layer == "dmp"
        and error.code == f"{status:02X}"
        and error.register == (4, 0x81)
    )


def test_set_rejection_is_not_mistaken_for_success(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "_t4_request_locked",
        lambda *a: (b"<Response/>", [reply(operation=0x29, status=0xF2)]),
    )
    with pytest.raises(NiceResponseError, match="SET not implemented"):
        client._write_dmp_register_locked(4, 0x81, b"\x1e")
    assert (
        client.parameters.support_state(client.parameters.address(4, 0x81), "set")
        is SupportState.UNSUPPORTED
    )
    assert (
        client.parameters.support_state(client.parameters.address(4, 0x81), "get")
        is SupportState.UNKNOWN
    )


def test_wrong_set_acknowledgement_does_not_succeed(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(
        client,
        "_t4_request_locked",
        lambda *a: (b"<Response/>", [reply(parameter=0x85, operation=0x29)]),
    )
    with pytest.raises(NiceProtocolError, match="acknowledgement"):
        client._write_dmp_register_locked(4, 0x81, b"\x1e")


def test_empty_controller_acknowledgement_is_unknown(monkeypatch) -> None:
    client = _client()
    monkeypatch.setattr(client, "_t4_request_locked", lambda *a: (b"<Response/>", []))
    assert (
        client._write_dmp_register_locked(4, 0x81, b"\x1e").acknowledgement == "unknown"
    )


def test_info_not_implemented_does_not_disable_get(monkeypatch) -> None:
    client = _client()
    responses = iter([reply(operation=0x09, status=0xF0), reply()])
    monkeypatch.setattr(
        client, "_t4_request_locked", lambda *a: (b"<Response/>", [next(responses)])
    )
    assert client._read_dmp_info_locked(4, 0x81) is None
    assert client._read_dmp_value_locked(4, 0x81, operation=0x19) == b"\x1e"
    address = client.parameters.address(4, 0x81)
    assert client.parameters.support_state(address, "info") is SupportState.UNSUPPORTED
    assert client.parameters.support_state(address, "get") is SupportState.SUPPORTED


async def test_transport_timeout_is_unknown_and_not_replayed(hass) -> None:
    calls = []

    def send():
        calls.append(1)
        raise NiceBidiConnectionError("device did not respond")

    executor = CommandExecutor(hass.async_add_executor_job)
    with pytest.raises(NiceBidiConnectionError):
        await executor.execute(NiceCommand("open", CommandKind.DOOR_ACTION), send)
    assert calls == [1] and executor.last_result.acknowledgement == "unknown"


async def test_explicit_refusal_is_rejected_without_breaking_the_route(hass) -> None:
    coordinator = _coordinator(hass)
    client = coordinator.client = FakeClient()
    client.send_dep_action_error = NiceResponseError(
        "refused", layer=ProtocolLayer.NHK, code="5", request_type="T4_REQUEST"
    )
    with pytest.raises(HomeAssistantError):
        await coordinator._async_send_dep_action("partial_open_1", refresh=False)
    assert coordinator.last_command_result.acknowledgement == "rejected"
    assert coordinator.last_command_result.error_layer == "nhk"
    assert not client.closed and coordinator.connection_state == "connected"


@pytest.mark.parametrize(
    ("readback", "verification"),
    [(b"\x1e", "verified"), (None, "unavailable"), (b"\x1f", "mismatch")],
)
async def test_setting_write_reads_only_the_changed_parameter(
    hass, readback, verification
) -> None:
    coordinator = _coordinator(hass)
    client = coordinator.client = FakeClient()
    client.dmp_values[(4, 0x81)] = readback
    coordinator.data = make_status(state="closed")
    coordinator._extended_status_next_refresh_monotonic = time.monotonic() + 1000
    deadline = coordinator._extended_status_next_refresh_monotonic
    if verification == "mismatch":
        with pytest.raises(HomeAssistantError, match="verification"):
            await coordinator.async_write_setting(PAUSE_TIME, 30)
    else:
        await coordinator._async_write_dmp_register(4, 0x81, 30)
    assert client.dmp_writes == [(4, 0x81, 30, 1)]
    assert client.value_reads == [(4, 0x81)]
    assert not client.read_status_include_extended
    assert coordinator._extended_status_next_refresh_monotonic == deadline
    assert coordinator.last_command_result.verification == verification
    assert coordinator.data.pause_time == (
        int.from_bytes(readback, "big") if readback else None
    )


async def test_readback_failure_never_resends_the_write(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()
    client.read_dmp_value_error = NiceReadPreemptedError("stop is waiting")
    await coordinator._async_write_dmp_register(4, 0x81, 30)
    assert len(client.dmp_writes) == 1
    assert coordinator.last_command_result.acknowledgement == "accepted"
    assert coordinator.last_command_result.verification == "unavailable"


@pytest.mark.parametrize("product", ["MC824H", "HYKE"])
async def test_missing_or_indexed_force_info_is_blocked_in_backend(
    hass, product
) -> None:
    coordinator = _coordinator(hass)
    client = coordinator.client = FakeClient()
    coordinator.device_info = make_device_info(device_product=product)
    for info in [None, DmpInfo(1, 0, 10, 0x81, array_size=2)]:
        coordinator.setting_info = {"opening_force": info} if info else {}
        with pytest.raises(HomeAssistantError):
            await coordinator.async_write_dmp_register(4, 0x4A, 50)
    assert not client.dmp_writes


async def test_backend_uses_info_width_byte_order_and_constraints(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()
    coordinator.setting_info["opening_force"] = decode_dmp_info(
        bytes.fromhex("82000a01000064000200")
    )
    with pytest.raises(HomeAssistantError, match="step"):
        await coordinator._async_write_dmp_register(4, 0x4A, 41)
    await coordinator._async_write_dmp_register(4, 0x4A, 42)
    assert client.dmp_writes == [(4, 0x4A, 42, 2)]
    assert coordinator.last_command_result.verification == "verified"


async def test_fresh_decoded_diagnostics_match_the_new_raw_registers(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator._cache_extended_status(
        replace(
            make_status(),
            motor_temperature=10,
            service_voltage=34,
            controller_errors={"positions": False},
            diagnostics_parameters="old",
            registers={"04/D2": "old"},
        )
    )
    fresh = replace(
        make_status(),
        motor_temperature=21,
        service_voltage=31,
        controller_errors={"positions": True},
        diagnostics_parameters="fresh",
        registers={"04/D2": "fresh", "04/D1": "fresh"},
    )
    merged = coordinator._merge_cached_extended_status(fresh)
    assert merged.motor_temperature == 21 and merged.service_voltage == 31
    assert (
        merged.controller_errors == {"positions": True}
        and merged.diagnostics_parameters == "fresh"
    )
    assert merged.registers["04/D2"] == "fresh"


async def test_gate_starting_during_metadata_read_prevents_write(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()

    def metadata(*args, **kwargs):
        coordinator.data = make_status(state="opening")

    client.read_dmp_info = metadata
    with pytest.raises(HomeAssistantError, match="started moving"):
        await coordinator.async_write_setting(PAUSE_TIME, 30)
    assert client.dmp_writes == []


async def test_metadata_is_rechecked_after_acquiring_the_session(
    hass, monkeypatch
) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = _client()
    client.parameters = coordinator.parameters
    from custom_components.nice_bidiwifi.protocol.nhk.access import (
        PairedUser,
        PairingsSnapshot,
        UserRole,
    )
    client.access_session_scope = ("test", 0)
    credentials = coordinator.entry_config.credentials
    coordinator.access.snapshot = PairingsSnapshot((PairedUser("1", credentials.username, credentials.source, UserRole.ADMIN),))
    coordinator.access.scope = client.access_session_scope
    coordinator.access._verified_at = time.monotonic()
    coordinator.setting_info["opening_force"] = DmpInfo(1, 0, 10, 1, 0, 100, 1)
    sent = []

    def connect():
        coordinator.setting_info["opening_force"] = DmpInfo(2, 0, 10, 1, 0, 100, 1)

    monkeypatch.setattr(client, "_ensure_connected_locked", connect)
    monkeypatch.setattr(
        client, "_write_dmp_register_locked", lambda *args: sent.append(args)
    )
    with pytest.raises(HomeAssistantError, match="metadata changed"):
        await coordinator._async_write_dmp_register(4, 0x4A, 50)
    assert not sent
    assert dict(coordinator.last_command_result.details)["delivery"] == "not_sent"


async def test_late_readback_does_not_overwrite_a_newer_command(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()
    first_read, release = threading.Event(), threading.Event()
    reads = []

    def read_value(*args, **kwargs):
        reads.append(1)
        if len(reads) == 1:
            first_read.set()
            assert release.wait(3)
            return b"\x1e"
        return b"\x28"

    client.read_dmp_value = read_value
    first = hass.async_create_task(coordinator._async_write_dmp_register(4, 0x81, 30))
    assert await hass.async_add_executor_job(first_read.wait, 3)
    try:
        await coordinator._async_write_dmp_register(4, 0x81, 40)
    finally:
        release.set()
    await first
    assert coordinator.data.pause_time == 40
    assert dict(coordinator.last_command_result.command.arguments)["value"] == 40
    assert coordinator.last_command_result.verification == "verified"


def test_unrecognized_status_and_wrong_receiver_are_not_successful() -> None:
    with pytest.raises(NiceProtocolError, match="status"):
        checked_dmp_reply(
            reply(status=1), target=(0, 3), group=4, parameter=0x81, operation=0x19
        )
    wrong_receiver = bytearray(reply())
    wrong_receiver[2] = 0x51
    with pytest.raises(NiceProtocolError):
        checked_dmp_reply(
            bytes(wrong_receiver),
            target=(0, 3),
            group=4,
            parameter=0x81,
            operation=0x19,
        )


def test_real_info_capture_passes_checked_reply_validation() -> None:
    plain = bytes.fromhex("551450910003080dc704860907000100f5610210111a14")
    result = checked_dmp_reply(
        plain, target=(0, 3), group=4, parameter=0x86, operation=0x09
    )
    assert decode_dmp_info(result.value).options == (16, 17)


async def test_bus_timeout_during_metadata_discovery_does_not_send_a_setting(
    hass,
) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()

    def metadata(*args, **kwargs):
        raise NiceResponseError(
            "bus timeout", layer=ProtocolLayer.NHK, code="14", request_type="T4_REQUEST"
        )

    client.read_dmp_info = metadata
    with pytest.raises(HomeAssistantError, match="metadata"):
        await coordinator.async_write_setting(PAUSE_TIME, 30)
    assert not client.dmp_writes


async def test_verification_connection_loss_keeps_write_outcome_but_marks_connection_failure(
    hass,
) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    client = coordinator.client = FakeClient()
    client.read_dmp_value_error = NiceBidiConnectionError("offline during readback")
    await coordinator._async_write_dmp_register(4, 0x81, 30)
    assert coordinator.last_command_result.acknowledgement == "accepted"
    assert coordinator.last_command_result.verification == "unavailable"
    assert coordinator.connection_state == "failed"
    assert len(client.dmp_writes) == 1


def test_indexed_get_uses_the_info_index_width(monkeypatch) -> None:
    client = _client()
    client.parameters.record_info(
        client.parameters.address(4, 0x4A), DmpInfo(1, 0, 10, 0x82, array_size=300)
    )
    requests = []

    def exchange(protocol, payload, *args):
        requests.append(payload)
        return b"<Response/>", [reply(parameter=0x4A, value=b"\x01\x00\x07")]

    monkeypatch.setattr(client, "_t4_request_locked", exchange)
    assert client._read_dmp_indexed_locked(4, 0x4A, 256) == b"\x01\x00\x07"
    assert requests[0][13:16] == b"\x02\x01\x00"


def test_timeouts_have_unknown_delivery_even_with_an_explicit_nhk_timeout(hass) -> None:
    executor = CommandExecutor(hass.async_add_executor_job)
    error = NiceResponseError(
        "bus timeout", layer=ProtocolLayer.NHK, code="14", request_type="T4_REQUEST"
    )
    result = executor.failed_result(
        NiceCommand("open", CommandKind.DOOR_ACTION), time.monotonic(), error
    )
    assert result.acknowledgement == "unknown" and result.error_layer == "nhk"


@pytest.mark.parametrize(
    "mode", ["local_only", "cloud_only", "local_with_cloud_fallback"]
)
def test_prepared_write_receipt_survives_routing_without_replay(mode) -> None:
    from custom_components.nice_bidiwifi.models.commands import CommandReceipt

    router, _, clients = _router(mode)
    receipt = CommandReceipt(CommandAcknowledgement.UNKNOWN)
    calls = []

    def write(prepared, *, before_dispatch=None):
        calls.append(prepared)
        return receipt

    clients[0].write_parameter = write
    prepared = router.parameters.prepare_write(PAUSE_TIME, 30)
    assert router.write_parameter(prepared) is receipt
    assert calls == [prepared]


def test_prepared_write_is_bound_to_its_device_before_network_access(
    monkeypatch,
) -> None:
    from custom_components.nice_bidiwifi.errors import NiceParameterError
    from custom_components.nice_bidiwifi.parameters import ParameterRepository

    client = _client()
    connected = []
    monkeypatch.setattr(client, "_ensure_connected_locked", lambda: connected.append(1))
    prepared = ParameterRepository(2).prepare_write(PAUSE_TIME, 30)
    with pytest.raises(NiceParameterError, match="target"):
        client.write_parameter(prepared)
    assert not connected


async def test_invalid_setting_does_not_cancel_existing_control_tasks(hass) -> None:
    coordinator = _coordinator(hass)
    coordinator.data = make_status(state="closed")
    coordinator.client = FakeClient()
    coordinator._async_cancel_position_target = AsyncMock()
    coordinator._async_cancel_calibration = AsyncMock()
    with pytest.raises(HomeAssistantError, match="between"):
        await coordinator.async_write_setting(PAUSE_TIME, 1001)
    coordinator._async_cancel_position_target.assert_not_awaited()
    coordinator._async_cancel_calibration.assert_not_awaited()
    assert not coordinator.client.dmp_writes

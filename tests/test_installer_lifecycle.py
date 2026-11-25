"""Source-backed lifecycle reads, secret suppression and HA runtime integration."""

import hashlib
from dataclasses import replace
from unittest.mock import AsyncMock

import pytest
from homeassistant.exceptions import HomeAssistantError

from custom_components.nice_bidiwifi.connection import CONNECTION_STATE_CONNECTED
from custom_components.nice_bidiwifi.errors import (
    NiceParameterError,
    NiceProtocolError,
    NiceUnsupportedError,
)
from custom_components.nice_bidiwifi.feature_catalog import FeatureBlocked
from custom_components.nice_bidiwifi.firmware import FirmwareAdapter, FirmwareArtifact
from custom_components.nice_bidiwifi.installer_catalog import PROCEDURES_BY_KEY
from custom_components.nice_bidiwifi.protocol.nhk.lifecycle import (
    boot_read_body,
    parse_boot_read,
    parse_inventory,
)
from tests.test_administration import _coordinator

INFO = b'<Response type="INFO"><Interface id="3"><Prod>BiDi-WiFi</Prod><VersionHW>A</VersionHW><VersionFW>1</VersionFW><SerialNr>private</SerialNr><key_data>secret</key_data></Interface><Devices><Device id="1"><Prod>ROBUS</Prod><Manuf>Nice</Manuf><VersionHW>1</VersionHW><VersionFW>2</VersionFW><SerialNr>private</SerialNr></Device><Device id="2"><Prod>OXI</Prod><VersionFW>3</VersionFW><Name>person</Name></Device></Devices></Response>'


def test_advertised_inventory_keeps_device_ids_without_serials_or_user_labels():
    inventory = parse_inventory(INFO)
    assert inventory.interface_id == 3 and len(inventory.devices) == 2
    assert inventory.devices[1].product == "OXI"
    assert inventory.complete
    assert not any(
        secret in str(inventory.export()) for secret in ["private", "secret", "person"]
    )
    assert not parse_inventory(
        b'<Response type="INFO"><Interface/></Response>'
    ).complete


@pytest.mark.parametrize(
    "frame",
    [
        b'<Response type="INFO"><Interface/><Devices><Device id="1"/><Device id="1"/></Devices></Response>',
        b'<Response type="INFO" protocolType="WNC"><Interface/></Response>',
        b'<Response type="INFO"><Interface/><Devices><Device id="invalid"/></Devices></Response>',
    ],
)
def test_inventory_rejects_ambiguous_or_wrong_namespace(frame):
    with pytest.raises(NiceProtocolError):
        parse_inventory(frame)


@pytest.mark.parametrize(
    "operation",
    [
        "FILE_ERASE",
        "FILE_WRITE",
        "START_BOOT",
        "START_PROGRAMMING",
        "STOP_PROGRAMMING",
        "<inject/>",
    ],
)
def test_boot_read_codec_cannot_dispatch_a_mutation(operation):
    with pytest.raises(NiceParameterError):
        boot_read_body(operation, 3)


def test_boot_reads_validate_interface_and_do_not_invent_percentages_or_authenticity():
    body = boot_read_body("FILE_INFO", 3)
    assert "<Protocol>BOOT_1</Protocol><Type>FILE_INFO</Type>" in body
    frame = b'<Response type="T4_UPDATE"><Interface id="3"><FwVersion>1.2</FwVersion><HwVerList>A,B</HwVerList><CrcChecked>true</CrcChecked><FileSignature>private</FileSignature></Interface></Response>'
    result = parse_boot_read(frame, "FILE_INFO", 3)
    assert result["crc_checked_reported"] and not result["authenticity_verified"]
    assert "private" not in str(result)
    with pytest.raises(NiceProtocolError, match="match"):
        parse_boot_read(frame, "FILE_INFO", 4)
    result = parse_boot_read(
        b'<Response type="T4_UPDATE"><Interface id="3"><Status>RUN</Status><Progress>149</Progress></Interface></Response>',
        "STATUS_PROGRAMMING",
        3,
    )
    assert (
        result["progress_reported"] == 149 and result["progress_unit"] == "unverified"
    )
    assert not result["started_by_integration"]
    result = parse_boot_read(
        b'<Response type="T4_UPDATE"><Interface id="3"><Devices><Device id="0"><HwVer>A</HwVer><DAddress>00</DAddress><DEndpoint>03</DEndpoint></Device></Devices></Interface></Response>',
        "DEV_ON_BOOT",
        3,
    )
    assert len(result["devices"]) == 1 and not result["entered_bootloader"]
    assert "DAddress" not in str(result)


def owner_for(hass):
    owner = _coordinator(hass)
    owner._set_connection_state(CONNECTION_STATE_CONNECTED)
    owner.client.read_device_inventory = lambda: parse_inventory(INFO)

    def read_boot(operation, interface_id, *, before_dispatch=None):
        if before_dispatch:
            before_dispatch()
        return {"operation": operation, "interface_id": interface_id}

    owner.client.read_boot_state = read_boot
    return owner


async def test_runtime_inventory_and_boot_inspection_are_only_reads(hass):
    owner = owner_for(hass)
    await owner.lifecycle.async_run("inspect_devices")
    assert len(owner.lifecycle.inventory.devices) == 2
    await owner.lifecycle.async_run("inspect_boot")
    assert set(owner.lifecycle.boot) == {
        "FILE_INFO",
        "DEV_ON_BOOT",
        "STATUS_PROGRAMMING",
    }
    assert owner.lifecycle.boot["FILE_INFO"]["data"]["interface_id"] == 3
    assert owner.client.interface_reboots == 0
    assert owner.lifecycle.runner.record.state == "completed"


async def test_missing_interface_identity_blocks_boot_targeting(hass):
    owner = owner_for(hass)
    owner.client.read_device_inventory = lambda: replace(
        parse_inventory(INFO), interface_id=None
    )
    owner.client.read_boot_state = lambda *args, **kwargs: pytest.fail(
        "must not probe a guessed interface"
    )
    with pytest.raises(FeatureBlocked, match="advertise"):
        await owner.lifecycle.async_run("inspect_boot")


async def test_configuration_snapshot_is_not_a_restorable_backup(hass):
    owner = owner_for(hass)
    owner.parameters.observe(owner.parameters.address(4, 0x81), b"\x1e")
    result = await owner.lifecycle.async_run("snapshot_configuration")
    assert not result["restorable"] and not result["complete"]
    assert len(result["parameters"]) >= 1
    owner.parameters.observe(owner.parameters.address(4, 0x81), b"\x1f")
    result = await owner.lifecycle.async_run("snapshot_configuration")
    assert "ctrl_pause_time" in result["comparison"]["changed_keys"]
    with pytest.raises(FeatureBlocked):
        await owner.lifecycle.async_run("restore_configuration", confirmed=True)
    assert not owner.client.dmp_writes


async def test_restart_uses_journal_and_existing_single_send_transport(hass):
    owner = owner_for(hass)
    await owner.async_reboot_interface()
    assert owner.client.interface_reboots == 1
    assert owner.lifecycle.runner.record.state == "acknowledged"
    assert owner.lifecycle.runner.result["physical_reboot_verified"] is False
    raw = await owner.lifecycle._store.async_load()
    assert raw["key"] == "restart_interface" and raw["dispatched"]


async def test_mutating_installer_procedure_blocks_new_movement_and_preserves_stop(
    hass,
):
    owner = owner_for(hass)
    owner.lifecycle.runner.busy = True
    owner.lifecycle.runner._spec = PROCEDURES_BY_KEY["restart_interface"]
    with pytest.raises(HomeAssistantError):
        owner.lifecycle.control_guard("open")
    owner.lifecycle.control_guard("stop")
    owner.lifecycle.control_guard("stop_remote")
    with pytest.raises(HomeAssistantError):
        owner.lifecycle.require_idle()


async def test_firmware_integrity_does_not_unlock_vendor_programming():
    image = b"not a vendor signed image"
    artifact = FirmwareArtifact(
        "ROBUS",
        ("A",),
        "2",
        "BOOT_1",
        hashlib.sha256(image).hexdigest(),
        len(image),
        "vendor-ref",
    )
    artifact.check_integrity(image, product="ROBUS", hardware="A", protocol="BOOT_1")
    with pytest.raises(NiceUnsupportedError, match="authentication"):
        await FirmwareAdapter().install(
            artifact, image, product="ROBUS", hardware="A", protocol="BOOT_1"
        )
    with pytest.raises(NiceParameterError):
        artifact.check_integrity(
            image, product="other", hardware="A", protocol="BOOT_1"
        )


async def test_snapshot_excludes_credentials_and_indexed_motor_values(hass):
    owner = owner_for(hass)
    owner.parameters.observe(owner.parameters.address(4, 0xFB), b"secret")
    owner.parameters.observe(
        replace(owner.parameters.address(4, 0x12), index=1), b"\x2a"
    )
    result = await owner.lifecycle.async_run("snapshot_configuration")
    assert not result["parameters"]
    assert "secret" not in str(owner.lifecycle.diagnostics())


async def test_session_change_discards_inventory_before_caching(hass):
    owner = owner_for(hass)

    def changing_session():
        owner.client.reconnect_count += 1
        return parse_inventory(INFO)

    owner.client.read_device_inventory = changing_session
    with pytest.raises(FeatureBlocked, match="session"):
        await owner.lifecycle.async_run("inspect_devices")
    assert owner.lifecycle.inventory is None


async def test_recovery_acknowledgement_requires_explicit_health_check_and_durable_save(
    hass,
):
    from custom_components.nice_bidiwifi.procedures import ProcedureRecord

    owner = owner_for(hass)
    original = owner.lifecycle.runner.record = ProcedureRecord(
        "restart_interface",
        state="needs_recovery",
        dispatched=True,
        recovery_required=True,
    )
    with pytest.raises(FeatureBlocked, match="acknowledgement"):
        await owner.lifecycle.async_acknowledge_recovery()
    owner.lifecycle._store.async_save = AsyncMock(side_effect=OSError("storage"))
    with pytest.raises(OSError):
        await owner.lifecycle.async_acknowledge_recovery(confirmed=True)
    assert owner.lifecycle.runner.record is original and original.recovery_required
    owner.lifecycle._store.async_save = AsyncMock()
    await owner.lifecycle.async_acknowledge_recovery(confirmed=True)
    assert owner.lifecycle.runner.record.state == "recovery_acknowledged"
    assert not owner.lifecycle.runner.record.recovery_required
    assert not owner.client.interface_reboots


async def test_read_client_boot_allowlist_runs_before_connection(monkeypatch):
    from tests.test_client import _client

    client = _client()
    calls = []
    monkeypatch.setattr(
        client, "_ensure_connected_locked", lambda: calls.append("connect")
    )
    with pytest.raises(NiceParameterError):
        client.read_boot_state("START_BOOT", 1)
    assert not calls


async def test_runtime_restart_rechecks_role_and_model(hass):
    from custom_components.nice_bidiwifi.protocol.nhk.access import (
        PairedUser,
        PairingsSnapshot,
        UserRole,
    )

    owner = owner_for(hass)
    credentials = owner.entry_config.credentials
    owner.client.read_pairings = lambda: PairingsSnapshot(
        (PairedUser("1", credentials.username, credentials.source, UserRole.USER),)
    )
    with pytest.raises(Exception, match="administrator"):
        await owner.lifecycle.async_run("restart_interface", confirmed=True)
    assert not owner.client.interface_reboots


async def test_unknown_or_failed_inspection_cannot_clear_observed_running_programming(
    hass,
):
    owner = owner_for(hass)
    owner.client.read_boot_state = lambda *args, **kwargs: {"status_reported": "RUN"}
    await owner.lifecycle.async_run("inspect_boot")
    assert owner.lifecycle.programming_observed
    with pytest.raises(HomeAssistantError):
        owner.lifecycle.control_guard("open")
    owner.lifecycle.control_guard("stop")
    owner.client.read_boot_state = lambda *args, **kwargs: {
        "status_reported": "UNKNOWN"
    }
    await owner.lifecycle.async_run("inspect_boot")
    assert owner.lifecycle.programming_observed
    owner.client.read_boot_state = lambda *args, **kwargs: {"status_reported": "IDLE"}
    await owner.lifecycle.async_run("inspect_boot")
    assert not owner.lifecycle.programming_observed


async def test_gate_dispatch_guard_rejects_queued_movement_when_procedure_takes_ownership(
    hass,
):
    owner = owner_for(hass)
    owner.lifecycle.runner.busy = True
    owner.lifecycle.runner._spec = PROCEDURES_BY_KEY["restart_interface"]
    with pytest.raises(NiceParameterError) as error:
        owner._control_dispatch_guard("open")
    assert error.value.delivery == "not_sent"
    owner._control_dispatch_guard("stop")


async def test_recovery_storage_write_excludes_a_new_restart(hass):
    owner = owner_for(hass)
    import asyncio

    from custom_components.nice_bidiwifi.procedures import ProcedureRecord

    owner.lifecycle.runner.record = ProcedureRecord(
        "restart_interface",
        state="needs_recovery",
        dispatched=True,
        recovery_required=True,
    )
    entered, release = asyncio.Event(), asyncio.Event()

    async def save(record):
        entered.set()
        await release.wait()

    owner.lifecycle._store.async_save = save
    task = asyncio.create_task(
        owner.lifecycle.async_acknowledge_recovery(confirmed=True)
    )
    await entered.wait()
    assert owner.lifecycle.runner.busy
    with pytest.raises(FeatureBlocked, match="active"):
        await owner.lifecycle.async_run("restart_interface", confirmed=True)
    release.set()
    await task
    assert not owner.client.interface_reboots
    assert owner.lifecycle.runner.record.state == "recovery_acknowledged"


def test_unknown_boot_status_does_not_export_unparsed_content():
    result = parse_boot_read(
        b'<Response type="T4_UPDATE"><Interface id="3"><Status>private certificate payload</Status></Interface></Response>',
        "STATUS_PROGRAMMING",
        3,
    )
    assert result["status_reported"] == "UNKNOWN"
    assert not result["status_recognized"]
    assert "private" not in str(result)


def test_installer_dispatch_rejection_is_explicitly_not_sent(hass):
    owner = owner_for(hass)
    owner.lifecycle.runner.busy = True
    with pytest.raises(NiceParameterError) as error:
        owner.lifecycle.require_idle_at_dispatch()
    assert error.value.delivery == "not_sent"

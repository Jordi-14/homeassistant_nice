"""Controller metadata, codec and backend-policy regressions."""

from dataclasses import replace

import pytest

from custom_components.nice_bidiwifi.errors import NiceParameterError
from custom_components.nice_bidiwifi.parameters import (
    ParameterAddress,
    ParameterRepository,
    SupportState,
)
from custom_components.nice_bidiwifi.protocol.t4.info import DmpInfo, decode_dmp_info
from custom_components.nice_bidiwifi.protocol.t4.settings import (
    OPENING_FORCE,
    PAUSE_TIME,
    PHOTO_CLOSE_MODE,
)
from tests.conftest import make_device_info


def test_metadata_keeps_raw_width_order_and_round_trips() -> None:
    raw = bytes.fromhex("82000a010000e8030200")
    info = decode_dmp_info(raw)
    assert info.byte_order == "little" and info.size == 2 and info.raw == raw
    repository = ParameterRepository(3)
    address = repository.address(4, 0x4A)
    repository.record_info(address, info)
    prepared = repository.prepare_write(OPENING_FORCE, 42)
    assert prepared.payload == b"\x2a\x00" and prepared.byte_order == "little"
    observed = repository.observe(address, prepared.payload)
    assert observed.raw == prepared.payload and observed.raw_value == 42
    assert repository.diagnostics()[address.key]["observed_at"]


@pytest.mark.parametrize("index_size", [1, 2, 3])
def test_indexed_observations_are_scoped_and_never_scalar_writable(index_size) -> None:
    repository = ParameterRepository(2)
    base = repository.address(4, 0x4A)
    repository.record_info(base, DmpInfo(1, 0, 0x0A, 0x80 | index_size, array_size=3))
    first = repository.address(4, 0x4A, index=1)
    second = repository.address(4, 0x4A, index=2)
    repository.observe(first, (1).to_bytes(index_size, "big") + b"\x04")
    repository.observe(second, (2).to_bytes(index_size, "big") + b"\x07")
    assert repository.diagnostics()[first.key]["raw_value"] == 4
    assert repository.diagnostics()[second.key]["raw_value"] == 7
    assert (
        repository.observe(second, (1).to_bytes(index_size, "big") + b"\x04").value
        is None
    )
    with pytest.raises(NiceParameterError, match="indexed"):
        repository.prepare_write(OPENING_FORCE, 50)


@pytest.mark.parametrize("size", [0, 1, 2])
def test_even_one_element_indexed_settings_do_not_allow_scalar_writes(size) -> None:
    repository = ParameterRepository()
    repository.record_info(
        repository.address(4, 0x4A), DmpInfo(1, 0, 10, 0x81, array_size=size)
    )
    with pytest.raises(NiceParameterError, match="indexed"):
        repository.prepare_write(OPENING_FORCE, 4)


@pytest.mark.parametrize("product", ["MC824H", "HYKE"])
def test_known_unverified_forces_fail_closed_without_info(product) -> None:
    repository = ParameterRepository()
    with pytest.raises(NiceParameterError, match="metadata"):
        repository.prepare_write(
            OPENING_FORCE, 50, device=make_device_info(device_product=product)
        )
    repository.record_info(
        repository.address(4, 0x4A), decode_dmp_info(bytes.fromhex("01000a01006401"))
    )
    assert (
        repository.prepare_write(
            OPENING_FORCE, 50, device=make_device_info(device_product=product)
        ).payload
        == b"2"
    )


@pytest.mark.parametrize("info_bytes", ["01000a8202", "01000a80", "01000a6102ff"])
def test_malformed_info_never_unlocks_a_setting(info_bytes) -> None:
    repository = ParameterRepository()
    raw = bytes.fromhex(info_bytes)
    assert decode_dmp_info(raw) is None
    repository.record_info(
        repository.address(4, 0x4A), None, raw=raw, state=SupportState.INVALID
    )
    with pytest.raises(NiceParameterError):
        repository.prepare_write(OPENING_FORCE, 50)


def test_indexed_guard_survives_later_missing_info() -> None:
    repository = ParameterRepository()
    address = repository.address(4, 0x4A)
    repository.record_info(address, DmpInfo(1, 0, 10, 0x81, array_size=2))
    repository.record_info(address, None, state=SupportState.UNAVAILABLE)
    with pytest.raises(NiceParameterError, match="indexed"):
        repository.prepare_write(OPENING_FORCE, 50)


def test_info_get_and_set_support_are_independent() -> None:
    repository = ParameterRepository()
    address = repository.address(4, 0x81)
    repository.record_info(address, None, state=SupportState.UNSUPPORTED)
    repository.observe(address, b"\x1e")
    repository.record_support(address, "set", SupportState.UNSUPPORTED, error="F2")
    repository.observe(address, None, source="readback")
    assert repository.support_state(address, "info") is SupportState.UNSUPPORTED
    assert repository.support_state(address, "get") is SupportState.SUPPORTED
    assert repository.support_state(address, "set") is SupportState.UNSUPPORTED
    with pytest.raises(NiceParameterError, match="SET"):
        repository.prepare_write(PAUSE_TIME, 30)


@pytest.mark.parametrize("value", [-1, 101, 1.5, True])
def test_backend_checks_reviewed_limits_and_raw_type(value) -> None:
    with pytest.raises(NiceParameterError):
        ParameterRepository().prepare_write(OPENING_FORCE, value)


def test_backend_checks_controller_options_step_and_contradictions() -> None:
    repository = ParameterRepository()
    repository.setting_info[PHOTO_CLOSE_MODE.key] = DmpInfo(
        1, 0, 0xF5, 0x61, options=(16, 17)
    )
    assert repository.prepare_write(PHOTO_CLOSE_MODE, 16).payload == b"\x10"
    with pytest.raises(NiceParameterError):
        repository.prepare_write(PHOTO_CLOSE_MODE, 15)
    repository.setting_info[PAUSE_TIME.key] = DmpInfo(1, 0, 0x11, 1, 5, 100, 5)
    with pytest.raises(NiceParameterError, match="step"):
        repository.prepare_write(PAUSE_TIME, 11)
    assert repository.prepare_write(PAUSE_TIME, 15).payload == b"\x0f"
    repository.setting_info[OPENING_FORCE.key] = DmpInfo(1, 0, 10, 1, 112, 128, 1)
    with pytest.raises(NiceParameterError, match="contradicts"):
        repository.prepare_write(OPENING_FORCE, 50)


@pytest.mark.parametrize(
    ("flags", "native", "expected"), [(0x11, 2.5, 25), (0x21, 250, 25)]
)
def test_engineering_units_convert_exactly(flags, native, expected) -> None:
    repository = ParameterRepository()
    repository.setting_info[PAUSE_TIME.key] = DmpInfo(1, 0, 0x11, flags, 0, 250, 1, 10)
    assert repository.prepare_native_write(PAUSE_TIME, native).value == expected
    observation = repository.observe(repository.address(4, 0x81), bytes([expected]))
    assert observation.value == native


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 2.55])
def test_nonrepresentable_engineering_values_are_rejected(value) -> None:
    repository = ParameterRepository()
    repository.setting_info[PAUSE_TIME.key] = DmpInfo(1, 0, 0x11, 0x11, 0, 250, 1, 10)
    with pytest.raises(NiceParameterError):
        repository.prepare_native_write(PAUSE_TIME, value)


def test_unknown_shapes_preserve_bytes_without_inventing_numbers() -> None:
    repository = ParameterRepository()
    address = repository.address(4, 0xD2)
    observation = repository.observe(address, b"\x01\x02\x03\x04")
    assert observation.value is None and observation.raw == b"\x01\x02\x03\x04"
    repository.setting_info[OPENING_FORCE.key] = DmpInfo(1, 0x7F, 10, 1)
    with pytest.raises(NiceParameterError, match="unverified"):
        repository.prepare_write(OPENING_FORCE, 50)


def test_unsupported_and_unknown_values_keep_target_identity() -> None:
    repository = ParameterRepository()
    first = repository.address(4, 0x81)
    radio = repository.address(4, 0x81, target=(0, 10))
    repository.observe(first, b"\x1e")
    repository.observe(radio, b"\x64")
    assert first.key != radio.key
    assert repository.diagnostics()[first.key]["raw_value"] == 30
    for parameter in (0xFB, 0xB8, 0xBA, 0xBB):
        secret = repository.address(4, parameter)
        repository.observe(secret, b"private")
        assert secret.key not in repository.diagnostics()
    with pytest.raises(NiceParameterError, match="catalog"):
        repository.prepare_write(replace(PAUSE_TIME, parameter=0xFB), 1)


def test_other_devices_cannot_overwrite_metadata_and_other_targets_have_no_controller_units() -> (
    None
):
    repository = ParameterRepository(1)
    wrong_device = ParameterAddress(2, 4, 0x81)
    with pytest.raises(ValueError, match="different device"):
        repository.record_info(wrong_device, DmpInfo(1, 0, 0x11, 1, 0, 250, 1))
    with pytest.raises(ValueError, match="different device"):
        repository.observe(wrong_device, b"\x1e")
    other_target = repository.address(4, 0x81, target=(0, 10))
    assert repository.observe(other_target, b"\x1e").value is None
    assert repository.diagnostics()[other_target.key]["definition"] is None


def test_incomplete_range_metadata_is_not_a_write_fallback() -> None:
    repository = ParameterRepository()
    repository.setting_info[PAUSE_TIME.key] = DmpInfo(1, 0, 0x11, 1)
    with pytest.raises(NiceParameterError, match="incomplete"):
        repository.prepare_write(PAUSE_TIME, 30)

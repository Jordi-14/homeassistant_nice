"""Tests for MyNice cloud NHK proxy framing."""

from __future__ import annotations

from custom_components.nice_bidiwifi.cloud_api import ETX, STX, NhkSession


def _change_xml(action: str, device_id: str = "1") -> str:
    session = NhkSession(
        mac="AA:BB:CC:DD:EE:FF",
        user="user",
        password="AA" * 32,
        controller="controller",
    )
    session.session_id = 0x12
    session.session_pw = b"\x00" * 32
    frame = session.build_change(action, device_id)

    assert frame[0] == STX
    assert frame[-1] == ETX
    return frame[1:-1].decode()


def test_change_frame_uses_selected_device_id() -> None:
    """Test CHANGE commands target the discovered cloud device ID."""
    xml = _change_xml("open", "2")

    assert '<Device id="2">' in xml
    assert '<Device id="1">' not in xml
    assert "<DoorAction>open</DoorAction>" in xml


def test_change_frame_defaults_to_device_id_one() -> None:
    """Test CHANGE commands keep the old default when no device ID is supplied."""
    xml = _change_xml("stop")

    assert '<Device id="1">' in xml
    assert "<DoorAction>stop</DoorAction>" in xml

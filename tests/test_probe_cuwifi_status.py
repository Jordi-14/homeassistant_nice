"""Tests for the CU_WIFI status probe helpers."""

from __future__ import annotations

import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPT_DIR))

import probe_cuwifi_status as probe  # noqa: E402

CUWIFI_INFO_XML = """
<Response id="257" target="AA:BB:CC:DD:EE:FF" source="controller" type="INFO">
  <Interface>
    <Settings>
      <Name type="string" perm="r,w" />
    </Settings>
    <Events hyst_len="64">
      <LastEvent type="int" perm="r" />
    </Events>
  </Interface>
  <Devices>
    <Device id="1">
      <Services>
        <T4Action type="string" perm="w" />
        <DoorAction type="string" values="open, stop, close" perm="w" />
      </Services>
      <Properties>
        <DoorStatus type="string" values="open, closed, opening, closing, stopped" perm="r" />
        <Obstruct type="bool" perm="r" />
        <T4_allowed type="hex" values="0FFFF8FE" perm="r" />
      </Properties>
      <Events hyst_len="64">
        <LastEvent type="int" perm="r" />
      </Events>
    </Device>
  </Devices>
</Response>
"""


class FakeSocket:
    """Minimal socket fake for probe trace tests."""

    def __init__(self, frames: list[bytes]) -> None:
        self.frames = frames
        self.sent: list[bytes] = []
        self.timeout: float | None = None

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout

    def sendall(self, data: bytes) -> None:
        self.sent.append(data)

    def recv(self, _size: int) -> bytes:
        if self.frames:
            return self.frames.pop(0)
        raise TimeoutError

    def close(self) -> None:
        return None


def test_info_inventory_extracts_cuwifi_properties() -> None:
    """Test the probe reports readable properties separately from services."""
    inventory = probe._info_inventory(CUWIFI_INFO_XML)
    summary = probe._inventory_summary(inventory)

    assert "DoorAction" in summary["writable_services"]
    assert "DoorStatus" in summary["readable_properties"]
    assert "Obstruct" in summary["readable_properties"]
    assert "LastEvent" in summary["readable_events"]


def test_nhk_selector_candidates_include_individual_and_grouped_reads() -> None:
    """Test readable INFO nodes become selector request candidates."""
    inventory = probe._info_inventory(CUWIFI_INFO_XML)
    candidates = probe._nhk_selector_candidates(inventory, device_id=1)

    door_status = next(candidate for candidate in candidates if candidate["names"] == ["DoorStatus"])
    grouped_properties = next(
        candidate
        for candidate in candidates
        if candidate["container"] == "Properties" and set(candidate["names"]) == {"DoorStatus", "Obstruct", "T4_allowed"}
    )

    assert "<Properties>" in door_status["body"]
    assert "<DoorStatus />" in door_status["body"]
    assert "<Obstruct />" in grouped_properties["body"]
    assert '<Device id="1">' in grouped_properties["body"]


def test_generate_dmp_reads_prioritizes_known_status_registers() -> None:
    """Test broad DMP generation includes known status reads first and deduplicates."""
    reads = probe._generate_dmp_reads("broad", max_reads=400)
    keys = {(read.daddr, read.dendpoint, read.group, read.parameter) for read in reads}

    assert reads[0].group == 0x04
    assert reads[0].parameter == 0x01
    assert (0x00, 0x03, 0x04, 0x11) in keys
    assert (0x00, 0x03, 0x04, 0x3F) in keys
    assert len(keys) == len(reads)
    assert len(reads) <= 400


def test_signed_probe_trace_preserves_async_frame_before_response() -> None:
    """Test traced signed probes keep non-matching frames around the response."""
    client = probe.ProbeClient(
        "127.0.0.1",
        443,
        probe.NiceBidiCredentials(
            username="user",
            password_hex="00" * 32,
            target_mac="AA:BB:CC:DD:EE:FF",
            source_id="controller",
        ),
    )
    event = b'\x02<Event id="42" type="T4_EVENT"><DoorStatus>opening</DoorStatus></Event>\x03'
    response = b'\x02<Response id="257" type="STATUS"><DoorStatus>opening</DoorStatus></Response>\x03'
    fake_socket = FakeSocket([event, response])
    client._socket = fake_socket
    client._session_key = b"\x01" * 32
    client._session_id = 1
    client._sequence = 1

    trace = client.signed_probe_trace("STATUS")

    assert fake_socket.sent
    assert trace["expected_frame_index"] == 1
    assert trace["frames"] == [event, response]


def test_frame_report_marks_event_and_leaf_values() -> None:
    """Test raw async event frames are classified and parsed."""
    client = probe.ProbeClient(
        "127.0.0.1",
        443,
        probe.NiceBidiCredentials(
            username="user",
            password_hex="00" * 32,
            target_mac="AA:BB:CC:DD:EE:FF",
        ),
    )
    event = b'\x02<Event id="42" type="T4_EVENT"><DoorStatus>opening</DoorStatus></Event>\x03'

    report = probe._frame_report(client, event, include_sensitive=False)

    assert report["frame_kind"] == "Event"
    assert report["type"] == "T4_EVENT"
    assert report["leaf_values"][0]["name"] == "DoorStatus"
    assert report["leaf_values"][0]["value"] == "opening"


def test_redact_text_masks_response_identifiers() -> None:
    """Test raw XML in error messages is redacted before reporting."""
    text = '<Response target="AA:BB:CC:DD:EE:FF" source="controller"><Error /></Response>'

    redacted = probe._redact_text(text, include_sensitive=False)

    assert "AA:BB:CC:DD:EE:FF" not in redacted
    assert "controller" not in redacted
    assert "<redacted:" in redacted

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


class InterruptingLiveClient:
    """Minimal client fake that raises Ctrl-C during passive listening."""

    def __init__(self) -> None:
        self.trace_calls = 0

    def signed_probe_trace(self, *_args, **_kwargs) -> dict[str, object]:
        self.trace_calls += 1
        return {
            "request_id": self.trace_calls,
            "frames": [],
            "expected_frame_index": None,
        }

    def listen_frames(self, *_args, **_kwargs) -> list[bytes]:
        raise KeyboardInterrupt

    def decrypt_t4_payloads(self, _response: bytes) -> list[bytes]:
        return []


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


def test_generate_dmp_reads_includes_expanded_discovery_registers() -> None:
    """Test DMP generation includes controller, OXI, status, and diagnostics reads."""
    reads = probe._generate_dmp_reads("broad", max_reads=400)
    keys = {(read.daddr, read.dendpoint, read.group, read.parameter) for read in reads}

    assert reads[0].daddr == 0x00
    assert reads[0].dendpoint == 0x03
    assert reads[0].group == 0x00
    assert reads[0].parameter == 0x00
    assert (0x00, 0x03, 0x04, 0x11) in keys
    assert (0x00, 0x0A, 0x0A, 0x04) in keys
    assert (0x00, 0x03, 0x04, 0xD1) in keys
    assert (0x00, 0x0A, 0x04, 0xD0) in keys
    assert len(keys) == len(reads)
    assert len(reads) <= 400


def test_exhaustive_defaults_enable_broad_post_live_scan() -> None:
    """Test exhaustive mode expands selector, DMP, and frame-drain defaults."""
    args = type(
        "Args",
        (),
        {
            "exhaustive": True,
            "nhk_request_types": ["INFO", "READ"],
            "dmp_profile": "focused",
            "max_dmp_reads": 400,
            "post_request_listen_seconds": 0.75,
        },
    )()

    probe._apply_exhaustive_defaults(args)

    assert args.nhk_request_types == ["INFO", "READ", "GET"]
    assert args.dmp_profile == "broad"
    assert args.max_dmp_reads == 4096
    assert args.post_request_listen_seconds == 1.0


def test_parse_bus_t4_dmp_current_position() -> None:
    """Test BusT4 parser decodes DMP current-position responses."""
    plain = bytes.fromhex("50 90 00 03 08 08 C3 04 11 19 02 00 17 16 0F")

    parsed = probe._parse_bus_t4_payload(plain)

    assert parsed["from"] == "00.03"
    assert parsed["from_role"] == "possible controller"
    assert parsed["message_type_name"] == "INF/DMP"
    assert parsed["message_size_matches"] is True
    assert parsed["inf"]["device_type_name"] == "controller"
    assert parsed["inf"]["register_label"] == "current encoder position"
    assert parsed["inf"]["operation_kind"] == "response"
    assert parsed["inf"]["value_decode"]["position"]["position"] == 5910


def test_parse_bus_t4_cmd_status_position() -> None:
    """Test BusT4 parser decodes RSP/CMD-style status and position packets."""
    plain = bytes.fromhex("00 FF 00 03 01 07 FA 04 02 83 01 64 64 84")

    parsed = probe._parse_bus_t4_payload(plain)

    assert parsed["from_role"] == "possible controller"
    assert parsed["message_type_name"] == "CMD/DEP"
    assert parsed["message_size_matches"] is True
    assert parsed["cmd"]["menu_name"] == "controller"
    assert parsed["cmd"]["status"] == "opening"
    assert parsed["cmd"]["possible_position"] == 356


def test_summary_counts_decoded_bus_t4_values() -> None:
    """Test the report summary surfaces decoded status, position, and diagnostics."""
    position_parse = probe._parse_bus_t4_payload(bytes.fromhex("50 90 00 03 08 08 C3 04 11 19 02 00 17 16 0F"))
    status_parse = probe._parse_bus_t4_payload(bytes.fromhex("00 FF 00 03 01 07 FA 04 02 83 01 64 64 84"))
    diag_parse = probe._parse_bus_t4_payload(bytes.fromhex("50 90 00 03 08 09 C3 04 D1 19 03 00 00 00 03 0F"))
    report = {
        "info_samples": [],
        "live_capture": {},
        "nhk_read_probes": [],
        "dmp_register_probes": [
            {
                "ok": True,
                "plain_payloads": [
                    {"bus_t4_parse": position_parse},
                    {"bus_t4_parse": status_parse},
                    {"bus_t4_parse": diag_parse},
                ],
            }
        ],
    }

    summary = probe._summarize_results(report)
    counts = summary["counts"]

    assert counts["decoded_bus_t4_statuses"]["opening"] == 1
    assert counts["decoded_bus_t4_positions"]["current encoder position: 5910"] == 1
    assert counts["decoded_bus_t4_positions"]["CMD/DEP possible position: 356"] == 1
    assert counts["decoded_bus_t4_diag_io"]["limit_closed"] == 1
    assert counts["decoded_bus_t4_diag_io"]["limit_open"] == 1


def test_manual_stop_live_capture_continues_after_interrupt() -> None:
    """Test Ctrl-C ends only the live capture in manual-stop mode."""
    args = type(
        "Args",
        (),
        {
            "listen_until_interrupted": True,
            "listen_seconds": 60.0,
            "listen_poll_timeout": 0.01,
            "post_request_listen_seconds": 0.0,
            "status_poll_interval": 0.0,
            "t4_status_poll_interval": 0.0,
            "info_poll_interval": 0.0,
            "timeout": 0.01,
            "include_sensitive": False,
            "quiet": True,
        },
    )()
    client = InterruptingLiveClient()

    result = probe._run_live_capture(client, args, started=0.0)

    assert result["manual_stop"] is True
    assert result["ended_by_interrupt"] is True
    assert result["duration_requested_s"] is None
    assert len(result["initial_request_traces"]) == 3


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

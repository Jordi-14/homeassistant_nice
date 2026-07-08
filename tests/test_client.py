"""Tests for the local Nice protocol client."""

from __future__ import annotations

import base64
import ssl

import pytest

from custom_components.nice_bidiwifi.client import (
    DEP_ACTION_COMMANDS,
    DEP_ACTION_PARTIAL_OPEN_1,
    ETX,
    STX,
    NiceBidiAuthError,
    NiceBidiClient,
    NiceBidiConnectionError,
    NiceBidiCredentials,
    NiceBidiDeviceInfo,
    NiceBidiStatus,
    _frame,
    _make_context,
    _random_t4_key,
    _response_summary,
    _xml_escape,
    _xml_payload,
    _xor_sha256,
    build_dep_action_frame,
    build_dmp_read_frame,
    build_dmp_write_frame,
    device_info_supports_nhk_status,
    nice_bidi_error_code,
    parse_dmp_response,
    parse_info_xml,
)


def _dmp_response(group: int, parameter: int, value: bytes) -> bytes:
    body = bytes([0x55, 0x0D, 0x00, 0x03, 0x50, 0x91, 0x08, 0x08, 0x00, group, parameter, 0x19, len(value), 0x02])
    return body + value + bytes([0x0D])


def _encrypted_t4_event(plain: bytes) -> bytes:
    key = b"event-key"
    encrypted = _xor_sha256(plain, key)
    return (
        STX
        + b'<Event type="T4_EVENT" id="42"><Interface id="1"><T4 protocol="DEP" key="'
        + base64.b64encode(key)
        + b'">'
        + base64.b64encode(encrypted)
        + b"</T4></Interface></Event>"
        + ETX
    )


def _client() -> NiceBidiClient:
    return NiceBidiClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )


class FakeSocket:
    """Socket fake for client unit tests."""

    def __init__(self, responses: list[bytes] | None = None) -> None:
        self.responses = responses or []
        self.sent: list[bytes] = []
        self.timeouts: list[float] = []
        self.closed = False

    def settimeout(self, timeout: float) -> None:
        """Record timeout changes."""
        self.timeouts.append(timeout)

    def sendall(self, data: bytes) -> None:
        """Record sent payloads."""
        self.sent.append(data)

    def recv(self, size: int) -> bytes:
        """Return queued responses, then time out."""
        if self.responses:
            return self.responses.pop(0)
        raise TimeoutError

    def close(self) -> None:
        """Record close."""
        self.closed = True


def test_credentials_decode_hex_password() -> None:
    """Test credential password decoding."""
    credentials = NiceBidiCredentials(
        username="user",
        password_hex="AB" * 32,
        target_mac="AA:BB:CC:DD:EE:FF",
    )

    assert credentials.password == bytes.fromhex("AB" * 32)
    assert credentials.source == "user"


def test_credentials_reject_invalid_password() -> None:
    """Test invalid credential password handling."""
    credentials = NiceBidiCredentials(
        username="user",
        password_hex="not hex",
        target_mac="AA:BB:CC:DD:EE:FF",
    )

    with pytest.raises(NiceBidiAuthError):
        _ = credentials.password


def test_nice_bidi_error_code_extracts_xml_code() -> None:
    """Test XML error code extraction."""
    err = NiceBidiConnectionError('<Response><Error><Code>14</Code></Error></Response>')

    assert nice_bidi_error_code(err) == "14"
    assert nice_bidi_error_code("offline") is None


def test_parse_dmp_response_extracts_register_value() -> None:
    """Test DMP response parsing."""
    parsed = parse_dmp_response(_dmp_response(0x04, 0x11, bytes.fromhex("12 34")))

    assert parsed["group"] == "04"
    assert parsed["parameter"] == "11"
    assert parsed["operation"] == "19"
    assert parsed["value_hex"] == "12 34"
    assert parsed["value_uint_be"] == 0x1234


def test_build_dmp_write_frame_builds_single_byte_set() -> None:
    """Test DMP SET frame construction."""
    plain = build_dmp_write_frame(0x00, 0x03, 0x04, 0x80, b"\x01")

    assert plain == bytes.fromhex("55 0e 00 03 50 91 08 07 cd 04 80 a9 00 01 01 2d 0e")


def test_build_dmp_write_frame_rejects_empty_value() -> None:
    """Test DMP SET frame validation."""
    with pytest.raises(ValueError, match="at least one byte"):
        build_dmp_write_frame(0x00, 0x03, 0x04, 0x80, b"")


def test_build_dep_action_frame_matches_captured_partial_open_1() -> None:
    """Test DEP action frame construction."""
    frame = build_dep_action_frame(DEP_ACTION_COMMANDS[DEP_ACTION_PARTIAL_OPEN_1])

    assert frame == bytes.fromhex("55 0c 00 03 50 91 01 05 c6 01 82 05 64 e2 0c")


def test_build_dep_action_frame_rejects_invalid_command() -> None:
    """Test DEP action frame command validation."""
    with pytest.raises(ValueError, match="command must be a byte"):
        build_dep_action_frame(0x100)


def test_frame_helpers_escape_and_strip_control_bytes() -> None:
    """Test XML helper utilities."""
    xml = '<Request value="a&b">'

    framed = _frame(xml)

    assert framed == STX + xml.encode() + ETX
    assert _xml_payload(framed) == xml
    assert _xml_escape('a&b"<>') == "a&amp;b&quot;&lt;&gt;"


def test_t4_key_uses_secrets_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test T4 keys use the cryptographic randomness helper."""
    calls = []

    def fake_choice(alphabet: str) -> str:
        calls.append(alphabet)
        return "A"

    monkeypatch.setattr("custom_components.nice_bidiwifi.client.secrets.choice", fake_choice)

    assert _random_t4_key(4) == b"AAAA"
    assert len(calls) == 4


def test_tls_context_keeps_device_compatible_insecure_mode() -> None:
    """Test TLS context keeps compatibility with BiDi local certificates."""
    context = _make_context()

    assert context.check_hostname is False
    assert context.verify_mode == ssl.CERT_NONE
    assert context.minimum_version == ssl.TLSVersion.TLSv1_2


def test_read_status_combines_registers_into_status() -> None:
    """Test status construction from DMP registers."""

    class StatusClient(NiceBidiClient):
        def _t4_request_locked(self, protocol, plain_payload, daddr, dendpoint, tout_ms):
            values = {
                (0x04, 0x01): b"\x02",
                (0x04, 0x11): (5000).to_bytes(2, "big"),
                (0x04, 0x18): (10000).to_bytes(2, "big"),
                (0x04, 0x19): (0).to_bytes(2, "big"),
                (0x04, 0x42): b"\x64",
                (0x04, 0x4A): b"\x46",
                (0x04, 0x80): b"\x01",
                (0x04, 0xB2): (12).to_bytes(2, "big"),
                (0x04, 0xB3): (345).to_bytes(2, "big"),
                (0x04, 0xD0): b"\x01",
                (0x04, 0xD1): b"\x00\x00\x03",
            }
            key = (plain_payload[9], plain_payload[10])
            if key not in values:
                return b"<Response><Error><Code>14</Code></Error></Response>", []
            return b"<Response />", [_dmp_response(*key, values[key])]

    status = StatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_status_locked(include_extended=True)

    assert status.state == "opening"
    assert status.position == 50.0
    assert status.current_position == 5000
    assert status.closed_position == 0
    assert status.open_position == 10000
    assert status.registers["04/01"] == "02"
    assert status.opening_speed == 100
    assert status.opening_force == 70
    assert status.auto_close is True
    assert status.maintenance_count == 12
    assert status.total_maneuver_count == 345
    assert status.limit_closed is True
    assert status.limit_open is True
    assert status.diagnostics_io_byte == 3
    assert status.obstacle is True
    assert status.last_stop_reason == "obstacle_by_encoder"


def test_read_info_extracts_interface_and_device_metadata() -> None:
    """Test INFO response parsing."""

    class InfoClient(NiceBidiClient):
        def _signed_exchange_locked(self, request_type, body=""):
            return (
                STX
                + b"<Response>"
                + b"<Interface><VersionHW>HW1</VersionHW><VersionFW>FW1</VersionFW>"
                + b"<Manuf>NICE</Manuf><Prod>BiDi-WiFi</Prod><SerialNr>IFACE123</SerialNr></Interface>"
                + b"<Devices><Device id=\"1\"><Type>gate</Type><Manuf>NICE</Manuf><Prod>NewRobus</Prod>"
                + b"<Desc>NewRobus</Desc><VersionHW>HW2</VersionHW><VersionFW>FG01h</VersionFW>"
                + b"<SerialNr>0E6809FF</SerialNr><ProdDTL>detail</ProdDTL></Device></Devices>"
                + b"</Response>"
                + ETX
            )

    info = InfoClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_info_locked()

    assert info == NiceBidiDeviceInfo(
        interface_hw_version="HW1",
        interface_fw_version="FW1",
        interface_manufacturer="NICE",
        interface_product="BiDi-WiFi",
        interface_serial="IFACE123",
        device_type="gate",
        device_manufacturer="NICE",
        device_product="NewRobus",
        device_description="NewRobus",
        device_hw_version="HW2",
        device_fw_version="FG01h",
        device_serial="0E6809FF",
        device_product_detail="detail",
    )


def test_parse_info_xml_extracts_service_and_property_capabilities() -> None:
    """Test INFO service and property capability parsing."""
    info = parse_info_xml(
        """
        <Response>
          <Interface id="1">
            <Services>
              <T4_allowed type="hex" values="1FFFF8FE" perm="r"/>
            </Services>
          </Interface>
          <Devices>
            <Device id="1">
              <Services>
                <DoorAction type="string" values="open, stop, close" perm="w"/>
              </Services>
              <Properties>
                <DoorStatus type="string" values="open, closed" perm="r"/>
                <Obstruct type="bool" perm="r"/>
              </Properties>
            </Device>
          </Devices>
        </Response>
        """
    )

    assert [service.name for service in info.services] == [
        "T4_allowed",
        "DoorAction",
    ]
    assert [prop.name for prop in info.properties] == ["DoorStatus", "Obstruct"]
    assert info.services[1].owner == "Device"
    assert info.services[1].owner_id == "1"
    assert info.services[1].path == 'Response/Devices/Device[@id="1"]/Services/DoorAction'
    assert info.services[1].value_type == "string"
    assert info.services[1].permission == "w"
    assert info.services[1].values == ("open", "stop", "close")
    assert info.properties[0].path == 'Response/Devices/Device[@id="1"]/Properties/DoorStatus'
    assert info.properties[0].permission == "r"
    assert info.properties[0].values == ("open", "closed")
    assert device_info_supports_nhk_status(info)


def test_device_info_supports_nhk_status_checks_device_id() -> None:
    """Test readable DoorStatus detection honors the selected device."""
    info = parse_info_xml(
        """
        <Response>
          <Devices>
            <Device id="2">
              <Properties>
                <DoorStatus type="string" values="open, closed" perm="r"/>
              </Properties>
            </Device>
          </Devices>
        </Response>
        """
    )

    assert not device_info_supports_nhk_status(info, device_id=1)
    assert device_info_supports_nhk_status(info, device_id=2)


def test_read_info_xml_returns_payload() -> None:
    """Test raw INFO XML reading."""

    class InfoClient(NiceBidiClient):
        def _signed_exchange_locked(self, request_type, body=""):
            return STX + b'<Response type="INFO" id="263"><Interface /></Response>' + ETX

    client = InfoClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )

    assert client._read_info_xml_locked() == '<Response type="INFO" id="263"><Interface /></Response>'


def test_read_nhk_status_parses_status_response() -> None:
    """Test CU_WIFI-style NHK STATUS DoorStatus parsing."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            assert request_type == "STATUS"
            return [
                STX
                + b'<Response type="STATUS" id="513"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>closing</DoorStatus><Obstruct>0</Obstruct>"
                + b"</Properties></Device></Devices></Response>"
                + ETX
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "closing"
    assert status.position is None
    assert status.current_position is None
    assert status.obstacle is False
    assert status.registers == {
        "NHK/DoorStatus": "closing",
        "NHK/Obstruct": "0",
    }


def test_read_nhk_status_accepts_change_event_frame() -> None:
    """Test async CHANGE frames use the same DoorStatus parser."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            return [
                STX
                + b'<Event type="CHANGE" id="41"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>stopped</DoorStatus><Obstruct>1</Obstruct>"
                + b"</Properties></Device></Devices></Event>"
                + ETX
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "stopped"
    assert status.obstacle is True


def test_read_nhk_status_parses_cuwifi_instant_position_event() -> None:
    """Test CU_WIFI 04/40 live T4 events can provide tentative position."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            assert request_type == "STATUS"
            assert post_response_listen_seconds > 0
            return [
                STX
                + b'<Response type="STATUS" id="513"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>opening</DoorStatus><Obstruct>0</Obstruct>"
                + b"</Properties></Device></Devices></Response>"
                + ETX,
                _encrypted_t4_event(bytes.fromhex("55 0f 00 ff 00 03 01 08 f5 04 40 00 00 4c ff ff 52 0f")),
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "opening"
    assert status.position == 76.0
    assert status.current_position is None
    assert status.closed_position is None
    assert status.open_position is None
    assert status.registers["NHK/T4InstantPosition"] == "76"
    assert status.registers["NHK/T4InstantPositionPayload"] == "04 40 00 00 4c ff ff"


def test_read_nhk_status_keeps_moving_state_for_intermediate_cuwifi_stopped_position() -> None:
    """Test CU_WIFI 04/40 stopped state does not override moving DoorStatus."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            return [
                STX
                + b'<Response type="STATUS" id="513"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>closing</DoorStatus><Obstruct>0</Obstruct>"
                + b"</Properties></Device></Devices></Response>"
                + ETX,
                _encrypted_t4_event(bytes.fromhex("55 0f 00 ff 00 03 01 08 f5 04 40 01 00 4c ff ff 6b 0f")),
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "closing"
    assert status.position == 76.0
    assert status.obstacle is False
    assert status.registers["NHK/DoorStatus"] == "closing"
    assert status.registers["NHK/T4Status"] == "stopped"
    assert status.registers["NHK/T4StatusIgnored"] == "stopped_with_intermediate_position"
    assert status.registers["NHK/T4InstantPosition"] == "76"
    assert status.registers["NHK/T4PayloadKind"] == "04/40"


def test_read_nhk_status_uses_cuwifi_t4_endpoint_status() -> None:
    """Test CU_WIFI 04/02 endpoint events provide terminal state and position."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            return [
                STX
                + b'<Response type="STATUS" id="513"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>opening</DoorStatus><Obstruct>0</Obstruct>"
                + b"</Properties></Device></Devices></Response>"
                + ETX,
                _encrypted_t4_event(
                    bytes.fromhex(
                        "55 1e 00 ff 00 03 01 17 ea 04 02 04 00 00 00 00 00 02 0c 00 00 00 0d 00 00 1b 58 00 00 00 74 36 1e"
                    )
                ),
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "open"
    assert status.position == 100.0
    assert status.registers["NHK/T4Status"] == "open"
    assert status.registers["NHK/T4PayloadKind"] == "04/02"


def test_read_nhk_status_ignores_out_of_range_cuwifi_position_event() -> None:
    """Test invalid tentative CU_WIFI position frames fall back to state-only."""

    class NhkStatusClient(NiceBidiClient):
        def _signed_exchange_frames_locked(
            self,
            request_type,
            body="",
            *,
            post_response_listen_seconds=0.0,
        ):
            return [
                STX
                + b'<Response type="STATUS" id="513"><Devices><Device id="1"><Properties>'
                + b"<DoorStatus>opening</DoorStatus><Obstruct>0</Obstruct>"
                + b"</Properties></Device></Devices></Response>"
                + ETX,
                _encrypted_t4_event(bytes.fromhex("55 0f 00 ff 00 03 01 08 f5 04 40 00 04 00 ff ff 52 0f")),
            ]

    status = NhkStatusClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )._read_nhk_status_locked()

    assert status.state == "opening"
    assert status.position is None
    assert "NHK/T4InstantPosition" not in status.registers


def test_decrypt_t4_payloads_returns_plain_payloads() -> None:
    """Test T4 payload decryption."""
    key = b"test-key"
    plain = build_dmp_read_frame(0x00, 0x03, 0x04, 0x11)
    encrypted = _xor_sha256(plain, key)
    response = (
        b'<Response><T4 key="'
        + base64.b64encode(key)
        + b'">'
        + base64.b64encode(encrypted)
        + b"</T4></Response>"
    )

    assert _client()._decrypt_t4_payloads(response) == [plain]


def test_response_summary_omits_raw_t4_payload() -> None:
    """Test response summaries do not expose raw T4 data."""
    response = b'<Response type="T4_REQUEST" id="263"><T4 key="secret-key">secret-payload</T4></Response>'

    summary = _response_summary(response)

    assert "type=T4_REQUEST" in summary
    assert "id=263" in summary
    assert "t4_payloads=1" in summary
    assert "secret-key" not in summary
    assert "secret-payload" not in summary


def test_signed_request_increments_request_id_and_adds_signature() -> None:
    """Test signed request construction."""
    client = _client()
    client._session_id = 7
    client._session_key = b"session-key"

    xml, request_id = client._signed_request_with_id("INFO")

    assert request_id == 0x0107
    assert 'type="INFO"' in xml
    assert "<Sign>" in xml
    assert client._sequence == 2


def test_send_locked_returns_matching_response() -> None:
    """Test framed request/response exchange."""
    client = _client()
    response = STX + b'<Response type="INFO" id="263"></Response>' + ETX
    socket = FakeSocket([response])
    client._socket = socket

    result = client._send_locked("<Request />", expected_type="INFO", expected_id=263)

    assert result == response
    assert socket.sent == [_frame("<Request />")]


def test_send_locked_raises_when_device_does_not_respond() -> None:
    """Test send timeout handling."""
    client = _client()
    client.timeout = 0.01
    client._socket = FakeSocket()

    with pytest.raises(NiceBidiConnectionError, match="device did not respond"):
        client._send_locked("<Request />", expected_type="INFO", expected_id=1)


def test_connect_locked_sets_session_data() -> None:
    """Test CONNECT response handling."""

    class ConnectClient(NiceBidiClient):
        def _send_locked(self, xml, expected_type, expected_id):
            return STX + b'<Response><Authentication id="9" sc="AABBCCDD" /></Response>' + ETX

    client = ConnectClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )

    client._connect_locked()

    assert client._session_id == 9
    assert client._sequence == 1
    assert client._session_key is not None


def test_connect_locked_uses_secrets_randbits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test CONNECT challenge uses cryptographic randomness."""
    sent_xml = ""

    class ConnectClient(NiceBidiClient):
        def _send_locked(self, xml, expected_type, expected_id):
            nonlocal sent_xml
            sent_xml = xml
            return STX + b'<Response><Authentication id="9" sc="AABBCCDD" /></Response>' + ETX

    monkeypatch.setattr("custom_components.nice_bidiwifi.client.secrets.randbits", lambda bits: 0x1234ABCD)
    client = ConnectClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )

    client._connect_locked()

    assert 'cc="1234ABCD"' in sent_xml


def test_connect_locked_maps_error_response_to_auth_error() -> None:
    """Test CONNECT auth errors."""

    class ConnectClient(NiceBidiClient):
        def _send_locked(self, xml, expected_type, expected_id):
            return STX + b"<Response><Error>denied</Error></Response>" + ETX

    client = ConnectClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )

    with pytest.raises(NiceBidiAuthError):
        client._connect_locked()


def test_test_connection_falls_back_to_info_when_dmp_status_is_unsupported() -> None:
    """Test setup validation accepts devices with INFO but no DMP status."""

    class CommandOnlyClient(NiceBidiClient):
        def __init__(self):
            super().__init__(
                "192.0.2.10",
                443,
                NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
            )
            self.info_reads = 0

        def read_status(self):
            raise NiceBidiConnectionError(
                '<Response type="T4_REQUEST"><Error><Code>14</Code></Error></Response>'
            )

        def read_info(self):
            self.info_reads += 1
            return NiceBidiDeviceInfo(
                interface_hw_version=None,
                interface_fw_version=None,
                interface_manufacturer=None,
                interface_product=None,
                interface_serial=None,
                device_type=None,
                device_manufacturer=None,
                device_product=None,
                device_description=None,
                device_hw_version=None,
                device_fw_version=None,
                device_serial=None,
                device_product_detail=None,
            )

    client = CommandOnlyClient()

    status = client.test_connection()

    assert status.state is None
    assert status.position is None
    assert status.registers == {}
    assert client.info_reads == 1


def test_run_with_reconnect_retries_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test reconnect loop behavior."""
    client = _client()
    ensure_calls = 0
    operation_calls = 0

    def ensure_connected() -> None:
        nonlocal ensure_calls
        ensure_calls += 1

    def operation() -> str:
        nonlocal operation_calls
        operation_calls += 1
        if operation_calls == 1:
            raise NiceBidiConnectionError("temporary")
        return "ok"

    monkeypatch.setattr(client, "_ensure_connected_locked", ensure_connected)
    monkeypatch.setattr(client, "_close_locked", lambda: None)
    monkeypatch.setattr("custom_components.nice_bidiwifi.client.time.sleep", lambda seconds: None)

    assert client._run_with_reconnect(operation) == "ok"
    assert ensure_calls == 2
    assert client.reconnect_count == 1


def test_send_action_rejects_invalid_action() -> None:
    """Test action validation."""
    with pytest.raises(ValueError, match="action must be open"):
        _client().send_action("toggle")


def test_send_action_locked_raises_on_error_response() -> None:
    """Test CHANGE error handling."""

    class ActionClient(NiceBidiClient):
        def _signed_exchange_locked(self, request_type, body=""):
            assert request_type == "CHANGE"
            assert "<DoorAction>open</DoorAction>" in body
            return STX + b"<Response><Error>blocked</Error></Response>" + ETX

    client = ActionClient(
        "192.0.2.10",
        443,
        NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
    )

    with pytest.raises(NiceBidiConnectionError, match="blocked"):
        client._send_action_locked("open")


def test_send_dep_action_rejects_invalid_action() -> None:
    """Test DEP action validation."""
    with pytest.raises(ValueError, match="action must be one of"):
        _client().send_dep_action("not-an-action")


def test_send_dep_action_locked_sends_dep_frame() -> None:
    """Test low-level DEP action command sending."""

    class DepActionClient(NiceBidiClient):
        def __init__(self):
            super().__init__(
                "192.0.2.10",
                443,
                NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
            )
            self.request = None

        def _t4_request_locked(self, protocol, plain_payload, daddr, dendpoint, tout_ms):
            self.request = (protocol, plain_payload, daddr, dendpoint, tout_ms)
            return b"<Response />", []

    client = DepActionClient()

    client._send_dep_action_locked(DEP_ACTION_PARTIAL_OPEN_1)

    assert client.request == (
        "DEP",
        bytes.fromhex("55 0c 00 03 50 91 01 05 c6 01 82 05 64 e2 0c"),
        0x00,
        0x03,
        200,
    )


def test_write_dmp_register_sends_expected_t4_request() -> None:
    """Test low-level DMP register writes."""

    class WriteClient(NiceBidiClient):
        def __init__(self):
            super().__init__(
                "192.0.2.10",
                443,
                NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
            )
            self.request = None

        def _ensure_connected_locked(self):
            return None

        def _t4_request_locked(self, protocol, plain_payload, daddr, dendpoint, tout_ms):
            self.request = (protocol, plain_payload, daddr, dendpoint, tout_ms)
            return b"<Response />", []

    client = WriteClient()

    client.write_dmp_register(0x04, 0xB1, 7000, size=2)

    assert client.request == (
        "DMP",
        build_dmp_write_frame(0x00, 0x03, 0x04, 0xB1, bytes.fromhex("1b 58")),
        0x00,
        0x03,
        200,
    )


def test_t4_request_builds_body_and_decrypts_response(monkeypatch: pytest.MonkeyPatch) -> None:
    """Test T4 request wrapping."""
    key = b"fixed-key"
    plain_response = b"plain-response"

    class T4Client(NiceBidiClient):
        def __init__(self):
            super().__init__(
                "192.0.2.10",
                443,
                NiceBidiCredentials("user", "AA" * 32, "AA:BB:CC:DD:EE:FF"),
            )
            self.body = ""

        def _signed_exchange_locked(self, request_type, body=""):
            self.body = body
            encrypted = _xor_sha256(plain_response, key)
            return (
                b'<Response><T4 key="'
                + base64.b64encode(key)
                + b'">'
                + base64.b64encode(encrypted)
                + b"</T4></Response>"
            )

    monkeypatch.setattr("custom_components.nice_bidiwifi.client._random_t4_key", lambda: key)
    client = T4Client()

    response, plains = client._t4_request_locked(
        "DMP",
        build_dmp_read_frame(0x00, 0x03, 0x04, 0x11),
        0x00,
        0x03,
        200,
    )

    assert response.startswith(b"<Response>")
    assert plains == [plain_response]
    assert "<Protocol tout=\"200\">DMP</Protocol>" in client.body


def test_status_reports_moving_states() -> None:
    """Test status movement helper."""
    assert NiceBidiStatus("opening", 10, 100, 0, 1000, {}).is_moving
    assert not NiceBidiStatus("open", 100, 1000, 0, 1000, {}).is_moving

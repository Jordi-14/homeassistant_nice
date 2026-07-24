"""Local NHK/T4 client for Nice."""

from __future__ import annotations

import base64
from collections.abc import Callable
import logging
import re
import secrets
import ssl
import threading
import time
from typing import Any

from .errors import (
    NiceAuthError as NiceBidiAuthError,
    NiceConnectionError as NiceBidiConnectionError,
    NiceError as NiceBidiError,
    nice_error_code as nice_bidi_error_code,
)
from .models.credentials import NiceCredentials as NiceBidiCredentials
from .models.device import (
    NiceDeviceInfo as NiceBidiDeviceInfo,
    NiceServiceCapability as NiceBidiServiceCapability,
)
from .models.status import (
    STATE_CLOSED,
    STATE_CLOSING,
    STATE_OPEN,
    STATE_OPENING,
    STATE_PARTIALLY_OPEN,
    STATE_STOPPED,
    NiceStatus as NiceBidiStatus,
)
from .protocol.nhk.codec import (
    ETX,
    STX,
    frame_xml as _frame,
    printable as _printable,
    response_matches,
    response_error_summary as _response_error_summary,
    response_summary as _response_summary,
    reverse_hex as _reverse_hex,
    sha256 as _sha256,
    xml_attribute as _attr,
    xml_escape as _xml_escape,
    xml_payload as _xml_payload,
)
from .protocol.nhk.info import device_info_supports_nhk_status, parse_info_xml
from .protocol.nhk.status import parse_nhk_status_frames as _parse_nhk_status_frames
from .protocol.t4.actions import (
    DEP_ACTION_COMMANDS,
    DEP_ACTION_COURTESY_LIGHT,
    DEP_ACTION_COURTESY_LIGHT_TIMER,
    DEP_ACTION_LOCK,
    DEP_ACTION_PARTIAL_OPEN_1,
    DEP_ACTION_PARTIAL_OPEN_2,
    DEP_ACTION_PARTIAL_OPEN_3,
    DEP_ACTION_STEP_STEP,
    DEP_ACTION_UNLOCK,
    build_dep_action_frame,
)
from .protocol.t4.codec import (
    decrypt_t4_payloads_from_frame as _decrypt_t4_payloads_from_frame,
    random_t4_key as _random_t4_key,
    xor_sha256 as _xor_sha256,
)
from .protocol.t4.dmp import (
    STATUS_BY_BYTE,
    build_dmp_read_frame,
    build_dmp_write_frame,
    decode_dmp_response,
    DmpResponse,
    parse_dmp_response,
)
from .protocol.t4.registers import (
    CONTROLLER_TARGET,
    CORE_STATUS_PROFILE,
    EXTENDED_CONTROLLER_PROFILE,
    OXI_INFO_PROFILE,
)
from .protocol.t4.status import status_from_dmp_registers
from .transport.dispatcher import (
    RawEventCallback,
    ReaderFailureCallback,
    ResponseDispatcher,
)
from .transport.lan import (
    LanTlsTransport,
    SocketFrameTransport,
    SocketLike,
    make_local_tls_context as _make_context,
)

_LOGGER = logging.getLogger(__name__)

__all__ = [
    "DEP_ACTION_COURTESY_LIGHT",
    "DEP_ACTION_COURTESY_LIGHT_TIMER",
    "DEP_ACTION_LOCK",
    "DEP_ACTION_PARTIAL_OPEN_1",
    "DEP_ACTION_PARTIAL_OPEN_2",
    "DEP_ACTION_PARTIAL_OPEN_3",
    "DEP_ACTION_STEP_STEP",
    "DEP_ACTION_UNLOCK",
    "ETX",
    "NiceBidiServiceCapability",
    "STATE_CLOSED",
    "STATE_CLOSING",
    "STATE_OPEN",
    "STATE_OPENING",
    "STATE_PARTIALLY_OPEN",
    "STATE_STOPPED",
    "STATUS_BY_BYTE",
    "STX",
    "_make_context",
    "parse_dmp_response",
]

NHK_STATUS_POST_RESPONSE_LISTEN_SECONDS = 0.75


class NiceBidiClient:
    """Persistent local NHK client for a Nice interface."""

    def __init__(
        self,
        host: str,
        port: int,
        credentials: NiceBidiCredentials,
        *,
        device_id: int = 1,
        timeout: float = 10.0,
        t4_timeout_ms: int = 200,
    ) -> None:
        self.host = host
        self.port = port
        self.credentials = credentials
        self.device_id = device_id
        self.timeout = timeout
        self.t4_timeout_ms = t4_timeout_ms
        self._transport: SocketFrameTransport | None = None
        self._dispatcher: ResponseDispatcher | None = None
        self._event_callbacks: list[RawEventCallback] = []
        self._event_failure_callbacks: list[ReaderFailureCallback] = []
        self._session_id = 0
        self._session_key: bytes | None = None
        self._sequence = 1
        self._lock = threading.RLock()
        self._reconnect_count = 0

    @property
    def _socket(self) -> SocketLike | None:
        """Return the underlying socket for backward-compatible diagnostics."""
        return self._transport.socket if self._transport is not None else None

    @_socket.setter
    def _socket(self, connected_socket: SocketLike | None) -> None:
        """Attach a socket through the framed transport compatibility boundary."""
        if connected_socket is None:
            self._close_locked()
            return
        self._set_transport(SocketFrameTransport(connected_socket))

    def _set_transport(self, transport: SocketFrameTransport) -> None:
        """Set the active transport and its sole reader."""
        self._transport = transport
        self._dispatcher = ResponseDispatcher(transport)
        for callback in self._event_callbacks:
            self._dispatcher.add_event_callback(callback)
        for callback in self._event_failure_callbacks:
            self._dispatcher.add_failure_callback(callback)

    def add_event_callback(self, callback: RawEventCallback) -> Callable[[], None]:
        """Register a callback for unsolicited protocol frames."""
        with self._lock:
            self._event_callbacks.append(callback)
            if self._dispatcher is not None:
                self._dispatcher.add_event_callback(callback)

        def remove() -> None:
            with self._lock:
                if callback in self._event_callbacks:
                    self._event_callbacks.remove(callback)
                if self._dispatcher is not None:
                    self._dispatcher.remove_event_callback(callback)

        return remove

    def add_event_failure_callback(
        self,
        callback: ReaderFailureCallback,
    ) -> Callable[[], None]:
        """Register a callback for an interrupted unsolicited-event stream."""
        with self._lock:
            self._event_failure_callbacks.append(callback)
            if self._dispatcher is not None:
                self._dispatcher.add_failure_callback(callback)

        def remove() -> None:
            with self._lock:
                if callback in self._event_failure_callbacks:
                    self._event_failure_callbacks.remove(callback)
                if self._dispatcher is not None:
                    self._dispatcher.remove_failure_callback(callback)

        return remove

    @property
    def event_stream_active(self) -> bool:
        """Return whether the persistent protocol reader is active."""
        dispatcher = self._dispatcher
        return bool(dispatcher and dispatcher.running)

    @property
    def event_stream_error(self) -> str | None:
        """Return the current reader failure without exposing frame contents."""
        dispatcher = self._dispatcher
        failure = dispatcher.failure if dispatcher is not None else None
        return failure.__class__.__name__ if failure is not None else None

    @property
    def reconnect_count(self) -> int:
        """Return the number of internal reconnect attempts."""
        with self._lock:
            return self._reconnect_count

    def close(self) -> None:
        """Close the current session."""
        with self._lock:
            self._close_locked()

    def read_status(self, *, include_extended: bool = False) -> NiceBidiStatus:
        """Read status and position DMP registers."""
        return self._run_with_reconnect(
            lambda: self._read_status_locked(include_extended=include_extended)
        )

    def read_nhk_status(self) -> NiceBidiStatus:
        """Read state from NHK STATUS/CHANGE properties."""
        return self._run_with_reconnect(self._read_nhk_status_locked)

    def read_info(self) -> NiceBidiDeviceInfo:
        """Read static BiDi-WiFi and control-unit metadata."""
        return self._run_with_reconnect(self._read_info_locked)

    def read_info_xml(self) -> str:
        """Read raw INFO XML from the BiDi-WiFi."""
        return self._run_with_reconnect(self._read_info_xml_locked)

    def send_action(self, action: str) -> None:
        """Send a high-level DoorAction command."""
        if action not in {"open", "stop", "close"}:
            raise ValueError("action must be open, stop, or close")
        self._run_with_reconnect(lambda: self._send_action_locked(action))

    def send_dep_action(self, action: str) -> None:
        """Send a low-level DEP action command."""
        if action not in DEP_ACTION_COMMANDS:
            valid = ", ".join(sorted(DEP_ACTION_COMMANDS))
            raise ValueError(f"action must be one of: {valid}")
        self._run_with_reconnect(lambda: self._send_dep_action_locked(action))

    def write_dmp_register(
        self,
        group: int,
        parameter: int,
        value: int,
        *,
        size: int = 1,
    ) -> None:
        """Write a BusT4/DMP register."""
        if size < 1:
            raise ValueError("size must be at least 1")
        if value < 0:
            raise ValueError("value must be non-negative")
        if value > (1 << (size * 8)) - 1:
            raise ValueError(f"value must fit in {size} byte(s)")
        payload = value.to_bytes(size, "big")
        self._run_with_reconnect(
            lambda: self._write_dmp_register_locked(group, parameter, payload)
        )

    def test_connection(self) -> NiceBidiStatus:
        """Authenticate and read status once."""
        try:
            return self.read_status()
        except NiceBidiConnectionError as err:
            if nice_bidi_error_code(err) != "14":
                raise
            info = self.read_info()
            if device_info_supports_nhk_status(info, self.device_id):
                try:
                    return self.read_nhk_status()
                except NiceBidiError:
                    _LOGGER.debug(
                        "Nice NHK status validation failed after DMP Code 14",
                        exc_info=True,
                    )
            return NiceBidiStatus(
                state=None,
                position=None,
                current_position=None,
                closed_position=None,
                open_position=None,
                registers={},
            )

    def _run_with_reconnect(self, operation: Callable[[], Any]) -> Any:
        last_error: Exception | None = None
        with self._lock:
            for attempt in range(2):
                try:
                    self._ensure_connected_locked()
                    return operation()
                except (OSError, ssl.SSLError, NiceBidiConnectionError) as exc:
                    last_error = exc
                    self._close_locked()
                    if attempt == 0:
                        _LOGGER.debug(
                            "Nice local operation failed; reconnecting once: %s",
                            exc.__class__.__name__,
                        )
                        self._reconnect_count += 1
                        time.sleep(1.0)
            assert last_error is not None
            raise NiceBidiConnectionError(str(last_error)) from last_error

    def _open_locked(self) -> None:
        last_error: Exception | None = None
        for attempt in range(3):
            try:
                _LOGGER.debug(
                    "Opening Nice local TLS connection to %s:%s (attempt %s/3)",
                    self.host,
                    self.port,
                    attempt + 1,
                )
                self._set_transport(
                    LanTlsTransport.connect(self.host, self.port, self.timeout)
                )
                _LOGGER.debug(
                    "Nice local TLS connection established to %s:%s",
                    self.host,
                    self.port,
                )
                return
            except (OSError, ssl.SSLError) as exc:
                last_error = exc
                if attempt < 2:
                    _LOGGER.debug(
                        "Nice local TLS connection attempt %s failed: %s: %s",
                        attempt + 1,
                        exc.__class__.__name__,
                        exc,
                    )
                    time.sleep(0.75 * (attempt + 1))
        assert last_error is not None
        raise NiceBidiConnectionError(str(last_error)) from last_error

    def _connect_locked(self) -> None:
        client_challenge = f"{secrets.randbits(32):08X}"
        xml = (
            self._start_request(0, "CONNECT")
            + f'<Authentication cc="{client_challenge}" username="{_xml_escape(self.credentials.username)}"/>\r\n'
            + "</Request>\r\n"
        )
        response = self._send_locked(xml, expected_type="CONNECT", expected_id=0)
        text = _printable(response)
        server_challenge = _attr(text, "sc")
        auth_match = re.search(r"<Authentication\b([^>]*)>", text)
        session_id = _attr(auth_match.group(1), "id") if auth_match else None
        if "<Error>" in text:
            raise NiceBidiAuthError(_response_error_summary(response))
        if not server_challenge or session_id is None:
            raise NiceBidiConnectionError(
                "CONNECT response was empty or missing session data"
            )

        self._session_id = int(session_id)
        self._sequence = 1
        self._session_key = _sha256(
            self.credentials.password,
            _reverse_hex(server_challenge),
            _reverse_hex(client_challenge),
        )
        _LOGGER.debug(
            "Nice local NHK authentication succeeded with session id %s",
            self._session_id,
        )

    def _ensure_connected_locked(self) -> None:
        if self._socket and self._session_key is not None:
            return
        self._close_locked()
        self._open_locked()
        self._connect_locked()

    def _close_locked(self) -> None:
        transport = self._transport
        dispatcher = self._dispatcher
        self._transport = None
        self._dispatcher = None
        if dispatcher is not None:
            try:
                dispatcher.close()
            except OSError:
                _LOGGER.debug("Nice dispatcher close failed", exc_info=True)
        elif transport is not None:
            try:
                transport.close()
            except OSError:
                _LOGGER.debug("Nice transport close failed", exc_info=True)
        self._session_key = None
        self._session_id = 0
        self._sequence = 1

    def _start_request(self, request_id: int, request_type: str) -> str:
        return (
            f'<Request gw="gwID" id="{request_id}" protocolType="NHK" '
            f'protocolVersion="1.0" source="{_xml_escape(self.credentials.source)}" '
            f'target="{_xml_escape(self.credentials.target_mac)}" type="{request_type}">\r\n'
        )

    def _next_request_id(self) -> int:
        request_id = (self._sequence << 8) | (self._session_id & 0xFF)
        self._sequence += 1
        return request_id

    def _sign(self, xml_command: str) -> str:
        if self._session_key is None:
            raise NiceBidiConnectionError("not authenticated")
        return base64.b64encode(
            _sha256(_sha256(xml_command.encode("utf-8")), self._session_key)
        ).decode("ascii")

    def _signed_request_with_id(
        self, request_type: str, body: str = ""
    ) -> tuple[str, int]:
        request_id = self._next_request_id()
        xml_command = self._start_request(request_id, request_type) + body
        return (
            xml_command + f"<Sign>{self._sign(xml_command)}</Sign>\r\n</Request>\r\n",
            request_id,
        )

    def _signed_exchange_locked(self, request_type: str, body: str = "") -> bytes:
        xml, request_id = self._signed_request_with_id(request_type, body)
        return self._send_locked(
            xml, expected_type=request_type, expected_id=request_id
        )

    def _signed_exchange_frames_locked(
        self,
        request_type: str,
        body: str = "",
        *,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        xml, request_id = self._signed_request_with_id(request_type, body)
        return self._send_frames_locked(
            xml,
            expected_type=request_type,
            expected_id=request_id,
            post_response_listen_seconds=post_response_listen_seconds,
        )

    def _send_locked(
        self, xml: str, expected_type: str | None, expected_id: int | None
    ) -> bytes:
        frames = self._send_frames_locked(
            xml, expected_type=expected_type, expected_id=expected_id
        )
        for response in frames:
            if self._matches_response(response, expected_type, expected_id):
                return response
        raise NiceBidiConnectionError("device did not respond")

    def _send_frames_locked(
        self,
        xml: str,
        expected_type: str | None,
        expected_id: int | None,
        *,
        post_response_listen_seconds: float = 0.0,
    ) -> list[bytes]:
        dispatcher = self._dispatcher
        if dispatcher is None:
            raise NiceBidiConnectionError("socket is not open")
        _LOGGER.debug(
            "Sending Nice local request type=%s id=%s to %s:%s",
            expected_type or "unknown",
            expected_id if expected_id is not None else "unknown",
            self.host,
            self.port,
        )
        frames = dispatcher.exchange(
            _frame(xml),
            expected_type=expected_type,
            expected_id=expected_id,
            timeout=self.timeout,
            post_response_listen_seconds=post_response_listen_seconds,
        )
        if frames:
            _LOGGER.debug(
                "Returning Nice local exchange with %s frame(s), last=%s",
                len(frames),
                _response_summary(frames[-1]),
            )
            return frames
        return []

    def _matches_response(
        self, response: bytes, expected_type: str | None, expected_id: int | None
    ) -> bool:
        return response_matches(response, expected_type, expected_id)

    def _send_action_locked(self, action: str) -> None:
        body = (
            "<Devices>\r\n"
            f'<Device id="{self.device_id}">\r\n'
            "<Services>\r\n"
            f"<DoorAction>{action}</DoorAction>\r\n"
            "</Services>\r\n"
            "</Device>\r\n"
            "</Devices>\r\n"
        )
        response = self._signed_exchange_locked("CHANGE", body)
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_response_error_summary(response))

    def _send_dep_action_locked(self, action: str) -> None:
        response, _ = self._t4_request_locked(
            "DEP",
            build_dep_action_frame(DEP_ACTION_COMMANDS[action]),
            0x00,
            0x03,
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_response_error_summary(response))

    def _write_dmp_register_locked(
        self,
        group: int,
        parameter: int,
        value: bytes,
    ) -> None:
        response, _ = self._t4_request_locked(
            "DMP",
            build_dmp_write_frame(
                *CONTROLLER_TARGET.as_tuple(),
                group,
                parameter,
                value,
            ),
            *CONTROLLER_TARGET.as_tuple(),
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            raise NiceBidiConnectionError(_response_error_summary(response))

    def _read_dmp_register_locked(
        self,
        registers: dict[str, DmpResponse],
        daddr: int,
        dendpoint: int,
        group: int,
        parameter: int,
        *,
        required: bool,
    ) -> None:
        response, plains = self._t4_request_locked(
            "DMP",
            build_dmp_read_frame(daddr, dendpoint, group, parameter),
            daddr,
            dendpoint,
            self.t4_timeout_ms,
        )
        if "<Error>" in _printable(response):
            if required:
                raise NiceBidiConnectionError(_response_error_summary(response))
            _LOGGER.debug(
                "Optional Nice DMP register read failed daddr=%02X dendpoint=%02X register=%02X/%02X: %s",
                daddr,
                dendpoint,
                group,
                parameter,
                _response_summary(response),
            )
            return
        for plain in plains:
            parsed = decode_dmp_response(plain)
            parsed_group = parsed.group
            parsed_parameter = parsed.parameter
            if parsed_group is None or parsed_parameter is None:
                continue
            key = f"{parsed_group:02X}/{parsed_parameter:02X}"
            if (daddr, dendpoint) != CONTROLLER_TARGET.as_tuple():
                key = f"{key}@{daddr:02X}.{dendpoint:02X}"
            registers[key] = parsed

    def _read_status_locked(self, *, include_extended: bool = False) -> NiceBidiStatus:
        registers: dict[str, DmpResponse] = {}
        profiles = [CORE_STATUS_PROFILE]
        if include_extended:
            profiles.extend((EXTENDED_CONTROLLER_PROFILE, OXI_INFO_PROFILE))
        for profile in profiles:
            for group, parameter in profile.registers:
                self._read_dmp_register_locked(
                    registers,
                    *profile.target.as_tuple(),
                    group,
                    parameter,
                    required=profile.required,
                )
        return status_from_dmp_registers(registers)

    def _read_nhk_status_locked(self) -> NiceBidiStatus:
        frames = self._signed_exchange_frames_locked(
            "STATUS",
            post_response_listen_seconds=NHK_STATUS_POST_RESPONSE_LISTEN_SECONDS,
        )
        for frame in frames:
            if "<Error>" in _printable(frame):
                raise NiceBidiConnectionError(_response_error_summary(frame))
        return _parse_nhk_status_frames(frames, self.device_id)

    def _read_info_xml_locked(self) -> str:
        response = self._signed_exchange_locked("INFO")
        text = _printable(response)
        if "<Error>" in text:
            raise NiceBidiConnectionError(_response_error_summary(response))
        return _xml_payload(response)

    def _read_info_locked(self) -> NiceBidiDeviceInfo:
        return parse_info_xml(self._read_info_xml_locked(), self.device_id)

    def _t4_request_locked(
        self,
        protocol: str,
        plain_payload: bytes,
        daddr: int,
        dendpoint: int,
        tout_ms: int,
    ) -> tuple[bytes, list[bytes]]:
        key = _random_t4_key()
        encrypted = _xor_sha256(plain_payload, key)
        encrypted_b64 = base64.b64encode(encrypted).decode("ascii")
        key_b64 = base64.b64encode(key).decode("ascii")
        body = (
            '<Interface id="1">\r\n'
            f'<Protocol tout="{tout_ms}">{protocol}</Protocol>\r\n'
            f"<DAddress>{daddr:02X}</DAddress>\r\n"
            f"<DEndpoint>{dendpoint:02X}</DEndpoint>\r\n"
            f'<T4 id="1" len="{len(encrypted_b64)}" key="{key_b64}" '
            f'DAddress="{daddr}" DEndpoint="{dendpoint}">{encrypted_b64}</T4>\r\n'
            "</Interface>\r\n"
        )
        response = self._signed_exchange_locked("T4_REQUEST", body)
        plains = self._decrypt_t4_payloads(response)
        _LOGGER.debug(
            "Nice T4 %s request daddr=%02X dendpoint=%02X returned %s payload(s)",
            protocol,
            daddr,
            dendpoint,
            len(plains),
        )
        return response, plains

    def _decrypt_t4_payloads(self, response: bytes) -> list[bytes]:
        return _decrypt_t4_payloads_from_frame(response)

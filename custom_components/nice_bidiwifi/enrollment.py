"""One-shot enrollment transport, isolated from the runtime module session."""

from __future__ import annotations

from dataclasses import dataclass
import secrets
from uuid import uuid4

from .client import NiceBidiClient
from .errors import NiceBidiError, NiceParameterError
from .models.credentials import NiceCredentials
from .protocol.nhk.access import (
    EnrollmentReply,
    parse_enrollment,
    setup_check,
)
from .protocol.nhk.codec import xml_escape
from .transport.lan import LegacyLanTlsTransport


@dataclass(frozen=True, slots=True, repr=False)
class EnrollmentIdentity:
    username: str
    source_id: str
    target_mac: str

    @classmethod
    def new(cls, target_mac):
        return cls("ha_" + uuid4().hex, uuid4().hex, target_mac)


class EnrollmentClient:
    """Never reconnect/replay a PAIR operation after uncertain delivery."""

    def __init__(
        self,
        host,
        port,
        identity: EnrollmentIdentity,
        *,
        legacy_tls=False,
        client_factory=NiceBidiClient,
    ):
        kwargs = (
            {"transport_factory": LegacyLanTlsTransport.connect} if legacy_tls else {}
        )
        self.identity = identity
        self._sequence = 0
        self._client = client_factory(
            host,
            port,
            NiceCredentials(
                identity.username, "00" * 32, identity.target_mac, identity.source_id
            ),
            **kwargs,
        )

    def close(self):
        self._client.close()

    def _exchange(self, kind, body, *, protocol="NHK"):
        if not (
            protocol == "NHK"
            and kind in {"PAIR", "VERIFY"}
            or protocol == "WNC"
            and kind in {"INFO", "SCAN"}
        ):
            raise NiceParameterError("Unresolved enrollment operation")
        client = self._client
        with client._lock:
            if client._transport is None or not client._transport.connected:
                try:
                    client._open_locked()
                except (NiceBidiError, OSError) as error:
                    error.delivery = "not_sent"
                    raise
            self._sequence += 1
            request_id = self._sequence
            header = client._start_request(request_id, kind)
            if protocol == "WNC":
                header = header.replace('protocolType="NHK"', 'protocolType="WNC"', 1)
            xml = header + body + "</Request>\r\n"
            try:
                return client._send_locked(
                    xml, expected_type=kind, expected_id=request_id
                )
            except (NiceBidiError, OSError) as err:
                err.delivery = "unknown"
                raise

    def pair_once(self, *, first_user=False, setup_code=None) -> EnrollmentReply:
        challenge = f"{secrets.randbits(32):08X}"
        check = ""
        if first_user:
            if setup_code is None:
                raise NiceParameterError(
                    "Setup code is required for first-user pairing"
                )
            check = f' check="{setup_check(setup_code, challenge)}"'
        elif setup_code is not None:
            raise NiceParameterError(
                "An access request must not impersonate first-user pairing"
            )
        body = (
            f'<Authentication username="{xml_escape(self.identity.username)}" cc="{challenge}" '
            f'CType="pc" OSType="linux" OSVer="" desc="Home Assistant"{check}/>\r\n'
        )
        return parse_enrollment(
            self._exchange("PAIR", body),
            "PAIR",
            self.identity.username,
            self.identity.source_id,
        )

    def verify(self) -> EnrollmentReply:
        body = f'<User username="{xml_escape(self.identity.username)}"/>\r\n'
        return parse_enrollment(
            self._exchange("VERIFY", body),
            "VERIFY",
            self.identity.username,
            self.identity.source_id,
        )


class ProvisioningClient(EnrollmentClient):
    def read_info(self):
        from .protocol.nhk.provisioning import parse_wnc_info

        return parse_wnc_info(
            self._exchange("INFO", "", protocol="WNC"), self.identity.target_mac
        )

    def scan(self):
        from .protocol.nhk.provisioning import parse_wnc_scan

        return parse_wnc_scan(self._exchange("SCAN", "", protocol="WNC"))

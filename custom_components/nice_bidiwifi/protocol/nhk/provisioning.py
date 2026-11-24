"""WNC namespace and private Wi-Fi DTOs; no implicit reconfiguration."""

from __future__ import annotations

import base64
from dataclasses import dataclass
import re

from ...errors import NiceParameterError, NiceProtocolError, NiceUnsupportedError
from ...models.discovery import normalize_device_id
from .access import parse_response
from .codec import xml_escape


@dataclass(frozen=True, slots=True, repr=False)
class WirelessNetwork:
    ssid: str
    security: str
    channel: int | None
    signal: int | None


@dataclass(frozen=True, slots=True, repr=False)
class ProvisioningInfo:
    model: str | None
    firmware: str | None
    source_matches: bool
    ap_state_verified: bool = False


def _root(frame, kind):
    root = parse_response(frame, kind)
    if root.get("protocolType") not in {None, "WNC"}:
        raise NiceProtocolError("Unexpected provisioning namespace")
    return root


def parse_wnc_info(frame, target):
    root = _root(frame, "INFO")
    interface = root.find("./Interface")
    if interface is None:
        raise NiceProtocolError("No provisioning interface information")
    return ProvisioningInfo(
        (interface.findtext("Type") or interface.findtext("Prod") or "")[:96] or None,
        (interface.findtext("VersionFW") or "")[:32] or None,
        normalize_device_id(target) is not None
        and normalize_device_id(root.get("source")) == normalize_device_id(target),
    )


def parse_wnc_scan(frame):
    root = _root(frame, "SCAN")
    nodes = root.findall("./WLAN")
    if len(nodes) > 64:
        raise NiceProtocolError("Wireless scan exceeds retention limit")
    rows = []
    for node in nodes:
        ssid = node.get("SSID", "")
        if len(ssid.encode()) > 32:
            continue

        def integer(key, low, high, node=node):
            try:
                value = int(node.get(key, ""))
            except ValueError:
                return None
            return value if low <= value <= high else None

        rows.append(
            WirelessNetwork(
                ssid,
                node.get("Security", "unknown")[:32],
                integer("Channel", 1, 196),
                integer("RSSI", -150, 0),
            )
        )
    return tuple(rows)


@dataclass(frozen=True, slots=True, repr=False)
class WifiConfiguration:
    ssid: str
    password: str
    country: str
    security: str = "WPA2"

    def body(self):
        if not 1 <= len(self.ssid.encode()) <= 32 or not re.fullmatch(
            r"[A-Z]{2}", self.country
        ):
            raise NiceParameterError("Invalid Wi-Fi network or country")
        if (
            self.security != "WPA2"
            or not self.password.isascii()
            or not 8 <= len(self.password) <= 63
        ):
            raise NiceParameterError(
                "Only the verified WPA2 passphrase subset is supported"
            )
        ssid64 = base64.b64encode(self.ssid.encode()).decode()
        key64 = base64.b64encode(self.password.encode()).decode()
        return (
            "<Interface><WLAN><SSID>"
            + xml_escape(self.ssid)
            + "</SSID><SSIDb64>"
            + ssid64
            + "</SSIDb64><Sec>WPA2</Sec><Key>"
            + key64
            + "</Key><KeyLen>"
            + str(len(self.password))
            + "</KeyLen><Band>2_4GHZ</Band><Country>"
            + self.country
            + "</Country></WLAN><CONF><Mode>NHK</Mode><Check>true</Check></CONF></Interface>\r\n"
        )


class ProvisioningMutationAdapter:
    gap = "setup.wnc_recovery"

    def configure(self, request: WifiConfiguration):
        # No transport, credential collection or cancellation happens before this
        # boundary. Wire fields alone do not establish AP state and rediscovery.
        raise NiceUnsupportedError(
            "Wi-Fi mutation is blocked until AP-state, identity and recovery bindings are verified"
        )

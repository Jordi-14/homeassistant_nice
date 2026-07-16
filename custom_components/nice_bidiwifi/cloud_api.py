"""Async client for the Nice/MyNice cloud and NHK proxy socket."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import logging
import random
import re
import ssl
import time
from typing import Any, Callable

import aiohttp

BASE_URL = "https://integration.niceappdomain.com/myNiceCloud/"
CLIENT_ID = "android-client-id"
CLIENT_SECRET = "android-client-id_21"

COMMON_HEADERS = {
    "OS": "Android",
    "OSVersion": "13",
    "DeviceModel": "Google Pixel pixel",
    "Accept-Language": "en",
    "Accept-Encoding": "gzip, deflate",
}

PROXY_HOST = "integration.niceappdomain.com"
PROXY_PORT = 7890
STX = 0x02
ETX = 0x03

_LOGGER = logging.getLogger(__name__)


class NiceAuthError(Exception):
    """Authentication failed."""


class NiceApiError(Exception):
    """A cloud or device request failed."""


def _sha256(*parts: bytes) -> bytes:
    h = hashlib.sha256()
    for p in parts:
        h.update(p)
    return h.digest()


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


def _attr(xml: str, name: str) -> str | None:
    m = re.search(name + r'\s*=\s*"([^"]*)"', xml or "")
    return m.group(1) if m else None


def _tag(xml: str, name: str) -> str | None:
    m = re.search(r"<" + name + r">([^<]*)</" + name + r">", xml or "")
    return m.group(1) if m else None


def parse_door_statuses(xml: str) -> dict[str, str]:
    """Return {device_id: DoorStatus} for every Device block in a frame."""
    out: dict[str, str] = {}
    for dev_id, block in re.findall(r'<Device id="([^"]+)">(.*?)</Device>', xml or "", re.DOTALL):
        ds = _tag(block, "DoorStatus")
        if ds:
            out[dev_id] = ds
    if not out:
        ds = _tag(xml, "DoorStatus")
        if ds:
            out["1"] = ds
    return out


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


class NiceCloud:
    """OAuth2 and discovery against the MyNice cloud."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        *,
        username: str | None = None,
        password: str | None = None,
        token: dict | None = None,
        on_token: Callable[[dict], None] | None = None,
    ) -> None:
        self._session = session
        self.username = username
        self.password = password
        self._token = dict(token) if token else None
        self._on_token = on_token

    @property
    def token(self) -> dict | None:
        return self._token

    def _basic(self) -> str:
        return "Basic " + _b64(f"{CLIENT_ID}:{CLIENT_SECRET}".encode())

    def _store_token(self, tok: dict) -> None:
        tok = dict(tok)
        tok["_expires_at"] = time.time() + int(tok.get("expires_in", 3600))
        self._token = tok
        if self._on_token:
            self._on_token(tok)

    async def _token_request(self, params: dict[str, str]) -> dict:
        try:
            async with self._session.post(
                BASE_URL + "oauth/token",
                params=params,
                headers={
                    **COMMON_HEADERS,
                    "Authorization": self._basic(),
                    "Content-Type": "application/json",
                },
                timeout=aiohttp.ClientTimeout(total=30),
            ) as r:
                body = await r.text()
                if r.status != 200:
                    raise NiceAuthError(f"token request failed ({r.status}): {body[:200]}")
                import json

                return json.loads(body)
        except aiohttp.ClientError as err:
            raise NiceApiError(f"network error during auth: {err}") from err

    async def async_login(self) -> None:
        """Full password grant."""
        if not self.username or not self.password:
            raise NiceAuthError("no credentials available for password login")
        _LOGGER.debug("Nice: password login")
        tok = await self._token_request(
            {
                "grant_type": "password",
                "username": self.username,
                "password": self.password,
            }
        )
        if not tok.get("access_token"):
            raise NiceAuthError("login response missing access_token")
        self._store_token(tok)

    async def async_refresh(self) -> bool:
        if not self._token or not self._token.get("refresh_token"):
            return False
        _LOGGER.debug("Nice: refreshing token")
        try:
            tok = await self._token_request(
                {
                    "grant_type": "refresh_token",
                    "refresh_token": self._token["refresh_token"],
                }
            )
        except NiceAuthError:
            return False
        if not tok.get("access_token"):
            return False
        tok.setdefault("refresh_token", self._token.get("refresh_token"))
        self._store_token(tok)
        return True

    async def async_ensure_token(self) -> None:
        """Authenticate only when there is no valid cached token."""
        t = self._token
        if t and t.get("access_token") and t.get("_expires_at", 0) > time.time() + 60:
            return
        if await self.async_refresh():
            return
        await self.async_login()

    async def _auth_headers(self) -> dict[str, str]:
        await self.async_ensure_token()
        tt = self._token.get("token_type", "Bearer")
        return {
            **COMMON_HEADERS,
            "Authorization": f"{tt} {self._token['access_token']}",
            "Content-Type": "application/json",
        }

    async def _request(self, method: str, path: str, **kw: Any) -> Any:
        for attempt in (1, 2):
            headers = await self._auth_headers()
            try:
                async with self._session.request(
                    method,
                    BASE_URL + path,
                    headers=headers,
                    timeout=aiohttp.ClientTimeout(total=30),
                    **kw,
                ) as r:
                    if r.status == 401 and attempt == 1:
                        self._token = None
                        continue
                    if r.status != 200:
                        text = await r.text()
                        raise NiceApiError(f"{path} failed ({r.status}): {text[:200]}")
                    return await r.json()
            except aiohttp.ClientError as err:
                raise NiceApiError(f"network error on {path}: {err}") from err
        raise NiceApiError(f"{path} failed after re-auth")

    async def async_discover(self) -> list[dict]:
        """Return door dicts including per-accessory socket credentials."""
        me = (await self._request("GET", "api/v1/users/data-act/me"))["data"]
        sd = await self._request("POST", "api/v1/user/smartdeviceList")

        creds: dict[str, dict] = {}
        for dev in sd.get("smartDevices", []):
            for ac in dev.get("accessoryCredentials", []) or []:
                creds[ac["accessoryMacAddress"]] = {
                    "user": ac["accessoryUser"],
                    "password": ac["accessoryPassword"],
                    "controller": ac["controllerID"],
                }

        doors: list[dict] = []
        for home in me.get("homes", []):
            for amb in home.get("ambients", []):
                for a in amb.get("automations", []):
                    mac = a.get("accessoryMacAddress")
                    doors.append(
                        {
                            "automation_id": a.get("id"),
                            "device_id": str(a.get("deviceId") or "1"),
                            "name": a.get("automationName"),
                            "type": a.get("automationType"),
                            "model": a.get("automationModel"),
                            "mac": mac,
                            "online": a.get("statusAutomationNetwork"),
                            "creds": creds.get(mac),
                        }
                    )
        return doors


class NhkSession:
    """Per-accessory NHK session state and message building."""

    def __init__(self, mac: str, user: str, password: str, controller: str) -> None:
        self.mac = mac
        self.user = user
        self.password = password
        self.controller = controller
        self.session_id: int | None = None
        self.session_pw: bytes | None = None
        self._counter = 1

    def next_id(self, with_session: bool = True) -> int:
        c = self._counter
        self._counter += 1
        if with_session and self.session_id is not None:
            return (self.session_id & 0xFF) | (c << 8)
        return c

    def frame(self, xml: str, signed: bool) -> bytes:
        xml = xml.replace("\n", "\r\n")
        if signed:
            i = xml.index("<Sign>")
            prefix = xml[:i]
            sign = _b64(_sha256(_sha256(prefix.encode()), self.session_pw))
            xml = prefix + "<Sign>" + sign + "</Sign>" + xml[i + len("<Sign></Sign>") :]
        return bytes([STX]) + xml.encode() + bytes([ETX])

    def header(self, rtype: str, with_session: bool = True) -> str:
        return (
            f'<Request gw="" id="{self.next_id(with_session)}" '
            f'protocolType="NHK" protocolVersion="1.0" '
            f'source="{self.controller}" target="{self.mac}" type="{rtype}">'
        )

    def build_connect(self) -> tuple[str, bytes]:
        """Return (cc, frame) for the unsigned CONNECT handshake."""
        cc = f"{random.getrandbits(32):08X}"
        xml = (
            self.header("CONNECT", with_session=False)
            + f'\n   <Authentication cc="{cc}" username="{self.user}"/>\n</Request>'
        )
        return cc, self.frame(xml, signed=False)

    def derive(self, sc: str, cc: str, session_id: int) -> None:
        """Derive the session key from the CONNECT challenge response."""
        self.session_id = session_id
        self.session_pw = _sha256(
            bytes.fromhex(self.password),
            bytes.fromhex(sc)[::-1],
            bytes.fromhex(cc)[::-1],
        )

    def build_status(self) -> bytes:
        xml = self.header("STATUS") + "\n   <Sign></Sign>\n</Request>"
        return self.frame(xml, signed=True)

    def build_change(self, action: str, device_id: str = "1") -> bytes:
        if action not in ("open", "close", "stop"):
            raise ValueError(action)
        device_id = str(device_id or "1")
        body = (
            f'\n   <Devices>\n      <Devices>\n         <Device id="{device_id}">\n'
            "            <Services>\n               <DoorAction>"
            + action
            + "</DoorAction>\n            </Services>\n         </Device>\n"
            "      </Devices>\n   </Devices>"
        )
        xml = self.header("CHANGE") + body + "\n   <Sign></Sign>\n</Request>"
        return self.frame(xml, signed=True)


class NhkClient:
    """One TLS socket to the device proxy, shared by all accessory sessions."""

    def __init__(self) -> None:
        self._reader: asyncio.StreamReader | None = None
        self._writer: asyncio.StreamWriter | None = None
        self._buf = bytearray()
        self._wlock = asyncio.Lock()
        self.sessions: dict[str, NhkSession] = {}

    @property
    def connected(self) -> bool:
        return self._writer is not None

    def has_session(self, mac: str) -> bool:
        return mac in self.sessions

    async def open(self) -> None:
        """Open the shared TLS socket."""
        self._reader, self._writer = await asyncio.wait_for(
            asyncio.open_connection(PROXY_HOST, PROXY_PORT, ssl=_ssl_context()),
            timeout=15,
        )
        self._buf.clear()
        self.sessions.clear()

    async def close(self) -> None:
        w = self._writer
        self._reader = self._writer = None
        self.sessions.clear()
        if w is not None:
            try:
                w.close()
                await w.wait_closed()
            except (OSError, asyncio.CancelledError):
                pass

    async def read_frame(self, timeout: float = 10.0) -> str | None:
        """Read one STX..ETX frame from the shared stream, or None on timeout."""
        if self._reader is None:
            raise NiceApiError("socket not open")
        try:
            while True:
                if STX in self._buf:
                    start = self._buf.index(STX)
                    end = self._buf.find(ETX, start)
                    if end != -1:
                        frame = bytes(self._buf[start + 1 : end])
                        del self._buf[: end + 1]
                        return frame.decode("utf-8", "replace")
                    if start > 0:
                        del self._buf[:start]
                chunk = await asyncio.wait_for(self._reader.read(4096), timeout)
                if not chunk:
                    raise NiceApiError("device socket closed")
                self._buf.extend(chunk)
        except TimeoutError:
            return None

    async def _write(self, data: bytes) -> None:
        if self._writer is None:
            raise NiceApiError("socket not open")
        async with self._wlock:
            self._writer.write(data)
            await self._writer.drain()

    async def add_session(self, mac: str, user: str, password: str, controller: str) -> NhkSession:
        """CONNECT a new accessory session over the shared socket."""
        sess = NhkSession(mac, user, password, controller)
        cc, frame = sess.build_connect()
        await self._write(frame)
        for _ in range(8):
            resp = await self.read_frame(15)
            if resp is None:
                raise NiceApiError(f"NHK CONNECT timeout for {mac}")
            sc = _attr(resp, "sc")
            if sc is not None:
                sess.derive(sc, cc, int(_attr(resp, "id")))
                self.sessions[mac] = sess
                _LOGGER.debug("NHK session up for %s (id %s)", mac, sess.session_id)
                return sess
        raise NiceApiError(f"no NHK CONNECT reply for {mac}")

    def route(self, frame: str) -> str | None:
        """Return the MAC of the session this frame belongs to, or None."""
        for mac in self.sessions:
            if mac in frame:
                return mac
        return None

    async def send_status(self, mac: str) -> None:
        sess = self.sessions.get(mac)
        if sess is None:
            raise NiceApiError(f"no session for {mac}")
        await self._write(sess.build_status())

    async def send_change(self, mac: str, action: str, device_id: str = "1") -> None:
        sess = self.sessions.get(mac)
        if sess is None:
            raise NiceApiError(f"no session for {mac}")
        await self._write(sess.build_change(action, device_id))

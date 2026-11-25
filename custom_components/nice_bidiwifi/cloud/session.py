"""Cookie-free, transient account HTTP sessions using HA's verified connector."""

from contextlib import contextmanager

import aiohttp
from homeassistant.helpers.aiohttp_client import async_create_clientsession


@contextmanager
def ephemeral_cloud_session(hass):
    session = async_create_clientsession(
        hass, auto_cleanup=False, cookie_jar=aiohttp.DummyCookieJar()
    )
    try:
        yield session
    finally:
        # HA owns the shared TLS connector. Detach this short-lived session,
        # rather than closing a connector also used by other integrations.
        session.detach()

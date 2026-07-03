"""Constants for the Nice integration."""

from datetime import timedelta

from homeassistant.const import CONF_HOST, CONF_NAME, CONF_PASSWORD, CONF_PORT, CONF_USERNAME

DOMAIN = "nice_bidiwifi"

CONF_DEVICE_ID = "device_id"
CONF_SOURCE_ID = "source_id"
CONF_TARGET_MAC = "target_mac"
CONF_T4_TIMEOUT_MS = "t4_timeout_ms"

DEFAULT_NAME = "Nice Gate"
DEFAULT_PORT = 443
DEFAULT_DEVICE_ID = 1
DEFAULT_T4_TIMEOUT_MS = 200
DEFAULT_TIMEOUT = 10.0

IDLE_UPDATE_INTERVAL = timedelta(seconds=30)
MOVING_UPDATE_INTERVAL = timedelta(seconds=2)
ERROR_UPDATE_INTERVAL = timedelta(seconds=60)

CONFIG_FIELDS = {
    CONF_NAME,
    CONF_HOST,
    CONF_PORT,
    CONF_USERNAME,
    CONF_PASSWORD,
    CONF_SOURCE_ID,
    CONF_TARGET_MAC,
    CONF_DEVICE_ID,
    CONF_T4_TIMEOUT_MS,
}

"""Constants for the HA Desktop Widget integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_desktop_widget"
CONFIG_ENTRY_UNIQUE_ID = "ha_desktop_widget_coordinator"

INTEGRATION_NAME = "HA Desktop Widget"
MANUFACTURER = "HA Desktop Widget"

PROTOCOL_VERSION = 1
STORE_VERSION = 1
STORE_KEY_PREFIX = DOMAIN

DATA_RUNTIMES = "runtimes"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.SENSOR,
    Platform.SWITCH,
)

CAPABILITY_VISIBILITY = "visibility"
CAPABILITY_SWITCH_PAGE = "switch_page"
SUPPORTED_CAPABILITIES = frozenset(
    {
        CAPABILITY_VISIBILITY,
        CAPABILITY_SWITCH_PAGE,
    }
)

COMMAND_SHOW = "show"
COMMAND_HIDE = "hide"
COMMAND_TOGGLE = "toggle"
COMMAND_SWITCH_PAGE = "switch_page"

SERVICE_SHOW = "show"
SERVICE_HIDE = "hide"
SERVICE_TOGGLE = "toggle"
SERVICE_SWITCH_PAGE = "switch_page"

ATTR_PAGE_ID = "page_id"

COMMAND_TIMEOUT_SECONDS = 10
COMMAND_EXPIRY_SECONDS = 15
PERSIST_DEBOUNCE_SECONDS = 5

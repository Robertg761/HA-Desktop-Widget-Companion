"""Constants for the HA Desktop Widget integration."""

from __future__ import annotations

from homeassistant.const import Platform

DOMAIN = "ha_desktop_widget"
CONFIG_ENTRY_UNIQUE_ID = "ha_desktop_widget_coordinator"

INTEGRATION_NAME = "HA Desktop Widget"
MANUFACTURER = "HA Desktop Widget"

PROTOCOL_VERSION = 1
STORE_VERSION = 1
PROFILE_STORE_VERSION = 1
STORE_KEY_PREFIX = DOMAIN

DATA_RUNTIMES = "runtimes"

PLATFORMS: tuple[Platform, ...] = (
    Platform.BINARY_SENSOR,
    Platform.SELECT,
    Platform.SENSOR,
    Platform.SWITCH,
)

CAPABILITY_VISIBILITY = "visibility"
CAPABILITY_SWITCH_PAGE = "switch_page"
CAPABILITY_APPLY_PROFILE = "apply_profile"
SUPPORTED_CAPABILITIES = frozenset(
    {
        CAPABILITY_VISIBILITY,
        CAPABILITY_SWITCH_PAGE,
        CAPABILITY_APPLY_PROFILE,
    }
)

COMMAND_SHOW = "show"
COMMAND_HIDE = "hide"
COMMAND_TOGGLE = "toggle"
COMMAND_SWITCH_PAGE = "switch_page"
COMMAND_APPLY_PROFILE = "apply_profile"

SERVICE_SHOW = "show"
SERVICE_HIDE = "hide"
SERVICE_TOGGLE = "toggle"
SERVICE_SWITCH_PAGE = "switch_page"
SERVICE_APPLY_PROFILE = "apply_profile"
SERVICE_UNASSIGN_PROFILE = "unassign_profile"
SERVICE_CAPTURE_PROFILE = "capture_profile"
SERVICE_SAVE_PROFILE = "save_profile"
SERVICE_DELETE_PROFILE = "delete_profile"

ATTR_PAGE_ID = "page_id"
ATTR_PROFILE = "profile"
ATTR_DOCUMENT = "document"

# Profile documents follow the desktop's canonical schema
# (packages/widget-renderer/src/profile-schema.js in HA Desktop Widget). Home Assistant enforces
# only structural bounds; the desktop owns the semantic normalization of each section.
PROFILE_SCHEMA_VERSION = 1
PROFILE_SECTION_KEYS = frozenset(
    {
        "ui",
        "primaryCards",
        "favoriteEntities",
        "customTabs",
        "activeTabId",
        "comparisonGraphs",
        "quickAccessTileOptions",
        "customEntityIcons",
        "customEntityNames",
        "opacity",
        "frostedGlass",
    }
)
MAX_PROFILES = 100
MAX_PROFILE_DOCUMENT_BYTES = 256 * 1024
MAX_PROFILE_DOCUMENT_DEPTH = 12

COMMAND_TIMEOUT_SECONDS = 10
COMMAND_EXPIRY_SECONDS = 15
PERSIST_DEBOUNCE_SECONDS = 5

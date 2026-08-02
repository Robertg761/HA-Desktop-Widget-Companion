"""HA Desktop Widget coordinator integration."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.typing import ConfigType

from .const import DATA_RUNTIMES, DOMAIN, PLATFORMS
from .runtime import HADesktopWidgetRuntime
from .services import async_setup_services
from .websocket_api import async_setup_websocket_api

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Set up global actions and authenticated WebSocket commands."""
    domain_data = hass.data.setdefault(DOMAIN, {})
    domain_data.setdefault(DATA_RUNTIMES, {})
    async_setup_websocket_api(hass)
    async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Set up the singleton coordinator config entry."""
    runtime = HADesktopWidgetRuntime(hass, entry.entry_id)
    await runtime.async_load()
    entry.runtime_data = runtime
    hass.data[DOMAIN][DATA_RUNTIMES][entry.entry_id] = runtime
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    """Unload the coordinator and its entity platforms."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    await runtime.async_shutdown()
    hass.data[DOMAIN][DATA_RUNTIMES].pop(entry.entry_id, None)
    return True


async def async_remove_config_entry_device(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    device_entry: dr.DeviceEntry,
) -> bool:
    """Allow a registered desktop to be removed from its HA device page."""
    runtime: HADesktopWidgetRuntime = config_entry.runtime_data
    for identifier_domain, identifier in device_entry.identifiers:
        if identifier_domain == DOMAIN:
            return await runtime.async_remove_desktop(identifier)
    return False


async def async_migrate_entry(hass: HomeAssistant, config_entry: ConfigEntry) -> bool:
    """Migrate future config-entry versions without YAML intervention."""
    if config_entry.version == 1:
        return True
    return False

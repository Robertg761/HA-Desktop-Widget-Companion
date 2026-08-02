"""Redacted diagnostics for HA Desktop Widget."""

from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant

from .runtime import HADesktopWidgetRuntime


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: ConfigEntry
) -> dict:
    """Return protocol and non-secret desktop state."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    return runtime.diagnostics()

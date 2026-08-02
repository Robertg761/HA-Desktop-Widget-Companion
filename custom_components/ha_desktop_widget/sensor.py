"""Low-churn desktop state sensors."""

from __future__ import annotations

from homeassistant.components.sensor import SensorEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .entity import HADesktopWidgetEntity, async_add_new_entities
from .runtime import HADesktopWidgetRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up optional current-page sensors."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    known_desktop_ids: set[str] = set()

    @callback
    def add_new() -> None:
        async_add_new_entities(runtime, known_desktop_ids, async_add_entities, CurrentPage)

    add_new()
    entry.async_on_unload(runtime.async_add_listener(add_new))


class CurrentPage(HADesktopWidgetEntity, SensorEntity):
    """Expose the current renderer page when explicitly enabled by the user."""

    _attr_entity_registry_enabled_default = False
    _attr_translation_key = "current_page"

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        super().__init__(runtime, desktop_id)
        self._attr_unique_id = f"{desktop_id}_current_page"

    @property
    def native_value(self) -> str | None:
        """Return the current page reported by the desktop."""
        return self.record.current_page if self.record else None

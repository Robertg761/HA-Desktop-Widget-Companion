"""Connectivity entities for HA Desktop Widget clients."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
    """Set up connectivity entities and listen for later registrations."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    known_desktop_ids: set[str] = set()

    @callback
    def add_new() -> None:
        async_add_new_entities(runtime, known_desktop_ids, async_add_entities, DesktopConnected)

    add_new()
    entry.async_on_unload(runtime.async_add_listener(add_new))


class DesktopConnected(HADesktopWidgetEntity, BinarySensorEntity):
    """Represent whether the desktop command subscription is online."""

    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_translation_key = "connected"

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        super().__init__(runtime, desktop_id)
        self._attr_unique_id = f"{desktop_id}_connected"

    @property
    def available(self) -> bool:
        """Stay available so an offline state is visible in Home Assistant."""
        return self.record is not None

    @property
    def is_on(self) -> bool:
        """Return the live session state."""
        return self.runtime.is_online(self.desktop_id)

    @property
    def extra_state_attributes(self) -> dict[str, str | None]:
        """Expose last contact without creating a heartbeat sensor."""
        return {"last_seen_at": self.record.last_seen_at if self.record else None}

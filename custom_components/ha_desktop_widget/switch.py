"""Widget visibility switch entities."""

from __future__ import annotations

from homeassistant.components.switch import SwitchEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import (
    CAPABILITY_VISIBILITY,
    COMMAND_HIDE,
    COMMAND_SHOW,
)
from .entity import HADesktopWidgetEntity, async_add_new_entities
from .runtime import HADesktopWidgetRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up visibility switches and listen for later registrations."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    known_desktop_ids: set[str] = set()

    @callback
    def add_new() -> None:
        async_add_new_entities(runtime, known_desktop_ids, async_add_entities, WidgetVisibility)

    add_new()
    entry.async_on_unload(runtime.async_add_listener(add_new))


class WidgetVisibility(HADesktopWidgetEntity, SwitchEntity):
    """Show or hide the native desktop renderer."""

    _attr_translation_key = "widget"

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        super().__init__(runtime, desktop_id)
        self._attr_unique_id = f"{desktop_id}_widget"

    @property
    def available(self) -> bool:
        return super().available and self.runtime.supports(
            self.desktop_id, CAPABILITY_VISIBILITY
        )

    @property
    def is_on(self) -> bool | None:
        """Return the renderer's reported visibility."""
        return self.record.visible if self.record else None

    async def async_turn_on(self, **kwargs) -> None:
        """Show the desktop widget and wait for acknowledgement."""
        await self.runtime.async_dispatch_command(self.desktop_id, COMMAND_SHOW)

    async def async_turn_off(self, **kwargs) -> None:
        """Hide the desktop widget and wait for acknowledgement."""
        await self.runtime.async_dispatch_command(self.desktop_id, COMMAND_HIDE)

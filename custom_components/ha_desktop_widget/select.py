"""Profile assignment select entities."""

from __future__ import annotations

from homeassistant.components.select import SelectEntity
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import CAPABILITY_APPLY_PROFILE
from .entity import HADesktopWidgetEntity, async_add_new_entities
from .runtime import HADesktopWidgetRuntime


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up profile selects and listen for later registrations."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    known_desktop_ids: set[str] = set()

    @callback
    def add_new() -> None:
        async_add_new_entities(runtime, known_desktop_ids, async_add_entities, DesktopProfile)

    add_new()
    entry.async_on_unload(runtime.async_add_listener(add_new))


class DesktopProfile(HADesktopWidgetEntity, SelectEntity):
    """Choose the profile a desktop is assigned to and keep it applied."""

    _attr_translation_key = "profile"

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        super().__init__(runtime, desktop_id)
        self._attr_unique_id = f"{desktop_id}_profile"

    @property
    def available(self) -> bool:
        """Assignments are stored in Home Assistant, so they can change while offline."""
        return (
            self.record is not None
            and self.runtime.supports(self.desktop_id, CAPABILITY_APPLY_PROFILE)
            and bool(self.runtime.profiles)
        )

    @property
    def options(self) -> list[str]:
        """Return the stored profile names."""
        return sorted(
            (profile.name for profile in self.runtime.profiles.values()), key=str.casefold
        )

    @property
    def current_option(self) -> str | None:
        """Return the assigned profile's name."""
        profile = self.runtime.assigned_profile(self.desktop_id)
        return profile.name if profile else None

    async def async_select_option(self, option: str) -> None:
        """Assign the profile and apply it now when the desktop is online."""
        profile = self.runtime.require_profile(option)
        await self.runtime.async_assign_profile(self.desktop_id, profile.profile_id)
        if self.runtime.is_online(self.desktop_id):
            await self.runtime.async_sync_profile(self.desktop_id, force=True)

"""Connectivity entities for HA Desktop Widget clients."""

from __future__ import annotations

from homeassistant.components.binary_sensor import BinarySensorDeviceClass, BinarySensorEntity
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
    """Set up connectivity entities and listen for later registrations."""
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    known_desktop_ids: set[str] = set()

    @callback
    def add_new() -> None:
        async_add_new_entities(runtime, known_desktop_ids, async_add_entities, _desktop_entities)

    add_new()
    entry.async_on_unload(runtime.async_add_listener(add_new))


def _desktop_entities(
    runtime: HADesktopWidgetRuntime, desktop_id: str
) -> list[BinarySensorEntity]:
    return [DesktopConnected(runtime, desktop_id), ProfileOutOfDate(runtime, desktop_id)]


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


class ProfileOutOfDate(HADesktopWidgetEntity, BinarySensorEntity):
    """Report when a desktop has not applied the current revision of its assigned profile."""

    _attr_device_class = BinarySensorDeviceClass.UPDATE
    _attr_translation_key = "profile_update"

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        super().__init__(runtime, desktop_id)
        self._attr_unique_id = f"{desktop_id}_profile_update"

    @property
    def available(self) -> bool:
        """Drift is known from stored state, so it stays visible while offline."""
        return self.record is not None and self.runtime.supports(
            self.desktop_id, CAPABILITY_APPLY_PROFILE
        )

    @property
    def is_on(self) -> bool:
        """Return whether the desktop is behind its assigned profile."""
        return self.runtime.profile_out_of_date(self.desktop_id)

    @property
    def extra_state_attributes(self) -> dict[str, str | int | None]:
        """Describe the assigned and applied profile revisions."""
        record = self.record
        profile = self.runtime.assigned_profile(self.desktop_id)
        return {
            "assigned_profile": profile.name if profile else None,
            "assigned_revision": profile.revision if profile else None,
            "applied_profile_id": record.active_profile_id if record else None,
            "applied_revision": record.profile_revision if record else None,
        }

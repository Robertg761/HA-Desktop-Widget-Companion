"""Shared entity base for registered desktop clients."""

from __future__ import annotations

from homeassistant.core import callback
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from .const import DOMAIN, MANUFACTURER
from .runtime import HADesktopWidgetRuntime


class HADesktopWidgetEntity(Entity):
    """Base entity backed only by coordinator memory."""

    _attr_has_entity_name = True

    def __init__(self, runtime: HADesktopWidgetRuntime, desktop_id: str) -> None:
        self.runtime = runtime
        self.desktop_id = desktop_id

    @property
    def record(self):
        """Return the current desktop record."""
        return self.runtime.get_desktop(self.desktop_id)

    @property
    def device_info(self) -> DeviceInfo:
        """Describe the persistent Home Assistant device."""
        record = self.record
        return DeviceInfo(
            identifiers={(DOMAIN, self.desktop_id)},
            name=record.name if record else "Desktop Widget",
            manufacturer=MANUFACTURER,
            model=(record.platform.title() if record else "Desktop"),
            sw_version=record.app_version if record else None,
        )

    @property
    def available(self) -> bool:
        """Control and state entities are unavailable while the desktop is offline."""
        return self.record is not None and self.runtime.is_online(self.desktop_id)

    async def async_added_to_hass(self) -> None:
        """Write state whenever the coordinator changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self.runtime.async_add_listener(self._handle_runtime_update))

    @callback
    def _handle_runtime_update(self) -> None:
        if self.hass is not None:
            self.async_write_ha_state()


def async_add_new_entities(
    runtime: HADesktopWidgetRuntime,
    known_desktop_ids: set[str],
    async_add_entities,
    entity_factory,
) -> None:
    """Add entities for newly registered desktops without duplicating existing ones."""
    new_ids = set(runtime.desktops) - known_desktop_ids
    if not new_ids:
        return
    known_desktop_ids.update(new_ids)
    async_add_entities([entity_factory(runtime, desktop_id) for desktop_id in sorted(new_ids)])

"""Tests for diagnostics redaction."""

from homeassistant.core import HomeAssistant
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.diagnostics import (
    async_get_config_entry_diagnostics,
)
from custom_components.ha_desktop_widget.runtime import HADesktopWidgetRuntime


async def test_diagnostics_contain_no_credentials(hass: HomeAssistant) -> None:
    """Diagnostics expose useful state but no authentication material."""
    runtime = HADesktopWidgetRuntime(hass, "test-entry")
    await runtime.async_register_desktop(
        {
            "desktop_id": "desktop-12345678",
            "name": "Office",
            "protocol_version": 1,
            "capabilities": ["visibility"],
            "access_token": "must-be-ignored",
        },
        user_id="user-1",
        is_admin=False,
    )

    diagnostics = runtime.diagnostics()
    serialized = repr(diagnostics).lower()
    assert "must-be-ignored" not in serialized
    assert "access_token" not in serialized
    assert diagnostics["registered_desktops"] == 1

    entry = MockConfigEntry(domain="ha_desktop_widget", data={})
    entry.runtime_data = runtime
    assert (await async_get_config_entry_diagnostics(hass, entry)) == diagnostics

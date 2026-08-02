"""Integration tests for entities, actions, and unloading."""

from __future__ import annotations

import asyncio
from typing import Any

from homeassistant.const import ATTR_DEVICE_ID, STATE_OFF, STATE_ON, STATE_UNAVAILABLE
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.const import (
    CONFIG_ENTRY_UNIQUE_ID,
    DOMAIN,
    SERVICE_SHOW,
)
from custom_components.ha_desktop_widget.runtime import HADesktopWidgetRuntime

DESKTOP_ID = "desktop-12345678"


class FakeConnection:
    """Capture remote command events."""

    def __init__(self) -> None:
        self.events: list[tuple[int, dict[str, Any]]] = []

    def send_event(self, subscription_id: int, event: dict[str, Any]) -> None:
        self.events.append((subscription_id, event))


async def _setup_entry(hass: HomeAssistant) -> MockConfigEntry:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HA Desktop Widget",
        data={},
        unique_id=CONFIG_ENTRY_UNIQUE_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()
    return entry


async def test_dynamic_device_entities_and_show_action(hass: HomeAssistant) -> None:
    """A registration becomes a native HA device whose action waits for acknowledgement."""
    entry = await _setup_entry(hass)
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    await runtime.async_register_desktop(
        {
            "desktop_id": DESKTOP_ID,
            "name": "Office",
            "platform": "linux",
            "architecture": "x64",
            "app_version": "3.9.0",
            "protocol_version": 1,
            "capabilities": ["visibility", "switch_page"],
        },
        user_id="user-1",
        is_admin=False,
    )
    await hass.async_block_till_done()

    entity_registry = er.async_get(hass)
    connected_entity_id = entity_registry.async_get_entity_id(
        "binary_sensor", DOMAIN, f"{DESKTOP_ID}_connected"
    )
    widget_entity_id = entity_registry.async_get_entity_id(
        "switch", DOMAIN, f"{DESKTOP_ID}_widget"
    )
    assert connected_entity_id is not None
    assert widget_entity_id is not None
    assert hass.states.get(connected_entity_id).state == STATE_OFF
    assert hass.states.get(widget_entity_id).state == STATE_UNAVAILABLE

    connection = FakeConnection()
    runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=connection,
        subscription_id=42,
        user_id="user-1",
        is_admin=False,
    )
    runtime.async_report_state(
        DESKTOP_ID,
        connection=connection,
        state={"visible": False, "current_page": "main"},
    )
    await hass.async_block_till_done()

    assert hass.states.get(connected_entity_id).state == STATE_ON
    assert hass.states.get(widget_entity_id).state == STATE_OFF

    device_registry = dr.async_get(hass)
    device = device_registry.async_get_device(identifiers={(DOMAIN, DESKTOP_ID)})
    assert device is not None

    service_task = hass.async_create_task(
        hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: [device.id]},
            blocking=True,
        ),
        "test show service",
    )
    await asyncio.sleep(0)
    command = connection.events[-1][1]
    runtime.async_acknowledge_command(
        DESKTOP_ID,
        connection=connection,
        command_id=command["command_id"],
        status="completed",
        error=None,
        state={"visible": True},
    )
    await service_task
    await hass.async_block_till_done()

    assert hass.states.get(widget_entity_id).state == STATE_ON


async def test_unload_marks_entry_not_loaded(hass: HomeAssistant) -> None:
    """The config entry unloads its runtime and all entity platforms cleanly."""
    entry = await _setup_entry(hass)

    assert await hass.config_entries.async_unload(entry.entry_id)
    await hass.async_block_till_done()
    assert not hasattr(entry, "runtime_data")

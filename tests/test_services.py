"""Tests for Home Assistant service actions and authorization boundaries."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from homeassistant.const import ATTR_DEVICE_ID
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.ha_desktop_widget.const import (
    ATTR_PAGE_ID,
    CONFIG_ENTRY_UNIQUE_ID,
    DOMAIN,
    SERVICE_SHOW,
    SERVICE_SWITCH_PAGE,
)
from custom_components.ha_desktop_widget.runtime import HADesktopWidgetRuntime
from custom_components.ha_desktop_widget.services import async_setup_services

DESKTOP_ID = "desktop-12345678"


class FakeConnection:
    """Capture commands dispatched by a Home Assistant service call."""

    def __init__(self) -> None:
        self.events: list[tuple[int, dict[str, Any]]] = []

    def send_event(self, subscription_id: int, event: dict[str, Any]) -> None:
        self.events.append((subscription_id, event))


async def _setup_desktop(
    hass: HomeAssistant, *, capabilities: list[str]
) -> tuple[MockConfigEntry, HADesktopWidgetRuntime, dr.DeviceEntry]:
    entry = MockConfigEntry(
        domain=DOMAIN,
        title="HA Desktop Widget",
        data={},
        unique_id=CONFIG_ENTRY_UNIQUE_ID,
    )
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    runtime: HADesktopWidgetRuntime = entry.runtime_data
    await runtime.async_register_desktop(
        {
            "desktop_id": DESKTOP_ID,
            "name": "Office",
            "protocol_version": 1,
            "capabilities": capabilities,
        },
        user_id="owner",
        is_admin=False,
    )
    await hass.async_block_till_done()
    device = dr.async_get(hass).async_get_device(identifiers={(DOMAIN, DESKTOP_ID)})
    assert device is not None
    return entry, runtime, device


async def test_switch_page_service_payload_and_acknowledgement(hass: HomeAssistant) -> None:
    """The page action sends a bounded typed payload and waits for its acknowledgement."""
    _, runtime, device = await _setup_desktop(
        hass, capabilities=["visibility", "switch_page"]
    )
    connection = FakeConnection()
    runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=connection,
        subscription_id=42,
        user_id="owner",
        is_admin=False,
    )

    call_task = hass.async_create_task(
        hass.services.async_call(
            DOMAIN,
            SERVICE_SWITCH_PAGE,
            {ATTR_DEVICE_ID: device.id, ATTR_PAGE_ID: "weather"},
            blocking=True,
        ),
        "test switch page service",
    )
    await asyncio.sleep(0)
    command = connection.events[-1][1]
    assert command["action"] == "switch_page"
    assert command["payload"] == {ATTR_PAGE_ID: "weather"}
    runtime.async_acknowledge_command(
        DESKTOP_ID,
        connection=connection,
        command_id=command["command_id"],
        status="completed",
        error=None,
        state={"current_page": "weather"},
    )
    await call_task


async def test_service_errors_and_admin_boundary(
    hass: HomeAssistant, hass_read_only_user: Any
) -> None:
    """Services reject bad targets, missing capabilities, offline clients, and non-admins."""
    entry, runtime, device = await _setup_desktop(hass, capabilities=["visibility"])

    with pytest.raises(HomeAssistantError, match="Administrator permission"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: device.id},
            blocking=True,
            context=Context(user_id=hass_read_only_user.id),
        )

    with pytest.raises(HomeAssistantError, match="offline"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: device.id},
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="not supported"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SWITCH_PAGE,
            {ATTR_DEVICE_ID: device.id, ATTR_PAGE_ID: "weather"},
            blocking=True,
        )

    with pytest.raises(HomeAssistantError, match="was not found"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: "missing-device"},
            blocking=True,
        )

    foreign = dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        identifiers={("other_domain", "foreign")},
    )
    with pytest.raises(HomeAssistantError, match="not an HA Desktop Widget"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: foreign.id},
            blocking=True,
        )

    assert await hass.config_entries.async_unload(entry.entry_id)
    with pytest.raises(HomeAssistantError, match="not loaded"):
        await hass.services.async_call(
            DOMAIN,
            SERVICE_SHOW,
            {ATTR_DEVICE_ID: device.id},
            blocking=True,
        )

    async_setup_services(hass)
    assert runtime.get_desktop(DESKTOP_ID) is not None

"""Tests for desktop sessions and acknowledged commands."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.core import HomeAssistant

from custom_components.ha_desktop_widget.runtime import (
    DesktopCommandError,
    DesktopOwnershipError,
    DesktopUnavailableError,
    HADesktopWidgetRuntime,
)

DESKTOP_ID = "desktop-12345678"


class FakeConnection:
    """Capture subscription events without exposing a network transport."""

    def __init__(self) -> None:
        self.events: list[tuple[int, dict[str, Any]]] = []

    def send_event(self, subscription_id: int, event: dict[str, Any]) -> None:
        self.events.append((subscription_id, event))


async def _registered_runtime(hass: HomeAssistant) -> HADesktopWidgetRuntime:
    runtime = HADesktopWidgetRuntime(hass, "test-entry")
    await runtime.async_register_desktop(
        {
            "desktop_id": DESKTOP_ID,
            "name": "Office",
            "capabilities": ["visibility", "switch_page"],
            "protocol_version": 1,
        },
        user_id="user-1",
        is_admin=False,
    )
    return runtime


async def test_registration_enforces_first_user_ownership(hass: HomeAssistant) -> None:
    """Another non-admin HA user cannot claim an existing installation ID."""
    runtime = await _registered_runtime(hass)

    with pytest.raises(DesktopOwnershipError):
        await runtime.async_register_desktop(
            {
                "desktop_id": DESKTOP_ID,
                "name": "Impostor",
                "capabilities": [],
                "protocol_version": 1,
            },
            user_id="user-2",
            is_admin=False,
        )


async def test_command_requires_active_session(hass: HomeAssistant) -> None:
    """Offline commands fail instead of entering a stale replay queue."""
    runtime = await _registered_runtime(hass)

    with pytest.raises(DesktopUnavailableError, match="offline"):
        await runtime.async_dispatch_command(DESKTOP_ID, "show")


async def test_command_round_trip_updates_state(hass: HomeAssistant) -> None:
    """A command completes only after the active desktop acknowledges it."""
    runtime = await _registered_runtime(hass)
    connection = FakeConnection()
    unsubscribe = runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=connection,
        subscription_id=42,
        user_id="user-1",
        is_admin=False,
    )

    command_task = hass.async_create_task(
        runtime.async_dispatch_command(DESKTOP_ID, "show"), "test show command"
    )
    await asyncio.sleep(0)

    subscription_id, event = connection.events[-1]
    assert subscription_id == 42
    assert event["action"] == "show"
    assert event["payload"] == {}

    runtime.async_acknowledge_command(
        DESKTOP_ID,
        connection=connection,
        command_id=event["command_id"],
        status="completed",
        error=None,
        state={"visible": True, "current_page": "office"},
    )

    acknowledgement = await command_task
    assert acknowledgement["status"] == "completed"
    assert runtime.get_desktop(DESKTOP_ID).visible is True
    assert runtime.get_desktop(DESKTOP_ID).current_page == "office"

    unsubscribe()
    assert not runtime.is_online(DESKTOP_ID)
    await runtime.async_shutdown()


async def test_wrong_connection_cannot_report_state(hass: HomeAssistant) -> None:
    """A user's second socket cannot impersonate the subscribed desktop session."""
    runtime = await _registered_runtime(hass)
    subscribed = FakeConnection()
    runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=subscribed,
        subscription_id=42,
        user_id="user-1",
        is_admin=False,
    )

    with pytest.raises(DesktopUnavailableError, match="active session"):
        runtime.async_report_state(
            DESKTOP_ID,
            connection=FakeConnection(),
            state={"visible": True},
        )
    await runtime.async_shutdown()


async def test_load_tolerates_invalid_records_and_restores_valid_one(
    hass: HomeAssistant,
) -> None:
    """Corrupt collection entries are skipped without discarding valid desktops."""
    runtime = HADesktopWidgetRuntime(hass, "test-entry")
    stored = {
        "desktops": {
            1: {},
            "not-a-record": "invalid",
            "invalid": {"desktop_id": ""},
            DESKTOP_ID: {
                "name": "Restored",
                "owner_user_id": "user-1",
                "protocol_version": 1,
                "capabilities": ["visibility"],
            },
        }
    }
    with patch.object(runtime.store, "async_load", AsyncMock(return_value=stored)):
        await runtime.async_load()

    assert list(runtime.desktops) == [DESKTOP_ID]
    assert runtime.get_desktop(DESKTOP_ID).name == "Restored"

    invalid_collection = HADesktopWidgetRuntime(hass, "other-entry")
    with patch.object(
        invalid_collection.store,
        "async_load",
        AsyncMock(return_value={"desktops": []}),
    ):
        await invalid_collection.async_load()
    assert invalid_collection.desktops == {}


async def test_listener_debounce_and_session_replacement(hass: HomeAssistant) -> None:
    """Listeners can unsubscribe and one socket can own only one live desktop."""
    runtime = await _registered_runtime(hass)
    second_id = "desktop-87654321"
    await runtime.async_register_desktop(
        {
            "desktop_id": second_id,
            "name": "Laptop",
            "capabilities": ["visibility"],
            "protocol_version": 1,
        },
        user_id="user-1",
        is_admin=False,
    )
    changes: list[bool] = []
    remove_listener = runtime.async_add_listener(lambda: changes.append(True))
    first_connection = FakeConnection()
    runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=first_connection,
        subscription_id=1,
        user_id="user-1",
        is_admin=True,
    )
    runtime.async_subscribe_commands(
        second_id,
        connection=first_connection,
        subscription_id=2,
        user_id="user-1",
        is_admin=True,
    )
    assert not runtime.is_online(DESKTOP_ID)

    second_connection = FakeConnection()
    runtime.async_subscribe_commands(
        second_id,
        connection=second_connection,
        subscription_id=3,
        user_id="user-1",
        is_admin=True,
    )
    assert runtime.sessions[second_id].connection is second_connection
    assert changes
    remove_listener()

    runtime.async_schedule_save()
    await runtime.async_save()
    await runtime.async_shutdown()


async def test_failed_timeout_and_disconnected_commands(hass: HomeAssistant) -> None:
    """Rejected, timed-out, and interrupted commands all fail deterministically."""
    runtime = await _registered_runtime(hass)
    connection = FakeConnection()
    unsubscribe = runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=connection,
        subscription_id=42,
        user_id="user-1",
        is_admin=False,
    )

    failed_task = hass.async_create_task(
        runtime.async_dispatch_command(DESKTOP_ID, "show", {"source": "test"}),
        "test failed command",
    )
    await asyncio.sleep(0)
    failed_command = connection.events[-1][1]
    runtime.async_acknowledge_command(
        DESKTOP_ID,
        connection=connection,
        command_id=failed_command["command_id"],
        status="failed",
        error="desktop refused",
        state=None,
    )
    with pytest.raises(DesktopCommandError, match="desktop refused"):
        await failed_task

    runtime.async_acknowledge_command(
        DESKTOP_ID,
        connection=connection,
        command_id="unknown-command",
        status="completed",
        error=None,
        state=None,
    )

    with patch(
        "custom_components.ha_desktop_widget.runtime.COMMAND_TIMEOUT_SECONDS",
        0.001,
    ):
        with pytest.raises(DesktopCommandError, match="did not acknowledge"):
            await runtime.async_dispatch_command(DESKTOP_ID, "hide")

    interrupted = hass.async_create_task(
        runtime.async_dispatch_command(DESKTOP_ID, "toggle"),
        "test interrupted command",
    )
    await asyncio.sleep(0)
    unsubscribe()
    with pytest.raises(DesktopUnavailableError, match="disconnected"):
        await interrupted
    await runtime.async_shutdown()


async def test_remove_desktop(hass: HomeAssistant) -> None:
    """Removing a registered desktop is durable and idempotent."""
    runtime = await _registered_runtime(hass)
    connection = FakeConnection()
    runtime.async_subscribe_commands(
        DESKTOP_ID,
        connection=connection,
        subscription_id=42,
        user_id="user-1",
        is_admin=False,
    )

    assert await runtime.async_remove_desktop(DESKTOP_ID)
    assert not await runtime.async_remove_desktop(DESKTOP_ID)
    assert runtime.get_desktop(DESKTOP_ID) is None
